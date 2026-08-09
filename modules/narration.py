"""
Passo 2 do pipeline: narração via Cartesia.

Gera a narração beat a beat (não o roteiro inteiro de uma vez), porque a Cartesia
devolve timestamps por palavra relativos a cada chamada — gerar por beat evita ter
que realinhar texto→palavra depois e dá o tempo de fala real de cada beat "de
graça" (soma das durações), que é o dado que o composition_builder precisa para
calcular a duração de cada clipe de footage.

A Cartesia retorna timestamps de palavra nativamente via WebSocket com
`add_timestamps: true` (confirmado na documentação oficial) — por isso este
módulo não depende do Whisper. Ver modules/captions.py para o fallback.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
import wave
from pathlib import Path
from typing import Callable

import websocket
from pydub import AudioSegment

from modules.config import cache_dir, load_config, output_dir
from modules.script_parser import Beat

CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"


class CartesiaError(RuntimeError):
    pass


def _write_wav(pcm_bytes: bytes, path: Path, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # pcm_s16le
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


MIN_SPEED = 0.6
MAX_SPEED = 1.5


def _synthesize_via_cartesia(
    text: str,
    cfg: dict,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
) -> tuple[bytes, dict]:
    """Chama a Cartesia por WebSocket para um único trecho de texto.

    Retorna (pcm_bytes, word_timestamps) onde word_timestamps tem
    {"words": [...], "start": [...], "end": [...]} em segundos, relativos ao
    início deste áudio.
    """
    api_key = os.environ.get("CARTESIA_API_KEY")
    if not api_key:
        raise CartesiaError(
            "CARTESIA_API_KEY não definida no .env. Copie .env.example para .env "
            "e preencha a chave (veja https://play.cartesia.ai/keys)."
        )
    narration_cfg = cfg["narration"]
    resolved_voice_id = voice_id or narration_cfg.get("voice_id")
    if not resolved_voice_id:
        raise CartesiaError(
            "Nenhum voice_id informado (nem em config.yaml, nem passado explicitamente). "
            "Escolha uma voz em https://play.cartesia.ai/voices e cole o ID lá, ou selecione "
            "uma voz no painel web."
        )
    resolved_speed = speed if speed is not None else narration_cfg.get("speed", 1.0)
    # generation_config.speed é o parâmetro atual da Cartesia (o antigo campo
    # top-level "speed" com slow/normal/fast está deprecated). Clampa em vez
    # de deixar a API rejeitar um valor vindo de fora da faixa (ex: slider do
    # painel web com passo arredondado).
    resolved_speed = max(MIN_SPEED, min(MAX_SPEED, resolved_speed))

    url = f"{CARTESIA_WS_URL}?cartesia_version={narration_cfg['cartesia_api_version']}"
    ws = websocket.create_connection(url, header=[f"X-API-Key: {api_key}"])
    try:
        request = {
            "model_id": narration_cfg["model_id"],
            "transcript": text,
            "voice": {"mode": "id", "id": resolved_voice_id},
            "language": language or narration_cfg["language"],
            "context_id": str(uuid.uuid4()),
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": narration_cfg["sample_rate"],
            },
            "add_timestamps": True,
            "continue": False,
            "generation_config": {"speed": resolved_speed},
        }
        ws.send(json.dumps(request))

        audio_chunks: list[bytes] = []
        words: list[str] = []
        starts: list[float] = []
        ends: list[float] = []

        while True:
            raw = ws.recv()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "error":
                raise CartesiaError(
                    f"Cartesia retornou erro ({msg.get('error_code')}): {msg.get('message')}"
                )
            if msg_type == "chunk":
                audio_chunks.append(base64.b64decode(msg["data"]))
            elif msg_type == "timestamps":
                wt = msg.get("word_timestamps", {})
                words.extend(wt.get("words", []))
                starts.extend(wt.get("start", []))
                ends.extend(wt.get("end", []))
            elif msg_type == "done":
                break
    finally:
        ws.close()

    pcm_bytes = b"".join(audio_chunks)
    word_timestamps = {"words": words, "start": starts, "end": ends}
    return pcm_bytes, word_timestamps


def synthesize_beat(
    beat: Beat,
    slug: str,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
    force: bool = False,
) -> dict:
    """Sintetiza (ou reaproveita do cache) o áudio de um único beat.

    `voice_id`/`language`/`speed`, se passados, sobrepõem o que está em
    config.yaml (usado pelo painel web, onde são escolhidos por rascunho, não
    fixos no config). O CLI não passa nada e continua usando o config.yaml de
    sempre.

    `force=True` ignora o cache e sintetiza de novo mesmo se já existir
    (usado pelo botão "Regenerar" do painel web, quando o usuário não gostou
    do resultado de um bloco específico — inclusive depois de ajustar a
    velocidade, já que o cache não é invalidado automaticamente por isso).

    Retorna {"audio_path": Path, "duration_seconds": float,
    "captions": [{"word", "start_seconds", "end_seconds"}]} com timestamps
    relativos ao início deste beat (deslocamento para a timeline global é feito
    em build_narration).
    """
    cfg = load_config()
    beat_dir = cache_dir("narration", slug)
    wav_path = beat_dir / f"beat_{beat.id:03d}.wav"
    meta_path = beat_dir / f"beat_{beat.id:03d}.json"

    if not force and wav_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {"audio_path": wav_path, **meta}

    pcm_bytes, word_timestamps = _synthesize_via_cartesia(
        beat.text, cfg, voice_id=voice_id, language=language, speed=speed
    )
    sample_rate = cfg["narration"]["sample_rate"]
    _write_wav(pcm_bytes, wav_path, sample_rate)

    duration_seconds = len(pcm_bytes) / 2 / sample_rate  # 2 bytes/amostra, mono
    captions = [
        {"word": w, "start_seconds": s, "end_seconds": e}
        for w, s, e in zip(
            word_timestamps["words"], word_timestamps["start"], word_timestamps["end"]
        )
    ]
    meta = {"duration_seconds": duration_seconds, "captions": captions}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"audio_path": wav_path, **meta}


def build_narration(
    beats: list[Beat],
    slug: str,
    on_beat_done: Callable[[int], None] | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float | None = None,
) -> dict:
    """Sintetiza todos os beats e concatena em um único WAV final.

    Retorna {"audio_path", "duration_seconds", "beats": [{id, start_seconds,
    end_seconds, captions}]} pronto para alimentar o composition_builder.

    `on_beat_done`, se passado, é chamado com o id de cada beat assim que sua
    narração termina de ser sintetizada (usado pelo painel web para mostrar
    progresso ao vivo; o CLI não passa nada e o comportamento não muda).
    `voice_id`/`language`/`speed` sobrepõem o config.yaml (ver synthesize_beat).
    """
    cfg = load_config()
    silence_ms = cfg["narration"]["silence_between_beats_ms"]
    silence = AudioSegment.silent(duration=silence_ms)

    final_audio = AudioSegment.empty()
    beats_timing = []
    cursor_seconds = 0.0

    for beat in beats:
        result = synthesize_beat(beat, slug, voice_id=voice_id, language=language, speed=speed)
        segment = AudioSegment.from_wav(result["audio_path"])

        start_seconds = cursor_seconds
        end_seconds = start_seconds + result["duration_seconds"]
        captions = [
            {
                "word": c["word"],
                "start_seconds": start_seconds + c["start_seconds"],
                "end_seconds": start_seconds + c["end_seconds"],
            }
            for c in result["captions"]
        ]
        beats_timing.append(
            {
                "id": beat.id,
                "text": beat.text,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "captions": captions,
            }
        )

        final_audio += segment + silence
        cursor_seconds = end_seconds + (silence_ms / 1000.0)

        if on_beat_done is not None:
            on_beat_done(beat.id)

    out_dir = output_dir(slug)
    audio_path = out_dir / "narration.wav"
    final_audio.export(audio_path, format="wav")

    return {
        "audio_path": audio_path,
        "duration_seconds": final_audio.duration_seconds,
        "beats": beats_timing,
    }


if __name__ == "__main__":
    import sys

    from modules.script_parser import parse_script

    if len(sys.argv) != 2:
        print("Uso: python -m modules.narration <caminho-do-roteiro>")
        sys.exit(1)

    script_beats = parse_script(sys.argv[1])
    script_slug = Path(sys.argv[1]).stem
    narration = build_narration(script_beats, script_slug)
    print(f"Narração gerada: {narration['audio_path']} ({narration['duration_seconds']:.1f}s)")
