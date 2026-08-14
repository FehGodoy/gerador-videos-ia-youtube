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


def _image_media_type(data: bytes) -> str:
    """Tipo real da miniatura, pelos bytes iniciais.

    Antes ia tudo como image/jpeg fixo. Funcionou enquanto só existia
    Pexels/Pixabay, mas o Wikimedia serve PNG também e a API recusa a
    requisição inteira com 400 quando o tipo declarado não bate — derrubando o
    ranking de todos os candidatos daquela cena, não só o PNG.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _download_thumbnail(url: str) -> bytes | None:
    try:
        # Sem User-Agent próprio a Wikimedia responde 403 — e candidato sem
        # miniatura é descartado pelo ranker, o que faria o Commons nunca ser
        # escolhido.
        from modules.footage_search import WIKIMEDIA_USER_AGENT

        resp = requests.get(
            url, timeout=THUMBNAIL_TIMEOUT, headers={"User-Agent": WIKIMEDIA_USER_AGENT}
        )
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        logger.warning("Falha ao baixar thumbnail para ranking: %s", url)
        return None


def _ask_claude(context: str, images: list[bytes], media_types: list[str]) -> tuple[int | None, str]:
    import anthropic

    cfg = load_config()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    kinds = ", ".join(
        f"{i}={'foto' if mt == 'image' else 'vídeo'}" for i, mt in enumerate(media_types)
    )

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "Você está escolhendo footage de stock para um vídeo documentário.\n"
                f"{context}\n\n"
                f"Abaixo estão {len(images)} miniaturas de candidatos, numeradas a partir de 0 na "
                "ordem que aparecem. Escolha a que melhor representa visualmente essa cena — o "
                "assunto, a ação, o contexto.\n"
                f"Tipo de cada candidato: {kinds}.\n"
                "Decida em dois passos.\n"
                "1) A cena pede um assunto IDENTIFICÁVEL — um modelo de veículo, uma pessoa "
                "real, um objeto histórico, um lugar com nome próprio? Se sim, FIDELIDADE VENCE "
                "TUDO: escolha o candidato que mostra aquilo de verdade, mesmo sendo foto. Um "
                "vídeo bonito de um carro genérico está ERRADO quando a cena pede um modelo "
                "específico. E não afirme que um candidato é o assunto exato se você não "
                "consegue confirmar isso pela miniatura — se for só parecido, diga que é "
                "aproximado no seu motivo.\n"
                "2) Se a cena pede algo GENÉRICO (uma profissão, uma ação comum, um ambiente), "
                "aí sim PREFIRA VÍDEO: foto parada quebra o ritmo do documentário. Só pegue "
                "foto se nenhum vídeo representar bem a cena.\n"
                "Dê uma NOTA DE 0 A 100 pra cada candidato, considerando: bate com o assunto e "
                "com as entidades citadas, serve pro tipo de visual pedido, e tem qualidade de "
                "imagem aceitável.\n"
                "Calibre a nota assim, e seja RIGOROSO — nota alta em material que só lembra o "
                "assunto é pior que admitir que não achamos nada:\n"
                "  85-100 = mostra exatamente o que a cena pede\n"
                "  70-84  = serve bem, mesmo não sendo o assunto exato\n"
                "  50-69  = só tematicamente relacionado, passaria como tapa-buraco\n"
                "  0-49   = não representa a cena\n"
                "Responda APENAS com um JSON, sem markdown: "
                '{"scores": [{"index": 0, "score": 0-100, "reason": "frase curta"}], '
                '"best_index": N}'
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
                    "media_type": _image_media_type(img_bytes),
                    "data": base64.b64encode(img_bytes).decode("ascii"),
                },
            }
        )

    response = client.messages.create(
        model=cfg["keywords"]["model"],
        # uma nota + motivo curto por candidato; com 8 candidatos, ~400 tokens
        max_tokens=900,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    parsed = json.loads(raw)

    scores: dict[int, dict] = {}
    for item in parsed.get("scores") or []:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("score")
        if not isinstance(index, int) or not isinstance(score, (int, float)):
            continue
        scores[index] = {
            "score": max(0, min(100, int(score))),
            "reason": str(item.get("reason", "")).strip(),
        }

    best_index = parsed.get("best_index")
    # se o modelo esqueceu o best_index mas pontuou, usa a maior nota
    if not isinstance(best_index, int) and scores:
        best_index = max(scores, key=lambda i: scores[i]["score"])
    return best_index, scores


def rank_candidates(context: str, candidates: list[dict]) -> list[dict]:
    """Reordena `candidates` (melhor primeiro) usando visão do Claude pra
    comparar cada miniatura com o contexto da cena (trecho da narração + o
    que essa cena específica deve mostrar — ver footage_search). Se algo
    falhar (sem chave de API, erro de rede, resposta não parseável), devolve
    a lista na ordem original — o mesmo princípio de fallback já usado em
    keyword_extractor.py, o ranking nunca deve derrubar o pipeline.
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
        best_index, scores = _ask_claude(
            context, images, [c.get("media_type", "video") for c in valid_candidates]
        )
    except Exception:
        logger.exception("Ranking de footage por IA falhou, mantendo ordem original.")
        return candidates

    if best_index is None or not (0 <= best_index < len(valid_candidates)):
        return candidates

    # nota e motivo em TODO candidato avaliado, não só no vencedor: é o que
    # alimenta o threshold/fallback e o que a revisão mostra pra você decidir
    scored = []
    for i, candidate in enumerate(valid_candidates):
        info = scores.get(i, {})
        scored.append(
            {
                **candidate,
                "relevance_score": info.get("score"),
                "ai_reasoning": info.get("reason", ""),
            }
        )

    # ordena pela nota, mas o best_index do modelo tem a palavra final na 1ª
    # posição (ele pode preferir vídeo a foto num empate de nota)
    ranked = sorted(scored, key=lambda c: c.get("relevance_score") or 0, reverse=True)
    winner = scored[best_index]
    ranked = [winner] + [c for c in ranked if c is not winner]

    # candidatos sem thumbnail (não avaliados) vão pro fim, sem nota
    skipped = [
        {**c, "relevance_score": None} for c in candidates if c not in valid_candidates
    ]
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
