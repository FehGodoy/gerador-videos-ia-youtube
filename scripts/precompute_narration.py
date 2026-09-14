"""Gera a narração (áudio via Cartesia, sem tocar na Anthropic) de TODOS os
blocos de um roteiro, a partir de um bloco específico em diante -- usado
pra "adiantar" um rascunho que travou numa chamada de hints, sem precisar
rodar o pipeline inteiro de novo (que abortaria de novo no mesmo lugar).

Depois de rodar isso, use scripts/inject_hints.py show/inject pra escrever
os hints de cada bloco à mão, e por fim rode auto_generate_video.py de
novo com --slug (narração e hints batem no cache, pula direto pra imagem).

Uso:
    python scripts/precompute_narration.py <slug> <script_file> <voice_id> <language> \
        --from-block N [--speed 1.0]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auto_generate_video import (
    DEFAULT_BASE_URL,
    create_narration_block,
    ensure_server_running,
    log,
    split_script_into_blocks,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug")
    parser.add_argument("script_file")
    parser.add_argument("voice_id")
    parser.add_argument("language")
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    ensure_server_running(args.base_url)

    script_text = Path(args.script_file).read_text(encoding="utf-8")
    block_texts = split_script_into_blocks(script_text)
    log(f"Roteiro tem {len(block_texts)} bloco(s) no total.")

    for i in range(args.from_block, len(block_texts)):
        label = f"Bloco {i + 1}/{len(block_texts)}"
        log(f"{label}: gerando narração...")
        slots = create_narration_block(
            args.base_url, args.slug, i, block_texts[i], args.voice_id, args.language, args.speed
        )
        log(f"{label}: {len(slots)} trecho(s) narrado(s).")


if __name__ == "__main__":
    main()
