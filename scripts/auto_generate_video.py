"""
Dispara o pipeline automático inteiro (roteiro por IA -> blocos ->
narração -> prompts de imagem -> imagens via fal.ai -> vídeo, sem revisão
manual) direto de uma CLI, sem precisar abrir o navegador — pensado pra
ser chamado pela skill `.claude/skills/gerar-video/SKILL.md`.

Não duplica regra de negócio nenhuma: fala com o servidor local
(webapp/server.py) só por HTTP, na MESMA sequência de chamadas que
webapp/static/app.js já faz (autoGenerateBlockImages, enqueueImageGen,
maybeAutoRenderVideo, confirm-render automático em composition_ready) —
qualquer mudança futura no pipeline em si (create_job, etc.) vale pros
dois automaticamente, sem precisar espelhar de novo aqui.

Uso:
    python scripts/auto_generate_video.py --channel meucanal --voice-id VOICE_ID \
        --topic "a historia da Honda Civic" --target-minutes 15

    python scripts/auto_generate_video.py --channel meucanal --voice-id VOICE_ID \
        --transcript-file caminho/transcricao.txt --target-minutes 15

    # Roteiro já pronto, usado EXATAMENTE como está (sem passar pela IA
    # pra reescrever/adaptar nada) — pula POST /api/scripts/generate, só
    # fatia o arquivo em blocos (um parágrafo = um bloco, mesmo grão que
    # colar manualmente no painel) e segue pro resto do pipeline.
    python scripts/auto_generate_video.py --channel meucanal --voice-id VOICE_ID \
        --script-file caminho/roteiro_pronto.txt
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "http://127.0.0.1:8010"
_SERVER_START_TIMEOUT_SECONDS = 30
_SERVER_POLL_INTERVAL_SECONDS = 1


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def is_server_up(base_url: str) -> bool:
    try:
        resp = requests.get(base_url, timeout=2)
        return resp.status_code < 500
    except requests.RequestException:
        return False


def ensure_server_running(base_url: str) -> None:
    """Confirma que o servidor local responde — se não, sobe ele mesmo
    (webapp/server.py via uvicorn, mesmo comando de run.ps1, sem --reload
    porque isso é uma execução automatizada, não desenvolvimento manual).
    Não derruba o servidor no final: pode já existir uso manual em
    paralelo (o próprio painel aberto no navegador)."""
    if is_server_up(base_url):
        log(f"Servidor já está no ar em {base_url}.")
        return

    log(f"Servidor não respondeu em {base_url} — subindo agora...")
    python_exe = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        raise RuntimeError(f"Venv não encontrado em {python_exe}. Rode primeiro: python -m venv .venv")

    port = base_url.rsplit(":", 1)[-1]
    subprocess.Popen(
        [str(python_exe), "-m", "uvicorn", "webapp.server:app", "--host", "127.0.0.1", "--port", port],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if is_server_up(base_url):
            log("Servidor no ar.")
            return
        time.sleep(_SERVER_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"Servidor não respondeu em {base_url} depois de {_SERVER_START_TIMEOUT_SECONDS}s.")


def split_script_into_blocks(text: str) -> list[str]:
    """Fatia um roteiro JÁ PRONTO em blocos, um parágrafo por bloco (linha
    em branco separa um do outro) — mesmo grão que colar manualmente no
    painel (ver `#block-text`, "Cole um bloco por vez (um parágrafo, por
    exemplo)"). Usado só quando `--script-file` é passado — pula
    completamente a geração por IA (`generate_script`/POST
    /api/scripts/generate), o texto do usuário nunca é reescrito."""
    import re

    paragraphs = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in paragraphs if p.strip()]


def generate_script(base_url: str, channel: str, language: str, topic: str | None, transcript: str | None, target_minutes: float) -> list[str]:
    log("Gerando roteiro...")
    resp = requests.post(
        f"{base_url}/api/scripts/generate",
        json={
            "channel": channel,
            "language": language,
            "topic": topic,
            "transcript": transcript,
            "target_minutes": target_minutes,
        },
        timeout=300,
    )
    if not resp.ok:
        raise RuntimeError(f"Falha ao gerar roteiro: {resp.json().get('detail', resp.text)}")
    blocks = resp.json()["blocks"]
    log(f"Roteiro gerado: {len(blocks)} bloco(s).")
    return blocks


def create_narration_block(base_url: str, slug: str, block_id: int, text: str, voice_id: str, language: str, speed: float) -> list[dict]:
    resp = requests.post(
        f"{base_url}/api/narration-blocks",
        json={
            "slug": slug,
            "block_id": block_id,
            "text": text,
            "voice_id": voice_id,
            "language": language,
            "speed": speed,
            "auto_mode": True,
        },
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"Falha ao gerar narração do bloco {block_id}: {resp.json().get('detail', resp.text)}")
    return resp.json()["slots"]


def fetch_hints(base_url: str, slug: str, block_id: int, language: str, channel: str) -> list[dict]:
    resp = requests.post(
        f"{base_url}/api/narration-blocks/{slug}/{block_id}/hints",
        json={"language": language, "channel": channel},
        timeout=180,
    )
    if not resp.ok:
        raise RuntimeError(f"Falha ao gerar dica/prompt do bloco {block_id}: {resp.json().get('detail', resp.text)}")
    return resp.json()["slots"]


def _is_eligible_for_image_gen(slot: dict) -> bool:
    # auto_mode sempre cria efeito "padrão" (mídia única) — nunca precisa
    # checar limite de galeria aqui, diferente de collectEligibleImageGenSlots
    # no app.js, que cobre os dois casos.
    if slot.get("needs_media") is False:
        return False
    if not slot.get("image_prompt"):
        return False
    media = slot.get("media") or []
    return not media or not media[0]


def generate_block_images(base_url: str, slug: str, block_id: int, slots: list[dict], block_label: str) -> None:
    eligible = [s for s in slots if _is_eligible_for_image_gen(s)]
    for i, slot in enumerate(eligible):
        log(f"{block_label}: imagem {i + 1}/{len(eligible)} (trecho {slot['index'] + 1})...")
        resp = requests.post(
            f"{base_url}/api/timeline/{slug}/{block_id}/{slot['index']}/generate-image",
            timeout=120,
        )
        if not resp.ok:
            # mesmo princípio de tolerância a erro parcial do app.js — um
            # trecho falhar não aborta o resto do bloco/rascunho.
            detail = resp.json().get("detail", resp.text)
            log(f"AVISO: falha ao gerar imagem do trecho {slot['index'] + 1}: {detail} — continuando.")


def create_job(base_url: str, slug: str, blocks: list[dict], voice_id: str, language: str, speed: float, remote_render: bool, channel: str) -> str:
    log("Criando job de renderização...")
    resp = requests.post(
        f"{base_url}/api/jobs",
        json={
            "slug": slug,
            "blocks": blocks,
            "voice_id": voice_id,
            "language": language,
            "speed": speed,
            "remote_render": remote_render,
            "media_mode": "own_media",
            "channel": channel,
        },
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"Falha ao criar job: {resp.json().get('detail', resp.text)}")
    return resp.json()["job_id"]


def _iter_sse_events(resp: requests.Response):
    """Parser mínimo de Server-Sent Events (linhas `event:`/`data:`,
    evento em branco separa um do outro) — sem dependência nova, é só
    texto por cima de uma resposta HTTP em streaming."""
    event_type = None
    data_lines: list[str] = []
    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip("\r")
        if line == "":
            if event_type is not None:
                yield event_type, "\n".join(data_lines)
            event_type = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())


def stream_job_events(base_url: str, job_id: str) -> str:
    """Acompanha o job até terminar — confirma a renderização sozinho
    assim que a composição fica pronta (mesmo comportamento automático
    que webapp/static/app.js::startJobEvents já tem em composition_ready,
    sem parar pra revisão manual). Devolve a URL do vídeo pronto ou
    levanta RuntimeError com o erro real."""
    confirmed = False
    with requests.get(f"{base_url}/api/jobs/{job_id}/events", stream=True, timeout=None) as resp:
        resp.raise_for_status()
        for event_type, data in _iter_sse_events(resp):
            if not data:
                continue
            payload = json.loads(data) if data.startswith("{") else {}

            if event_type == "composition_ready" and not confirmed:
                confirmed = True
                log("Composição pronta — confirmando renderização automaticamente (sem revisão)...")
                confirm_resp = requests.post(f"{base_url}/api/jobs/{job_id}/confirm-render", timeout=30)
                if not confirm_resp.ok:
                    raise RuntimeError(f"Falha ao confirmar renderização: {confirm_resp.json().get('detail', confirm_resp.text)}")

            elif event_type == "render_progress":
                frame, total = payload.get("frame", 0), payload.get("total", 0)
                log(f"Render: {frame}/{total} frames")

            elif event_type == "render_status":
                log(f"Render: {payload.get('message', '')}")

            elif event_type == "job_done":
                return payload["video_url"]

            elif event_type == "job_error":
                raise RuntimeError(f"Falha ao gerar o vídeo: {payload.get('message', 'erro desconhecido')}")

    raise RuntimeError("Conexão com o servidor encerrada antes do vídeo terminar.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--language", default="pt")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--target-minutes", type=float, default=15.0)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--transcript-file", default=None)
    parser.add_argument(
        "--script-file", default=None,
        help="Roteiro JÁ PRONTO, usado exatamente como está (sem passar pela IA) — pula a geração "
             "de roteiro, só fatia o arquivo em blocos (um parágrafo = um bloco).",
    )
    render_group = parser.add_mutually_exclusive_group()
    render_group.add_argument("--remote-render", dest="remote_render", action="store_true", default=True)
    render_group.add_argument("--no-remote-render", dest="remote_render", action="store_false")
    args = parser.parse_args()

    sources_given = sum(bool(x) for x in (args.topic, args.transcript_file, args.script_file))
    if sources_given != 1:
        parser.error("Informe exatamente um dos três: --topic, --transcript-file OU --script-file.")

    transcript = None
    if args.transcript_file:
        transcript_path = Path(args.transcript_file)
        if not transcript_path.exists():
            parser.error(f"Arquivo de transcrição não encontrado: {transcript_path}")
        transcript = transcript_path.read_text(encoding="utf-8")

    script_path = None
    if args.script_file:
        script_path = Path(args.script_file)
        if not script_path.exists():
            parser.error(f"Arquivo de roteiro não encontrado: {script_path}")

    try:
        ensure_server_running(args.base_url)

        slug = f"cli-{uuid.uuid4().hex[:10]}"
        log(f"Rascunho: {slug}")

        if script_path:
            log("Usando roteiro já pronto (sem passar pela IA) — só fatiando em blocos...")
            block_texts = split_script_into_blocks(script_path.read_text(encoding="utf-8"))
            log(f"Roteiro fatiado em {len(block_texts)} bloco(s).")
        else:
            block_texts = generate_script(
                args.base_url, args.channel, args.language, args.topic, transcript, args.target_minutes
            )

        blocks_for_job = []
        for i, text in enumerate(block_texts):
            label = f"Bloco {i + 1}/{len(block_texts)}"
            log(f"{label}: gerando narração...")
            slots = create_narration_block(args.base_url, slug, i, text, args.voice_id, args.language, args.speed)
            log(f"{label}: gerando tradução/dica/prompt de imagem...")
            slots = fetch_hints(args.base_url, slug, i, args.language, args.channel)
            generate_block_images(args.base_url, slug, i, slots, label)
            blocks_for_job.append({"id": i, "text": text})

        job_id = create_job(
            args.base_url, slug, blocks_for_job, args.voice_id, args.language, args.speed,
            args.remote_render, args.channel,
        )
        video_url = stream_job_events(args.base_url, job_id)
        log(f"Vídeo pronto: {video_url}")
        return 0
    except Exception as e:
        log(f"ERRO: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
