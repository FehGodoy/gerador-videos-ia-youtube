"""
Passo 4 do pipeline: busca de footage no Pexels/Pixabay + ranking por IA
(modules/footage_ranker.py) antes de escolher qual baixar — evita pegar
cegamente o primeiro resultado da busca por palavra-chave, que às vezes nem
bate visualmente com o que está sendo narrado (ex: busca por "Honda HR-V"
sem achar o modelo exato e trazendo outro carro qualquer).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import requests

from modules.config import PROJECT_ROOT, cache_dir, load_config

logger = logging.getLogger(__name__)

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_SEARCH_URL = "https://api.pexels.com/v1/search"
PIXABAY_VIDEO_SEARCH_URL = "https://pixabay.com/api/videos/"
PIXABAY_PHOTO_SEARCH_URL = "https://pixabay.com/api/"

MAX_CANDIDATES_TO_RANK = 8


class FootageNotFound(RuntimeError):
    pass


def _search_pexels_videos(term: str, cfg: dict) -> list[dict]:
    import os

    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PEXELS_VIDEO_SEARCH_URL,
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
        candidates.append(
            {
                "source": "pexels",
                "media_type": "video",
                "url": chosen["link"],
                "thumbnail_url": video.get("image"),
                "duration": video["duration"],
            }
        )
    return candidates


def _search_pexels_photos(term: str, cfg: dict) -> list[dict]:
    import os

    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PEXELS_PHOTO_SEARCH_URL,
        headers={"Authorization": api_key},
        params={
            "query": term,
            "orientation": cfg["footage"]["orientation"],
            "per_page": cfg["footage"]["candidates_per_beat"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    candidates = []
    for photo in resp.json().get("photos", []):
        src = photo.get("src", {})
        url = src.get("large2x") or src.get("original")
        if not url:
            continue
        candidates.append(
            {
                "source": "pexels",
                "media_type": "image",
                "url": url,
                "thumbnail_url": src.get("medium") or src.get("small"),
                "duration": None,
            }
        )
    return candidates


def _search_pixabay_videos(term: str, cfg: dict) -> list[dict]:
    import os

    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PIXABAY_VIDEO_SEARCH_URL,
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
        candidates.append(
            {
                "source": "pixabay",
                "media_type": "video",
                "url": best["url"],
                "thumbnail_url": best.get("thumbnail"),
                "duration": duration,
            }
        )
    return candidates


def _search_pixabay_photos(term: str, cfg: dict) -> list[dict]:
    import os

    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        PIXABAY_PHOTO_SEARCH_URL,
        params={
            "key": api_key,
            "q": term,
            "image_type": "photo",
            "per_page": max(cfg["footage"]["candidates_per_beat"], 3),  # mínimo exigido pela API
        },
        timeout=15,
    )
    resp.raise_for_status()
    candidates = []
    for hit in resp.json().get("hits", []):
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if not url:
            continue
        candidates.append(
            {
                "source": "pixabay",
                "media_type": "image",
                "url": url,
                "thumbnail_url": hit.get("previewURL") or hit.get("webformatURL"),
                "duration": None,
            }
        )
    return candidates


_SEARCH_FUNCS = {
    "pexels": [_search_pexels_videos, _search_pexels_photos],
    "pixabay": [_search_pixabay_videos, _search_pixabay_photos],
}


def search_candidates(search_terms: list[str]) -> list[dict]:
    """Busca candidatos (sem baixar) pro primeiro termo que trouxer algum
    resultado, combinando vídeo + foto de Pexels e Pixabay. Cada candidato tem
    {source, media_type, url, thumbnail_url, duration} — `duration` é None
    para fotos. A revisão manual no painel decide qual usar; a IA de ranking
    (footage_ranker.py) já compara os dois tipos pela miniatura."""
    cfg = load_config()
    for term in search_terms:
        combined: list[dict] = []
        for source_name in cfg["footage"]["sources"]:
            for search_func in _SEARCH_FUNCS[source_name]:
                try:
                    combined.extend(search_func(term, cfg))
                except requests.RequestException:
                    logger.exception(
                        "Busca de footage falhou em %s para o termo '%s'", source_name, term
                    )
        if combined:
            return combined[:MAX_CANDIDATES_TO_RANK]
    return []


def _candidate_cache_key(candidate: dict) -> str:
    # hash da URL do próprio candidato, não do termo de busca — trocar de
    # candidato num beat não invalida o cache de nenhum dos dois, e o mesmo
    # clipe usado em beats diferentes é baixado uma vez só.
    return hashlib.sha1(candidate["url"].encode("utf-8")).hexdigest()[:16]


def download_candidate(candidate: dict) -> str:
    """Baixa (ou reaproveita do cache) um candidato específico."""
    footage_cache = cache_dir("footage")
    cache_key = _candidate_cache_key(candidate)
    cached = list(footage_cache.glob(f"{cache_key}.*"))
    if cached:
        return str(cached[0])

    ext = "jpg" if candidate.get("media_type") == "image" else "mp4"
    dest = footage_cache / f"{cache_key}.{ext}"
    with requests.get(candidate["url"], stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return str(dest)


def _fallback_clip(cfg: dict) -> Path | None:
    fallback_dir = PROJECT_ROOT / cfg["paths"]["fallback_footage"]
    clips = sorted(fallback_dir.glob("*.mp4"))
    return clips[0] if clips else None


def _review_path(slug: str, beat_id: int) -> Path:
    return cache_dir("footage_candidates", slug) / f"beat_{beat_id:03d}.json"


def load_candidates_for_review(slug: str, beat_id: int) -> dict | None:
    """Lê a lista ranqueada + escolha atual salva pra este beat, se existir.
    Usado tanto pra reaproveitar (não regerar/re-ranquear à toa quando o beat
    já foi processado) quanto pela tela de revisão do painel web."""
    path = _review_path(slug, beat_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_candidates_for_review(slug: str, beat_id: int, candidates: list[dict], chosen_index: int) -> None:
    path = _review_path(slug, beat_id)
    path.write_text(
        json.dumps(
            {"candidates": candidates, "chosen_index": chosen_index}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def search_and_download_footage(
    beat_id: int, beat_text: str, search_terms: list[str], slug: str | None = None
) -> dict:
    """Busca, ranqueia por IA (modules/footage_ranker) e baixa o melhor
    candidato pro beat. Se `slug` for passado, reaproveita uma escolha já
    salva (evita rechamar a IA de ranking à toa numa regeneração) e grava a
    lista ranqueada completa pra revisão manual no painel web.

    Retorna {"clip_path": str, "source": str, "media_type": "video"|"image",
    "search_terms": [...]}. Se a busca não achar nada, usa um clipe genérico
    de assets/fallback/ em vez de quebrar o pipeline (sempre vídeo); se nem
    isso existir, "clip_path" vem None.
    """
    from modules.footage_ranker import rank_candidates

    cfg = load_config()

    if slug:
        cached_review = load_candidates_for_review(slug, beat_id)
        if cached_review is not None and cached_review["candidates"]:
            chosen = cached_review["candidates"][cached_review["chosen_index"]]
            try:
                clip_path = download_candidate(chosen)
                return {
                    "clip_path": clip_path,
                    "source": chosen["source"],
                    "media_type": chosen.get("media_type", "video"),
                    "search_terms": search_terms,
                }
            except requests.RequestException:
                logger.exception(
                    "Falha ao reaproveitar candidato salvo do beat %d, buscando de novo", beat_id
                )

    candidates = search_candidates(search_terms)

    if not candidates:
        logger.warning(
            "Nenhum footage encontrado para o beat %d (termos: %s). Usando fallback genérico.",
            beat_id,
            search_terms,
        )
        fallback = _fallback_clip(cfg)
        return {
            "clip_path": str(fallback) if fallback else None,
            "source": "fallback" if fallback else None,
            "media_type": "video",
            "search_terms": search_terms,
        }

    ranked = rank_candidates(beat_text, candidates)
    chosen = ranked[0]

    try:
        clip_path = download_candidate(chosen)
    except requests.RequestException:
        logger.exception("Download de footage falhou (beat %d, %s)", beat_id, chosen["source"])
        fallback = _fallback_clip(cfg)
        return {
            "clip_path": str(fallback) if fallback else None,
            "source": "fallback" if fallback else None,
            "media_type": "video",
            "search_terms": search_terms,
        }

    if slug:
        save_candidates_for_review(slug, beat_id, ranked, chosen_index=0)

    return {
        "clip_path": clip_path,
        "source": chosen["source"],
        "media_type": chosen.get("media_type", "video"),
        "search_terms": search_terms,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso: python -m modules.footage_search <texto-do-beat> <termo1> [termo2 ...]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    result = search_and_download_footage(0, sys.argv[1], sys.argv[2:])
    print(json.dumps(result, ensure_ascii=False, indent=2))
