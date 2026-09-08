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


_SECTION_BORDER_RE = __import__("re").compile(r"^[-=_*]{2,}.*[-=_*]{2,}$")


def _is_section_header(paragraph: str) -> bool:
    """Um parágrafo é marcador de seção se: (a) é uma linha só cercada por
    um mesmo tipo de borda repetida (`--- ... ---`, `=== ... ===`), ou (b)
    é INTEIRAMENTE maiúsculo (ignorando pontuação/espaço) — cobre título
    sem borda nenhuma. A checagem (a) foi adicionada depois de um roteiro
    de verdade usar `--- BLOCK 1: Gancho + Fundamento ---`, onde as
    palavras do título ("Gancho", "Fundamento") não são maiúsculas, então
    só a checagem (b) (usada sozinha antes) deixava esse marcador passar
    batido como narração de verdade."""
    if "\n" not in paragraph and _SECTION_BORDER_RE.match(paragraph):
        return True
    return paragraph.isupper()


def split_script_into_blocks(text: str) -> list[str]:
    """Fatia um roteiro JÁ PRONTO em blocos, um parágrafo por bloco (linha
    em branco separa um do outro) — mesmo grão que colar manualmente no
    painel (ver `#block-text`, "Cole um bloco por vez (um parágrafo, por
    exemplo)"). Usado só quando `--script-file` é passado — pula
    completamente a geração por IA (`generate_script`/POST
    /api/scripts/generate), o texto do usuário nunca é reescrito.

    Marcadores de seção (ver `_is_section_header`) nunca viram bloco de
    narração — bug real pego testando com um roteiro de verdade do
    usuário: sem esse filtro, esses marcadores viravam blocos de
    narração absurdos, sendo narrados/gerando imagem de verdade.

    Além disso, TUDO antes do primeiro marcador desse tipo também é
    tratado como preâmbulo (título, nome do canal) — mesmo quando o
    título em si não bate em `_is_section_header` sozinho. Segundo bug
    real pego testando com outro roteiro de verdade: um título em alemão
    ("Trockener Mund am Morgen: ...") capitaliza só os substantivos
    (regra do idioma), não a frase inteira — mas ainda assim não é
    narração, é só metadado do documento.
    """
    import re

    paragraphs = re.split(r"\n\s*\n", text.strip())
    clean = [p.strip() for p in paragraphs if p.strip()]

    header_indices = [i for i, p in enumerate(clean) if _is_section_header(p)]
    preamble_end = header_indices[0] if header_indices else 0
    header_set = set(header_indices)
    return [p for i, p in enumerate(clean) if i >= preamble_end and i not in header_set]


def group_script_into_blocks_by_section(text: str) -> list[str]:
    """Variante de `split_script_into_blocks` que agrupa todos os
    parágrafos de uma mesma seção nomeada (entre dois marcadores tipo
    "--- BLOCK N ---"/"=== BLOCK N ===") numa ÚNICA narração, em vez de
    um bloco por parágrafo — pedido do usuário quando o roteiro tem
    parágrafos curtos demais e vira narração fragmentada demais (menos
    chamadas de Cartesia/LLM; o número de IMAGENS geradas não muda —
    continua uma por trecho de ~5s dentro do bloco, independente de como
    ele foi agrupado). Preâmbulo (antes do 1º marcador) é descartado,
    mesma regra de `split_script_into_blocks`. Sem marcador nenhum no
    texto, cai pro comportamento de sempre (um parágrafo = um bloco) —
    não tem seção pra agrupar."""
    import re

    paragraphs = re.split(r"\n\s*\n", text.strip())
    clean = [p.strip() for p in paragraphs if p.strip()]

    header_indices = [i for i, p in enumerate(clean) if _is_section_header(p)]
    if not header_indices:
        return clean

    header_set = set(header_indices)
    groups: list[list[str]] = []
    current: list[str] = []
    for i, p in enumerate(clean):
        if i < header_indices[0]:
            continue  # preâmbulo
        if i in header_set:
            if current:
                groups.append(current)
            current = []
            continue
        current.append(p)
    if current:
        groups.append(current)

    return ["\n\n".join(g) for g in groups]


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


_HINTS_MAX_ATTEMPTS = 3
_HINTS_RETRY_DELAY_SECONDS = 5


def _hints_all_empty(slots: list[dict]) -> bool:
    # Mesmo critério do painel (webapp/static/app.js, botão "Gerar de
    # novo"): todo trecho sem NENHUM dos 3 campos de IA é sinal de que
    # generate_slot_hints esgotou as 3 tentativas dela e caiu no fallback
    # vazio (ver modules/timeline.py) — falha real, não é cache velho.
    return all(not s.get("translation_pt") and not s.get("hint") and not s.get("image_prompt") for s in slots)


def fetch_hints(base_url: str, slug: str, block_id: int, language: str, channel: str) -> list[dict]:
    """Bug real pego rodando contra um roteiro de verdade: um bloco cuja
    chamada de LLM falhou (fallback vazio) passava batido aqui e só
    quebrava bem mais tarde, em create_job, com uma mensagem genérica
    ("Faltam N trechos sem mídia") sem dizer QUAL bloco nem por quê —
    tarde demais pra saber o que aconteceu sem inspecionar o manifesto na
    mão. Agora detecta o fallback vazio e tenta de novo (mesmo efeito de
    clicar "Gerar de novo" no painel — cada chamada é independente, sem
    cache de falha) antes de desistir."""
    last_slots: list[dict] = []
    for attempt in range(_HINTS_MAX_ATTEMPTS):
        if attempt > 0:
            log(f"Bloco {block_id}: dica/prompt veio vazio (tentativa {attempt}/{_HINTS_MAX_ATTEMPTS}) — tentando de novo...")
            time.sleep(_HINTS_RETRY_DELAY_SECONDS)
        resp = requests.post(
            f"{base_url}/api/narration-blocks/{slug}/{block_id}/hints",
            json={"language": language, "channel": channel},
            timeout=180,
        )
        if not resp.ok:
            raise RuntimeError(f"Falha ao gerar dica/prompt do bloco {block_id}: {resp.json().get('detail', resp.text)}")
        last_slots = resp.json()["slots"]
        if not _hints_all_empty(last_slots):
            return last_slots
    raise RuntimeError(
        f"Bloco {block_id}: tradução/dica/prompt de imagem vieram vazios em {_HINTS_MAX_ATTEMPTS} "
        "tentativas — provável instabilidade da API de LLM. Abortando em vez de criar um job que "
        "certamente falharia por falta de mídia nesses trechos."
    )


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
             "de roteiro, só fatia o arquivo em blocos (um parágrafo = um bloco por padrão).",
    )
    parser.add_argument(
        "--group-by-section", action="store_true", default=False,
        help="Com --script-file: agrupa os parágrafos de cada seção nomeada (\"--- BLOCK N ---\") "
             "numa única narração, em vez de um bloco por parágrafo — menos chamadas de "
             "narração/dica; o número de imagens geradas não muda.",
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
            script_text = script_path.read_text(encoding="utf-8")
            if args.group_by_section:
                block_texts = group_script_into_blocks_by_section(script_text)
                log(f"Roteiro agrupado em {len(block_texts)} bloco(s) (um por seção nomeada).")
            else:
                block_texts = split_script_into_blocks(script_text)
                log(f"Roteiro fatiado em {len(block_texts)} bloco(s) (um por parágrafo).")
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
