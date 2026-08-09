"""
Passo 1 do pipeline: ingestão do roteiro.

Divide o roteiro em "beats" (frases ou pequenos parágrafos) preservando o texto
original de cada um, para uso posterior nas legendas e na narração.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

# Parágrafos com mais palavras que isso são subdivididos por frase, para que
# nenhum beat vire um trecho de narração longo demais para um único clipe.
MAX_WORDS_PER_BEAT = 40

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú0-9\"'])")
_HEADER_RE = re.compile(r"^\s*#+\s")


@dataclass
class Beat:
    id: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _split_paragraphs(raw_text: str) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", raw_text.strip())
    result = []
    for p in paragraphs:
        p = " ".join(line.strip() for line in p.splitlines() if line.strip())
        if not p or _HEADER_RE.match(p):
            continue
        result.append(p)
    return result


def _split_into_sentences(paragraph: str) -> list[str]:
    sentences = _SENTENCE_SPLIT_RE.split(paragraph)
    return [s.strip() for s in sentences if s.strip()]


def parse_script(script_path: str | Path) -> list[Beat]:
    """Lê um roteiro .txt/.md e retorna a lista de beats na ordem do texto."""
    path = Path(script_path)
    raw_text = path.read_text(encoding="utf-8")

    beats: list[Beat] = []
    for paragraph in _split_paragraphs(raw_text):
        if len(paragraph.split()) <= MAX_WORDS_PER_BEAT:
            beats.append(Beat(id=len(beats), text=paragraph))
            continue
        for sentence in _split_into_sentences(paragraph):
            beats.append(Beat(id=len(beats), text=sentence))

    return beats


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 2:
        print("Uso: python modules/script_parser.py <caminho-do-roteiro>")
        sys.exit(1)

    parsed = parse_script(sys.argv[1])
    print(json.dumps([b.to_dict() for b in parsed], ensure_ascii=False, indent=2))
    print(f"\n{len(parsed)} beats extraídos.", file=sys.stderr)
