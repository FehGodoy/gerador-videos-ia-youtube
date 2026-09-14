"""Injeta hints (tradução/dica/prompt de imagem por trecho) ESCRITOS À MÃO
no cache que modules/timeline.py::generate_slot_hints normalmente
preencheria chamando a API da Anthropic -- pedido do usuário pra gerar
vídeo sem depender de crédito separado da API (ver
modules/timeline.py::inject_slot_hints).

Fluxo:
    1. python scripts/inject_hints.py show <slug> <block_id>
       Mostra os trechos (slots) já narrados desse bloco, numerados, pra
       quem for escrever os hints (o Claude Code) ver o texto exato de
       cada um antes de escrever o image_prompt.

    2. Escreve um JSON com uma lista de hints, UM POR TRECHO, na mesma
       ordem, cada um no formato:
       {"translation_pt": "...", "hint": "...", "image_prompt": "...",
        "needs_media": true, "has_person": false}
       (sem reforço de idioma/estilo -- isso é aplicado automaticamente
       na injeção, mesmas regras de sempre).

    3. python scripts/inject_hints.py inject <slug> <block_id> <language>
           --hints-file caminho/hints.json [--channel "Nome do Canal"]
       Grava no cache. A próxima chamada normal do pipeline (CLI ou
       painel) pra esse bloco encontra o cache batendo e NUNCA chama a
       API -- segue direto pra gerar imagem.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import timeline as timeline_module
from webapp import channels as channels_module


def cmd_show(slug: str, block_id: int) -> None:
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        print(f"Bloco {block_id} do rascunho {slug} não existe (ainda não foi narrado).")
        return
    print(f"{len(manifest)} trecho(s) no bloco {block_id}:\n")
    for slot in manifest:
        print(f"{slot['index']}. {slot['text']}")


def cmd_inject(
    slug: str, block_id: int, language: str, hints_file: str, channel: str | None
) -> None:
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        raise SystemExit(f"Bloco {block_id} do rascunho {slug} não existe.")

    hints = json.loads(Path(hints_file).read_text(encoding="utf-8"))
    if len(hints) != len(manifest):
        raise SystemExit(
            f"hints-file tem {len(hints)} item(ns), bloco tem {len(manifest)} trecho(s) -- "
            "precisa ser 1 por 1, na mesma ordem."
        )

    identity = channels_module.get_identity(channel) if channel else {}
    beat_text = " ".join(s["text"] for s in manifest)

    saved = timeline_module.inject_slot_hints(
        manifest,
        beat_text,
        language,
        slug,
        block_id,
        hints,
        image_style=identity.get("image_style_prompt"),
        character_style=identity.get("character_style_prompt"),
        scene_first=bool(identity.get("scene_first_prompt_order")),
    )
    print(f"Injetado no cache: {len(saved)} hint(s) pro bloco {block_id} do rascunho {slug}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="Mostra os trechos já narrados de um bloco.")
    p_show.add_argument("slug")
    p_show.add_argument("block_id", type=int)

    p_inject = sub.add_parser("inject", help="Grava hints escritos à mão no cache.")
    p_inject.add_argument("slug")
    p_inject.add_argument("block_id", type=int)
    p_inject.add_argument("language")
    p_inject.add_argument("--hints-file", required=True)
    p_inject.add_argument("--channel", default=None)

    args = parser.parse_args()
    if args.cmd == "show":
        cmd_show(args.slug, args.block_id)
    elif args.cmd == "inject":
        cmd_inject(args.slug, args.block_id, args.language, args.hints_file, args.channel)


if __name__ == "__main__":
    main()
