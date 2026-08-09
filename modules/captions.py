"""
Passo 6 do pipeline: dados de legenda.

Os timestamps por palavra já vêm prontos de modules/narration.py (a Cartesia
retorna isso nativamente). Este módulo só garante que todo beat tem captions
antes de virar composition.json, com um fallback via faster-whisper para o
caso raro de a Cartesia não devolver timestamps para algum trecho.

A renderização visual da legenda (fonte, estilo, highlight de palavra) é
responsabilidade do <CaptionOverlay> no Remotion — este módulo só entrega dados.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_whisper_model = None  # carregado sob demanda, é pesado (só usado no fallback)


def transcribe_with_whisper(audio_path: Path, offset_seconds: float = 0.0) -> list[dict]:
    """Fallback: transcreve um trecho de áudio com faster-whisper para obter
    timestamps por palavra, usado só quando a Cartesia não retornou nenhum
    timestamp para o beat. `offset_seconds` desloca os tempos para a timeline
    global, do mesmo jeito que build_narration faz com os dados da Cartesia.
    """
    global _whisper_model
    from faster_whisper import WhisperModel

    if _whisper_model is None:
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")

    segments, _info = _whisper_model.transcribe(str(audio_path), word_timestamps=True)
    captions = []
    for segment in segments:
        for word in segment.words:
            captions.append(
                {
                    "word": word.word.strip(),
                    "start_seconds": offset_seconds + word.start,
                    "end_seconds": offset_seconds + word.end,
                }
            )
    return captions


def ensure_captions(beat_timing: dict, slug: str) -> list[dict]:
    """Garante que um beat (já com timing calculado por build_narration) tem
    captions preenchidas, usando o fallback do Whisper se a Cartesia não
    retornou nenhuma palavra para esse trecho."""
    if beat_timing["captions"]:
        return beat_timing["captions"]

    logger.warning(
        "Beat %d não tem timestamps da Cartesia — usando faster-whisper como fallback.",
        beat_timing["id"],
    )
    from modules.config import cache_dir

    beat_audio = cache_dir("narration", slug) / f"beat_{beat_timing['id']:03d}.wav"
    return transcribe_with_whisper(beat_audio, offset_seconds=beat_timing["start_seconds"])
