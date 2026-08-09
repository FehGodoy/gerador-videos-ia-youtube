"""
Ranking dos candidatos de footage por visão computacional (Claude). Em vez de
confiar cegamente na ordem de busca do Pexels/Pixabay, manda as miniaturas
dos candidatos + o texto do beat pra Anthropic escolher qual bate melhor
visualmente.

Limite importante: isso não garante marca/modelo exato (ex: "Honda HR-V" vs
qualquer outro SUV parecido, se o banco não tiver o modelo certo) — só
melhora a média de acerto temático/contextual (é mesmo um carro? é mesmo uma
cena de trânsito, e não uma oficina?). Pra garantia de 100%, ver a revisão
manual no painel web (webapp/server.py + static/app.js).
"""
from __future__ import annotations

import base64
import json
import logging
import os

import requests

from modules.config import load_config

logger = logging.getLogger(__name__)

THUMBNAIL_TIMEOUT = 10


def _download_thumbnail(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=THUMBNAIL_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        logger.warning("Falha ao baixar thumbnail para ranking: %s", url)
        return None


def _ask_claude(beat_text: str, images: list[bytes]) -> tuple[int | None, str]:
    import anthropic

    cfg = load_config()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "Você está escolhendo footage de stock para um vídeo documentário. "
                f'Trecho da narração: "{beat_text}"\n\n'
                f"Abaixo estão {len(images)} imagens (miniaturas de vídeos candidatos), "
                "numeradas a partir de 0 na ordem que aparecem. Escolha a que melhor "
                "representa visualmente esse trecho — o assunto, a ação, o contexto. "
                "Responda APENAS com um JSON, sem markdown: "
                '{"best_index": N, "reasoning": "motivo em uma frase curta"}'
            ),
        }
    ]
    for i, img_bytes in enumerate(images):
        content.append({"type": "text", "text": f"Imagem {i}:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(img_bytes).decode("ascii"),
                },
            }
        )

    response = client.messages.create(
        model=cfg["keywords"]["model"],
        max_tokens=200,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    parsed = json.loads(raw)
    return parsed.get("best_index"), parsed.get("reasoning", "")


def rank_candidates(beat_text: str, candidates: list[dict]) -> list[dict]:
    """Reordena `candidates` (melhor primeiro) usando visão do Claude pra
    comparar cada miniatura com o texto do beat. Se algo falhar (sem chave de
    API, erro de rede, resposta não parseável), devolve a lista na ordem
    original — o mesmo princípio de fallback já usado em keyword_extractor.py,
    o ranking nunca deve derrubar o pipeline.
    """
    if len(candidates) <= 1:
        return candidates

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return candidates

    images = []
    valid_candidates = []
    for candidate in candidates:
        thumb_url = candidate.get("thumbnail_url")
        if not thumb_url:
            continue
        data = _download_thumbnail(thumb_url)
        if data is None:
            continue
        images.append(data)
        valid_candidates.append(candidate)

    if len(valid_candidates) <= 1:
        return candidates

    try:
        best_index, reasoning = _ask_claude(beat_text, images)
    except Exception:
        logger.exception("Ranking de footage por IA falhou, mantendo ordem original.")
        return candidates

    if best_index is None or not (0 <= best_index < len(valid_candidates)):
        return candidates

    ranked = [valid_candidates[best_index]] + [
        c for i, c in enumerate(valid_candidates) if i != best_index
    ]
    ranked[0] = {**ranked[0], "ai_reasoning": reasoning}
    # candidatos sem thumbnail (não avaliados) vão pro fim, sem prioridade
    skipped = [c for c in candidates if c not in valid_candidates]
    return ranked + skipped


if __name__ == "__main__":
    import sys

    from modules.footage_search import search_candidates

    if len(sys.argv) < 3:
        print("Uso: python -m modules.footage_ranker <texto-do-beat> <termo1> [termo2 ...]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    found = search_candidates(sys.argv[2:])
    print(f"{len(found)} candidatos encontrados")
    ranked_result = rank_candidates(sys.argv[1], found)
    for i, c in enumerate(ranked_result):
        marker = " <== ESCOLHIDO" if i == 0 else ""
        print(f"{i}. [{c['source']}] {c['url']}{marker}")
        if "ai_reasoning" in c:
            print(f"   motivo: {c['ai_reasoning']}")
