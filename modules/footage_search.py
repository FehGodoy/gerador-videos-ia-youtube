"""
Passo 4 do pipeline (Fase 1: busca simples por keyword, sem reranking CLIP —
isso é trabalho da Fase 2).

Busca candidatos nas APIs gratuitas do Pexels e Pixabay Video usando os termos
do passo 3, baixa o primeiro candidato válido e mantém cache local em disco
para não rebaixar o mesmo material em execuções futuras.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import requests

from modules.config import PROJECT_ROOT, cache_dir, load_config

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"


class FootageNotFound(RuntimeError):
    pass


def _search_pexels(term: str, cfg: dict) -> list[dict]:
    import os

    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PEXELS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={
            "query": term,
            "orientation": cfg["footage"]["orientation"],
            "per_page": cfg["footage"]["candidates_per_beat"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    target_width = cfg["video"]["width"]
    candidates = []
    for video in resp.json().get("videos", []):
        if video.get("duration", 0) < cfg["footage"]["min_duration_seconds"]:
            continue
        files = video.get("video_files", [])
        if not files:
            continue
        # Pexels costuma oferecer variantes em 4K. Pegar sempre a maior parecia
        # razoável ("dá pra redimensionar pra baixo"), mas o Remotion decodifica
        # cada clipe via Chromium (<OffthreadVideo>) — decodificar 4K real chegou
        # a travar o render por completo num teste. Pega o menor arquivo que
        # ainda cobre a resolução de saída; só usa o maior disponível se nenhum
        # cobrir (melhor um upscale leve do que travar).
        suitable = [f for f in files if f.get("width", 0) >= target_width]
        chosen = min(suitable, key=lambda f: f["width"]) if suitable else max(
            files, key=lambda f: f.get("width", 0)
        )
        candidates.append({"source": "pexels", "url": chosen["link"], "duration": video["duration"]})
    return candidates


def _search_pixabay(term: str, cfg: dict) -> list[dict]:
    import os

    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PIXABAY_SEARCH_URL,
        params={
            "key": api_key,
            "q": term,
            "per_page": max(cfg["footage"]["candidates_per_beat"], 3),  # mínimo exigido pela API
        },
        timeout=15,
    )
    resp.raise_for_status()
    candidates = []
    for hit in resp.json().get("hits", []):
        duration = hit.get("duration", 0)
        if duration < cfg["footage"]["min_duration_seconds"]:
            continue
        videos = hit.get("videos", {})
        best = videos.get("large") or videos.get("medium") or videos.get("small")
        if not best:
            continue
        candidates.append({"source": "pixabay", "url": best["url"], "duration": duration})
    return candidates


_SEARCH_FUNCS = {"pexels": _search_pexels, "pixabay": _search_pixabay}


def _download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def _fallback_clip(cfg: dict) -> Path | None:
    fallback_dir = PROJECT_ROOT / cfg["paths"]["fallback_footage"]
    clips = sorted(fallback_dir.glob("*.mp4"))
    return clips[0] if clips else None


def search_and_download_footage(beat_id: int, search_terms: list[str]) -> dict:
    """Busca, baixa (ou reaproveita do cache) o footage para um beat.

    Retorna {"clip_path": str, "source": str, "search_terms": [...]}. Se todas
    as buscas falharem, loga o erro e usa um clipe genérico de
    assets/fallback/ em vez de quebrar o pipeline; se nem isso existir,
    "clip_path" vem como None (composition_builder decide o que fazer).
    """
    cfg = load_config()
    footage_cache = cache_dir("footage")

    for term in search_terms:
        cache_key = hashlib.sha1(term.encode("utf-8")).hexdigest()[:16]
        cached = list(footage_cache.glob(f"{cache_key}.*"))
        if cached:
            return {
                "clip_path": str(cached[0]),
                "source": "cache",
                "search_terms": search_terms,
            }

        for source_name in cfg["footage"]["sources"]:
            try:
                candidates = _SEARCH_FUNCS[source_name](term, cfg)
            except requests.RequestException:
                logger.exception("Busca de footage falhou em %s para o termo '%s'", source_name, term)
                continue
            if not candidates:
                continue

            chosen = candidates[0]
            dest = footage_cache / f"{cache_key}.mp4"
            try:
                _download(chosen["url"], dest)
            except requests.RequestException:
                logger.exception("Download de footage falhou (%s, termo '%s')", source_name, term)
                continue
            return {"clip_path": str(dest), "source": source_name, "search_terms": search_terms}

    logger.warning(
        "Nenhum footage encontrado para o beat %d (termos: %s). Usando fallback genérico.",
        beat_id,
        search_terms,
    )
    fallback = _fallback_clip(cfg)
    return {
        "clip_path": str(fallback) if fallback else None,
        "source": "fallback" if fallback else None,
        "search_terms": search_terms,
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Uso: python -m modules.footage_search <termo1> [termo2 ...]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    result = search_and_download_footage(0, sys.argv[1:])
    print(json.dumps(result, ensure_ascii=False, indent=2))
