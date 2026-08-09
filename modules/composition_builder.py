"""
Monta o composition.json final: junta beats + narração + footage + legendas,
valida contra composition.schema.json antes de salvar.

Fase 1: todo beat é do tipo "concreto" (footage). A classificação
concreto/estatístico e o campo "chart" são trabalho da Fase 2.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import jsonschema

from modules.captions import ensure_captions
from modules.config import PROJECT_ROOT, load_config, output_dir
from modules.footage_search import search_and_download_footage
from modules.keyword_extractor import extract_keywords
from modules.narration import build_narration
from modules.script_parser import parse_script

logger = logging.getLogger(__name__)

SCHEMA_PATH = PROJECT_ROOT / "composition.schema.json"

# (beat_id, stage, status) — stage: narration|keywords|footage|captions,
# status: running|done. Usado pelo painel web para progresso ao vivo; o CLI
# não passa nada e o comportamento de hoje não muda.
OnBeatProgress = Callable[[int, str, str], None]


def _path_str(p) -> str:
    """Caminho relativo à raiz do projeto (POSIX), não absoluto.

    O Remotion não aceita caminhos de arquivo absolutos locais como `src` de
    <Audio>/<OffthreadVideo> — só URLs http(s) ou staticFile(), que resolve
    relativo ao public dir (configurado em remotion.config.ts como a raiz
    deste projeto). Por isso todo caminho no composition.json precisa ser
    relativo à raiz, não absoluto.
    """
    return Path(p).resolve().relative_to(PROJECT_ROOT).as_posix()


def validate_composition(data: dict) -> None:
    """Valida um composition.json (ou dict equivalente) contra o schema.
    Levanta jsonschema.ValidationError se algo estiver fora do formato."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)


def build_composition(
    script_path: str,
    slug: str | None = None,
    on_beat_progress: OnBeatProgress | None = None,
    voice_id: str | None = None,
    language: str | None = None,
) -> dict:
    cfg = load_config()
    script_path = Path(script_path)
    slug = slug or script_path.stem

    beats = parse_script(script_path)

    on_narration_beat_done = None
    if on_beat_progress is not None:
        on_narration_beat_done = lambda beat_id: on_beat_progress(beat_id, "narration", "done")
    narration = build_narration(
        beats, slug, on_beat_done=on_narration_beat_done, voice_id=voice_id, language=language
    )

    beats_by_id = {b["id"]: b for b in narration["beats"]}

    composition_beats = []
    for beat in beats:
        beat_timing = beats_by_id[beat.id]

        if on_beat_progress is not None:
            on_beat_progress(beat.id, "keywords", "running")
        terms = extract_keywords(beat, slug)
        if on_beat_progress is not None:
            on_beat_progress(beat.id, "keywords", "done")
            on_beat_progress(beat.id, "footage", "running")
        footage = search_and_download_footage(beat.id, terms)
        if on_beat_progress is not None:
            on_beat_progress(beat.id, "footage", "done")
            on_beat_progress(beat.id, "captions", "running")
        captions = ensure_captions(beat_timing, slug)
        if on_beat_progress is not None:
            on_beat_progress(beat.id, "captions", "done")

        composition_beats.append(
            {
                "id": beat.id,
                "text": beat.text,
                "start_seconds": beat_timing["start_seconds"],
                "end_seconds": beat_timing["end_seconds"],
                "type": "concreto",
                "footage": {
                    "clip_path": _path_str(footage["clip_path"]) if footage["clip_path"] else None,
                    "source": footage["source"],
                    "search_terms": footage["search_terms"],
                }
                if footage["clip_path"]
                else None,
                "chart": None,
                "captions": captions,
            }
        )

    composition = {
        "fps": cfg["video"]["fps"],
        "width": cfg["video"]["width"],
        "height": cfg["video"]["height"],
        "audio": {
            "path": _path_str(narration["audio_path"]),
            "duration_seconds": narration["duration_seconds"],
        },
        "music": None,
        "beats": composition_beats,
    }

    validate_composition(composition)

    out_path = output_dir(slug) / "composition.json"
    out_path.write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("composition.json salvo em %s", out_path)

    return composition


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Uso: python -m modules.composition_builder <caminho-do-roteiro>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    build_composition(sys.argv[1])
