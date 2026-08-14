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
import re
from itertools import zip_longest
from pathlib import Path

import requests

from modules.config import PROJECT_ROOT, cache_dir, load_config

logger = logging.getLogger(__name__)

WIKIMEDIA_SEARCH_URL = "https://commons.wikimedia.org/w/api.php"
# Política da Wikimedia exige User-Agent identificável; requisição anônima
# genérica pode ser bloqueada.
WIKIMEDIA_USER_AGENT = (
    "gerador-videos-ia-youtube/1.0 (uso pessoal; https://github.com/FehGodoy/gerador-videos-ia-youtube)"
)
# Só licenças que permitem uso comercial com atribuição. O Commons também
# hospeda material "fair use", que NÃO pode ir pra um vídeo monetizado.
WIKIMEDIA_OK_LICENSES = ("cc0", "cc-by", "cc by", "pd", "public domain")
WIKIMEDIA_MIN_WIDTH = 1280
# Largura da miniatura mandada pra IA de ranking. Não dá pra derivar essa URL
# trocando a largura na URL de download: o upload.wikimedia.org só serve as
# larguras que ele mesmo gerou (320/400/640/800/1024 respondem HTTP 400 no
# mesmo arquivo em que 1280 e 1920 funcionam). Por isso a segunda chamada.
WIKIMEDIA_THUMB_WIDTH = 480

NASA_SEARCH_URL = "https://images-api.nasa.gov/search"

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


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


def _wikimedia_license_ok(license_short: str, license_id: str) -> bool:
    haystack = f"{license_short} {license_id}".lower()
    if "fair use" in haystack or "non-free" in haystack or "nonfree" in haystack:
        return False
    return any(ok in haystack for ok in WIKIMEDIA_OK_LICENSES)


def _search_wikimedia(term: str, cfg: dict) -> list[dict]:
    """Fotos do Wikimedia Commons. Sem chave de API.

    Existe pelo motivo que os bancos de stock não resolvem: assunto
    ESPECÍFICO. Buscar "Honda HR-V" no Pexels devolve um SUV qualquer; no
    Commons devolve a foto do modelo. Vale pra carro, evento histórico, pessoa
    pública, lugar nomeado.

    Filtra por licença: o Commons também hospeda material "fair use", que não
    pode ir num vídeo monetizado. Só passa o que permite uso comercial com
    atribuição — e a atribuição é carregada junto pra você poder creditar.
    """
    def query(params: dict) -> dict:
        resp = requests.get(
            WIKIMEDIA_SEARCH_URL,
            params={"action": "query", "format": "json", **params},
            headers={"User-Agent": WIKIMEDIA_USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("query", {}).get("pages", {})

    try:
        pages = query(
            {
                "generator": "search",
                "gsrsearch": term,
                "gsrnamespace": "6",  # namespace File:
                "gsrlimit": str(max(cfg["footage"]["candidates_per_beat"], 3)),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": str(cfg["video"]["width"]),
            }
        )
        # segunda chamada só pelas miniaturas de ranking (ver
        # WIKIMEDIA_THUMB_WIDTH): sem busca, só pelos pageids já encontrados
        thumbs: dict[str, str] = {}
        if pages:
            thumb_pages = query(
                {
                    "pageids": "|".join(str(p) for p in pages),
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": str(WIKIMEDIA_THUMB_WIDTH),
                }
            )
            for page_id, page in thumb_pages.items():
                url = (page.get("imageinfo") or [{}])[0].get("thumburl")
                if url:
                    thumbs[str(page_id)] = url
    except requests.RequestException:
        logger.exception("Busca no Wikimedia Commons falhou para o termo '%s'", term)
        return []

    candidates = []
    for page_id, page in pages.items():
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if (info.get("width") or 0) < WIKIMEDIA_MIN_WIDTH:
            continue

        meta = info.get("extmetadata", {})
        license_short = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        if not _wikimedia_license_ok(license_short, meta.get("License", {}).get("value", "")):
            continue

        # thumburl já vem na largura do vídeo — evita baixar o original, que
        # costuma passar de 5 MB e iria inteiro pro pacote do render remoto.
        download_url = info.get("thumburl") or info.get("url")
        thumb_url = thumbs.get(str(page_id))
        if not download_url or not thumb_url:
            continue

        candidates.append(
            {
                "source": "wikimedia",
                "media_type": "image",
                "url": download_url,
                "thumbnail_url": thumb_url,
                "duration": None,
                "attribution": {
                    "author": _strip_html(meta.get("Artist", {}).get("value", "")) or "desconhecido",
                    "license": license_short or "desconhecida",
                    "page": info.get("descriptionurl", ""),
                    "title": page.get("title", ""),
                },
            }
        )
    return candidates


def _search_nasa(term: str, cfg: dict) -> list[dict]:
    """Acervo de imagens da NASA. Sem chave, domínio público.

    Estreito mas insuperável no que cobre: espaço, aeronáutica, satélite,
    ciência, Terra vista de cima. As URLs vêm com a largura declarada no JSON
    — nada de derivar tamanho na mão (testei: trocar o sufixo do arquivo
    devolve sempre o mesmo bytes, então adivinhar não funcionaria).
    """
    try:
        resp = requests.get(
            NASA_SEARCH_URL,
            params={"q": term, "media_type": "image"},
            headers={"User-Agent": WIKIMEDIA_USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("Busca na NASA falhou para o termo '%s'", term)
        return []

    limite = max(cfg["footage"]["candidates_per_beat"], 3)
    candidates = []
    for item in resp.json().get("collection", {}).get("items", [])[:limite]:
        links = [l for l in (item.get("links") or []) if l.get("href")]
        if not links:
            continue
        maior = max(links, key=lambda l: l.get("width") or 0)
        if (maior.get("width") or 0) < WIKIMEDIA_MIN_WIDTH:
            continue
        dados = (item.get("data") or [{}])[0]
        candidates.append(
            {
                "source": "nasa",
                "media_type": "image",
                "url": maior["href"],
                "thumbnail_url": min(links, key=lambda l: l.get("width") or 10**9)["href"],
                "duration": None,
                "attribution": {
                    "author": dados.get("center") or "NASA",
                    "license": "Domínio público (NASA)",
                    "page": f"https://images.nasa.gov/details/{dados.get('nasa_id', '')}",
                    "title": dados.get("title") or "",
                },
            }
        )
    return candidates


_SEARCH_FUNCS = {
    "pexels": [_search_pexels_videos, _search_pexels_photos],
    "pixabay": [_search_pixabay_videos, _search_pixabay_photos],
    "wikimedia": [_search_wikimedia],
    "nasa": [_search_nasa],
}


def _sources_for(strategy: str, cfg: dict) -> list[str]:
    """Quais fontes consultar pra esta estratégia do diretor visual.

    Sem isso, todas as fontes disputam as mesmas 8 vagas de candidato e cada
    uma entra com uma sobra — o acervo documental fica com 1 vaga numa cena
    que é justamente sobre um fato específico. Roteando, a cena NEWS vê
    arquivo de verdade e a cena FOOTAGE vê vídeo de stock.
    """
    todas = cfg["footage"]["sources"]
    roteadas = (cfg["footage"].get("strategy_sources") or {}).get(strategy)
    if not roteadas:
        return todas
    # respeita o que está desligado em sources
    escolhidas = [s for s in roteadas if s in todas]
    return escolhidas or todas


def search_candidates(search_terms: list[str], strategy: str = "FOOTAGE") -> list[dict]:
    """Busca candidatos (sem baixar) pro primeiro termo que trouxer algum
    resultado, combinando vídeo + foto de Pexels e Pixabay. Cada candidato tem
    {source, media_type, url, thumbnail_url, duration} — `duration` é None
    para fotos. A revisão manual no painel decide qual usar; a IA de ranking
    (footage_ranker.py) já compara os dois tipos pela miniatura."""
    cfg = load_config()
    fontes = _sources_for(strategy, cfg)
    for term in search_terms:
        per_source: list[list[dict]] = []
        for source_name in fontes:
            for search_func in _SEARCH_FUNCS[source_name]:
                try:
                    found = search_func(term, cfg)
                except requests.RequestException:
                    logger.exception(
                        "Busca de footage falhou em %s para o termo '%s'", source_name, term
                    )
                    continue
                if found:
                    per_source.append(found)

        # Reveza entre as fontes em vez de concatenar e cortar. Concatenando, o
        # Pexels sozinho já enchia as 8 vagas e nenhuma fonte listada depois
        # dele chegava a ser avaliada — o Wikimedia entrou no config e não
        # aparecia em candidato nenhum.
        combined: list[dict] = []
        for row in zip_longest(*per_source):
            for candidate in row:
                if candidate is not None:
                    combined.append(candidate)
        if combined:
            return combined[:MAX_CANDIDATES_TO_RANK]
    return []


def _candidate_cache_key(candidate: dict) -> str:
    # hash da URL do próprio candidato, não do termo de busca — trocar de
    # candidato num beat não invalida o cache de nenhum dos dois, e o mesmo
    # clipe usado em beats diferentes é baixado uma vez só.
    return hashlib.sha1(candidate["url"].encode("utf-8")).hexdigest()[:16]


def candidate_clip_path(candidate: dict) -> str | None:
    """Caminho no cache que este candidato ocuparia, se já tiver sido baixado.

    Usado pela revisão no painel pra casar um candidato com as cenas do
    composition.json (que guardam clip_path, não a URL de origem) sem
    precisar baixar nada."""
    cached = list(cache_dir("footage").glob(f"{_candidate_cache_key(candidate)}.*"))
    return str(cached[0]) if cached else None


DOWNLOAD_ATTEMPTS = 3


def download_candidate(candidate: dict) -> str:
    """Baixa (ou reaproveita do cache) um candidato específico.

    Escreve num arquivo temporário e só renomeia pro nome definitivo quando o
    download termina inteiro: uma conexão que cai no meio (acontece com os
    CDNs de stock) deixava um arquivo truncado no cache, e como o cache é por
    hash da URL esse arquivo quebrado seria reaproveitado pra sempre —
    incluindo dentro do zip mandado pro render no GitHub.
    """
    footage_cache = cache_dir("footage")
    cache_key = _candidate_cache_key(candidate)
    cached = list(footage_cache.glob(f"{cache_key}.*"))
    if cached:
        return str(cached[0])

    ext = "jpg" if candidate.get("media_type") == "image" else "mp4"
    dest = footage_cache / f"{cache_key}.{ext}"
    partial = footage_cache / f"{cache_key}.{ext}.part"

    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            # User-Agent identificável em todo download: os servidores da
            # Wikimedia respondem 403 a requisição sem UA próprio, e mandar o
            # nosso não atrapalha os CDNs de Pexels/Pixabay.
            with requests.get(
                candidate["url"],
                stream=True,
                timeout=60,
                headers={"User-Agent": WIKIMEDIA_USER_AGENT},
            ) as resp:
                resp.raise_for_status()
                expected = int(resp.headers.get("Content-Length") or 0)
                written = 0
                with open(partial, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                        written += len(chunk)
            if expected and written < expected:
                raise requests.RequestException(
                    f"download incompleto: {written} de {expected} bytes"
                )
            partial.replace(dest)
            return str(dest)
        except requests.RequestException as e:
            last_error = e
            partial.unlink(missing_ok=True)
            logger.warning(
                "Download de footage falhou (tentativa %d/%d): %s", attempt, DOWNLOAD_ATTEMPTS, e
            )

    raise last_error if last_error else requests.RequestException("download falhou")


def _fallback_clip(cfg: dict) -> Path | None:
    fallback_dir = PROJECT_ROOT / cfg["paths"]["fallback_footage"]
    clips = sorted(fallback_dir.glob("*.mp4"))
    return clips[0] if clips else None


def _review_path(slug: str, beat_id: int, slot: int) -> Path:
    return cache_dir("footage_candidates", slug) / f"beat_{beat_id:03d}_shot_{slot:02d}.json"


def load_candidates_for_review(slug: str, beat_id: int, slot: int = 0) -> dict | None:
    """Lê a lista ranqueada + escolha atual salva pra este shot, se existir.
    Usado tanto pra reaproveitar (não regerar/re-ranquear à toa quando o beat
    já foi processado) quanto pela tela de revisão do painel web.

    `slot` é o índice do shot dentro do beat — um beat longo é preenchido por
    vários shots visualmente distintos, cada um com sua própria lista de
    candidatos e sua própria escolha."""
    path = _review_path(slug, beat_id, slot)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_candidates_for_review(
    slug: str, beat_id: int, candidates: list[dict], chosen_index: int, slot: int = 0
) -> None:
    path = _review_path(slug, beat_id, slot)
    path.write_text(
        json.dumps(
            {"candidates": candidates, "chosen_index": chosen_index}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def _fallback_result(cfg: dict, search_terms: list[str]) -> dict:
    fallback = _fallback_clip(cfg)
    return {
        "clip_path": str(fallback) if fallback else None,
        "source": "fallback" if fallback else None,
        "media_type": "video",
        # duração desconhecida (clipe local, não veio de uma API com metadados)
        # — o tiling de cenas trata None como "corte curto por segurança".
        "duration": None,
        "search_terms": search_terms,
    }


def _result_from_candidate(candidate: dict, clip_path: str, search_terms: list[str]) -> dict:
    result = {
        "clip_path": clip_path,
        "source": candidate["source"],
        "media_type": candidate.get("media_type", "video"),
        "duration": candidate.get("duration"),
        "search_terms": search_terms,
        "relevance_score": candidate.get("relevance_score"),
        "ai_reasoning": candidate.get("ai_reasoning", ""),
    }
    # Wikimedia é CC BY / CC BY-SA na maioria: creditar não é opcional. Carrega
    # a atribuição até o composition.json pra dar pra montar os créditos.
    if candidate.get("attribution"):
        result["attribution"] = candidate["attribution"]
    return result


def search_and_download_footage(
    beat_id: int,
    beat_text: str,
    search_terms: list[str],
    slug: str | None = None,
    slot: int = 0,
    strategy: str = "FOOTAGE",
    entities: list[str] | None = None,
) -> dict:
    """Busca, ranqueia por IA (modules/footage_ranker) e baixa o melhor
    candidato pra um shot do beat. Se `slug` for passado, reaproveita uma
    escolha já salva (evita rechamar a IA de ranking à toa numa regeneração)
    e grava a lista ranqueada completa pra revisão manual no painel web.

    `slot` identifica o shot dentro do beat (um beat longo tem vários).

    Retorna {"clip_path", "source", "media_type", "duration", "search_terms"}.
    `duration` é a duração do clipe em segundos (None pra imagem ou fallback)
    — usada pelo composition_builder pra nunca criar uma cena mais longa que
    o clipe, o que congelava o último frame por minutos. Se a busca não achar
    nada, usa um clipe genérico de assets/fallback/ em vez de quebrar o
    pipeline; se nem isso existir, "clip_path" vem None.
    """
    from modules.footage_ranker import rank_candidates

    cfg = load_config()

    if slug:
        cached_review = load_candidates_for_review(slug, beat_id, slot)
        if cached_review is not None and cached_review["candidates"]:
            chosen = cached_review["candidates"][cached_review["chosen_index"]]
            try:
                return _result_from_candidate(chosen, download_candidate(chosen), search_terms)
            except requests.RequestException:
                logger.exception(
                    "Falha ao reaproveitar candidato salvo (beat %d, shot %d), buscando de novo",
                    beat_id,
                    slot,
                )

    candidates = search_candidates(search_terms, strategy)

    if not candidates:
        logger.warning(
            "Nenhum footage encontrado (beat %d, shot %d, termos: %s). Usando fallback genérico.",
            beat_id,
            slot,
            search_terms,
        )
        return _fallback_result(cfg, search_terms)

    # contexto curto e focado no shot em vez do beat inteiro: um beat pode ter
    # centenas de palavras cobrindo vários assuntos, e mandar tudo faria a IA
    # de ranking julgar a miniatura contra o assunto errado (além de custar
    # tokens à toa).
    partes = [f'Trecho: "{beat_text[:300]}"']
    if entities:
        # entidades explícitas no contexto: é o que faz a IA cobrar o assunto
        # exato em vez de aceitar um parecido
        partes.append(f"Entidades citadas (o visual precisa bater com elas): {', '.join(entities)}")
    partes.append(f"Tipo de visual pedido: {strategy}")
    partes.append(f"Esta cena deve mostrar: {', '.join(search_terms)}")
    ranked = rank_candidates(" | ".join(partes), candidates)
    chosen = ranked[0]

    try:
        clip_path = download_candidate(chosen)
    except requests.RequestException:
        logger.exception(
            "Download de footage falhou (beat %d, shot %d, %s)", beat_id, slot, chosen["source"]
        )
        return _fallback_result(cfg, search_terms)

    if slug:
        save_candidates_for_review(slug, beat_id, ranked, chosen_index=0, slot=slot)

    return _result_from_candidate(chosen, clip_path, search_terms)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso: python -m modules.footage_search <texto-do-beat> <termo1> [termo2 ...]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    result = search_and_download_footage(0, sys.argv[1], sys.argv[2:])
    print(json.dumps(result, ensure_ascii=False, indent=2))
