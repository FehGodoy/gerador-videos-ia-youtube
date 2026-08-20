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
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

from modules.config import PROJECT_ROOT, cache_dir, load_config

logger = logging.getLogger(__name__)

WIKIMEDIA_SEARCH_URL = "https://commons.wikimedia.org/w/api.php"
# Política da Wikimedia exige User-Agent identificável; requisição anônima
# genérica pode ser bloqueada.
WIKIMEDIA_USER_AGENT = (
    "gerador-videos-ia-youtube/1.0 (uso pessoal; https://github.com/FehGodoy/gerador-videos-ia-youtube)"
)
# Só pro download de candidatos de sites de terceiro (google_images aponta pra
# qualquer site que o Google indexou, não uma CDN feita pra acesso
# automatizado como Wikimedia/Pexels/Pixabay). Mandar nosso UA identificável
# ali é o oposto de ajudar — muita proteção anti-hotlink barra exatamente por
# UA de script e ausência de Referer (a checagem mais comum: "esse pedido de
# imagem veio da própria página que a mostra, ou de qualquer lugar?"). Não é
# infalível — não passa por Cloudflare/fingerprint/captcha — mas resolve a
# proteção mais comum, que é só isso.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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

SERPER_IMAGES_URL = "https://google.serper.dev/images"
SERPER_ACCOUNT_URL = "https://google.serper.dev/account"
# Abaixo disso a imagem fica visivelmente borrada em tela cheia (o vídeo
# renderiza a 1920px de largura, e a imagem ainda leva um Ken Burns sutil —
# um zoom leve que pede folga a mais de resolução pra não pixelar). 640 era
# baixo demais pra isso; 1280 ainda aceita a maioria das fotos editoriais
# normais, só corta thumbnail pequena mesmo.
SERPER_MIN_WIDTH = 1280
# Aviso no painel abaixo disso — não é um corte automático, só um alerta pra
# trocar de chave antes de ficar sem crédito no meio de um vídeo.
SERPER_LOW_BALANCE_THRESHOLD = 100

# Instagram/Facebook servem imagem por um endpoint interno de preview que não
# existe pra visitante comum (confirmado testando: nem navegador com cookies
# reais resolve — não é proteção, é a porta simplesmente não estar aberta pro
# público). TikTok recusa com 403 mesmo pra navegador autenticado. Nenhum dos
# três algum dia funcionou nos testes desta sessão — bloquear aqui evita
# gastar ~10-30s abrindo navegador pra uma tentativa que sempre falha.
_BLOCKED_IMAGE_DOMAINS = ("instagram.com", "facebook.com", "fbsbx.com", "tiktok.com")


def _is_blocked_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith(f".{d}") for d in _BLOCKED_IMAGE_DOMAINS)

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
# Cota do YouTube Data API é diária e por projeto, não por chave: 10.000
# unidades, e cada search.list custa 100. Com as três chaves em contas
# separadas o teto do dia triplica. Guardamos aqui quais já estouraram pra não
# gastar uma chamada só pra tomar 403 de novo no mesmo processo.
_GOOGLE_QUOTA_EXHAUSTED: set[str] = set()
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}

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


# Mesmo parâmetro que o Google usa na própria busca (tbs=qdr:X), que o
# Serper repassa direto. Ajuda muito em conteúdo de notícia/atualidade
# (evita imagem antiga sem relação com o fato recente), mas ATRAPALHA roteiro
# sobre assunto histórico — filtraria fora justamente a foto de arquivo
# antiga que faria sentido ali. Por isso é opção em config.yaml
# (footage.google_images.recency), não fixo no código.
_RECENCY_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


def _search_google_images(term: str, cfg: dict) -> list[dict]:
    """Google Imagens via Serper.dev (SERPER_API_KEY no .env).

    A Custom Search JSON API oficial do Google fica bloqueada nas contas
    disponíveis (403 a nível de projeto, testado inclusive via curl puro).
    Serper contorna isso rodando a busca por trás e devolvendo o resultado já
    estruturado — mas a imagem em si continua vindo de qualquer site indexado
    pelo Google, sem filtro de licença nenhum (diferente de Wikimedia/NASA).
    Por decisão explícita do usuário, essa fonte fica na lista mesmo assim; o
    campo `license` no crédito reflete que o direito de uso NÃO foi checado.
    """
    from modules.settings import get_serper_api_key

    api_key = get_serper_api_key()
    if not api_key:
        return []

    body = {"q": term, "num": max(cfg["footage"]["candidates_per_beat"], 3)}
    recency = (cfg["footage"].get("google_images") or {}).get("recency")
    tbs = _RECENCY_TBS.get(recency)
    if tbs:
        body["tbs"] = tbs

    try:
        resp = requests.post(
            SERPER_IMAGES_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("Busca no Google Imagens (Serper) falhou para o termo '%s'", term)
        return []

    candidates = []
    for item in resp.json().get("images", []):
        url = item.get("imageUrl")
        if not url or (item.get("imageWidth") or 0) < SERPER_MIN_WIDTH:
            continue
        if _is_blocked_domain(url) or _is_blocked_domain(item.get("link") or ""):
            continue
        candidates.append(
            {
                "source": "google_images",
                "media_type": "image",
                "url": url,
                "thumbnail_url": item.get("thumbnailUrl") or url,
                "duration": None,
                "attribution": {
                    "author": item.get("source") or item.get("domain") or "",
                    "license": "Direitos não verificados (Google Imagens)",
                    "page": item.get("link") or "",
                    "title": item.get("title") or "",
                },
            }
        )
    return candidates


def get_serper_status() -> dict:
    """Saldo de créditos do Serper.dev pra mostrar no painel.

    `/account` é o endpoint de saldo real da conta — não confundir com o
    header `x-ratelimit-remaining` que a busca devolve, que é limite por
    minuto (25/min ali), não o saldo total. Devolve
    {"configured": bool, "balance": int|None, "low": bool}; `balance` fica
    None quando a chave não está configurada ou a checagem falhou.
    """
    from modules.settings import get_serper_api_key

    api_key = get_serper_api_key()
    if not api_key:
        return {"configured": False, "balance": None, "low": False}

    try:
        resp = requests.get(
            SERPER_ACCOUNT_URL, headers={"X-API-KEY": api_key}, timeout=10
        )
        resp.raise_for_status()
        saldo = resp.json().get("balance")
    except requests.RequestException:
        logger.exception("Não consegui checar o saldo do Serper")
        return {"configured": True, "balance": None, "low": False}

    return {
        "configured": True,
        "balance": saldo,
        "low": saldo is not None and saldo < SERPER_LOW_BALANCE_THRESHOLD,
    }


def _google_api_keys() -> list[str]:
    """Chaves do Google ainda utilizáveis, na ordem de preferência.

    GOOGLE_API_KEY_1/2/3 são de contas diferentes justamente pra somar cota;
    GOOGLE_API_KEY (sem sufixo) é aceita como forma de configuração única.
    """
    chaves = [os.environ.get(f"GOOGLE_API_KEY_{i}") for i in (1, 2, 3)]
    chaves.append(os.environ.get("GOOGLE_API_KEY"))
    vistas: list[str] = []
    for chave in chaves:
        if chave and chave not in vistas and chave not in _GOOGLE_QUOTA_EXHAUSTED:
            vistas.append(chave)
    return vistas


def _google_get(url: str, params: dict) -> dict | None:
    """GET numa API do Google trocando de chave quando a cota do dia acaba.

    Devolve None (em vez de levantar) quando nenhuma chave conseguiu responder
    — pra busca de footage, ficar sem cota é o mesmo que "esta fonte não achou
    nada": as outras fontes continuam e o shot cai no fallback se ninguém
    achar.
    """
    chaves = _google_api_keys()
    if not chaves:
        logger.warning("Nenhuma chave do Google disponível (cota esgotada ou não configurada)")
        return None

    for chave in chaves:
        try:
            resp = requests.get(url, params={**params, "key": chave}, timeout=20)
        except requests.RequestException:
            logger.exception("Chamada ao Google falhou (%s)", url)
            return None

        if resp.status_code == 200:
            return resp.json()

        erro = (resp.json().get("error") or {}) if resp.headers.get(
            "Content-Type", ""
        ).startswith("application/json") else {}
        motivos = {e.get("reason") for e in (erro.get("errors") or [])}
        if motivos & _QUOTA_REASONS:
            # cota diária: essa chave está queimada até virar o dia, não
            # adianta tentar de novo neste processo
            _GOOGLE_QUOTA_EXHAUSTED.add(chave)
            logger.warning("Cota diária do Google esgotada numa chave; tentando a próxima")
            continue
        if resp.status_code in (429, 500, 503) or "rateLimitExceeded" in motivos:
            # limite por segundo ou instabilidade: outra chave provavelmente
            # passa, mas esta continua válida amanhã e no resto da execução
            logger.warning("Google respondeu %s; tentando a próxima chave", resp.status_code)
            continue

        logger.error(
            "Google respondeu %s: %s", resp.status_code, erro.get("message") or resp.text[:200]
        )
        return None

    return None


def _parse_iso8601_duration(value: str) -> int | None:
    """PT1H2M3S -> segundos. Formato que o YouTube usa em contentDetails."""
    m = re.fullmatch(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return None
    horas, minutos, segundos = (int(g) if g else 0 for g in m.groups())
    return horas * 3600 + minutos * 60 + segundos


def _search_youtube(term: str, cfg: dict) -> list[dict]:
    """Vídeos do YouTube via Data API v3.

    Cobre o que acervo de stock não tem e foto não resolve: cena em movimento
    de um fato, um lugar ou um objeto específico. Em troca, exige um download
    por yt-dlp (nenhuma URL de arquivo direto vem da API) e traz uma questão de
    direitos que nem Pexels nem Wikimedia trazem — por isso o filtro de
    licença Creative Commons vem ligado por padrão em `youtube.creative_commons_only`.

    A duração NÃO vem no search.list; é preciso um segundo GET no videos.list.
    Esse segundo GET custa 1 unidade de cota contra as 100 do search, então
    vale pelos dois filtros que ele habilita (curto demais / longo demais).
    """
    yt = cfg["footage"].get("youtube") or {}
    params = {
        "q": term,
        "part": "snippet",
        "type": "video",
        "maxResults": max(cfg["footage"]["candidates_per_beat"], 3),
        "videoEmbeddable": "true",
        "safeSearch": "moderate",
    }
    if yt.get("creative_commons_only", True):
        params["videoLicense"] = "creativeCommon"
    if yt.get("prefer_hd", True):
        params["videoDefinition"] = "high"

    busca = _google_get(YOUTUBE_SEARCH_URL, params)
    if not busca:
        return []

    ids = [
        item["id"]["videoId"]
        for item in busca.get("items", [])
        if (item.get("id") or {}).get("videoId")
    ]
    if not ids:
        return []

    detalhes = _google_get(
        YOUTUBE_VIDEOS_URL, {"id": ",".join(ids), "part": "contentDetails,snippet"}
    )
    if not detalhes:
        return []

    trecho = float(yt.get("clip_seconds", 20))
    inicio = float(yt.get("start_offset_seconds", 10))
    max_origem = float(yt.get("max_source_duration_seconds", 1800))
    minimo = cfg["footage"]["min_duration_seconds"]

    candidates = []
    for item in detalhes.get("items", []):
        total = _parse_iso8601_duration((item.get("contentDetails") or {}).get("duration", ""))
        # sem duração é live/stream: o corte por tempo não tem como funcionar
        if not total or total > max_origem:
            continue
        # Pula a abertura, que costuma ser vinheta/logo em vez de imagem útil —
        # mas só quando sobra vídeo suficiente depois dela.
        comeco = inicio if total > inicio + trecho else 0.0
        disponivel = min(trecho, total - comeco)
        if disponivel < minimo:
            continue

        snippet = item.get("snippet") or {}
        thumbs = snippet.get("thumbnails") or {}
        melhor_thumb = max(
            thumbs.values(), key=lambda t: t.get("width") or 0, default={}
        ).get("url")
        if not melhor_thumb:
            continue

        vid = item["id"]
        candidates.append(
            {
                "source": "youtube",
                "media_type": "video",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumbnail_url": melhor_thumb,
                "youtube_video_id": vid,
                # duração do trecho que vamos baixar, não a do vídeo original:
                # o tiling de cenas usa isso pra nunca pedir um pedaço que não
                # existe no arquivo em disco.
                "duration": round(disponivel, 2),
                "youtube_segment": [round(comeco, 2), round(comeco + disponivel, 2)],
                "attribution": {
                    "author": snippet.get("channelTitle") or "",
                    "license": "Creative Commons (YouTube)"
                    if yt.get("creative_commons_only", True)
                    else "YouTube",
                    "page": f"https://www.youtube.com/watch?v={vid}",
                    "title": snippet.get("title") or "",
                },
            }
        )
    return candidates


_SEARCH_FUNCS = {
    "pexels": [_search_pexels_videos, _search_pexels_photos],
    "pixabay": [_search_pixabay_videos, _search_pixabay_photos],
    "wikimedia": [_search_wikimedia],
    "nasa": [_search_nasa],
    "youtube": [_search_youtube],
    "google_images": [_search_google_images],
}


# Prioridade GLOBAL escolhida pelo usuário: quando uma estratégia do diretor
# visual roteia pra mais de uma fonte, é nesta ordem que elas são tentadas.
# google_images vem primeiro mesmo custando crédito (Serper), YouTube exige
# baixar o vídeo inteiro na primeira vez então também não é grátis em tempo —
# a hierarquia prioriza achado específico sobre economia; quem fica de fora
# nem chega a ser chamado quando uma fonte anterior já resolveu (ver
# search_candidates).
SOURCE_PRIORITY = ("google_images", "youtube", "wikimedia", "pexels", "pixabay", "nasa")


def _sources_for(strategy: str, cfg: dict, allowed_sources: list[str] | None = None) -> list[str]:
    """Quais fontes consultar pra esta estratégia do diretor visual, na ordem
    da hierarquia global (SOURCE_PRIORITY).

    O roteamento por estratégia continua existindo — vídeo de stock genérico
    (FOOTAGE) não busca em fonte de foto, cena documental (NEWS) não busca em
    Pexels — só a ORDEM dentro de cada grupo é que segue a prioridade.

    `allowed_sources`, quando passado (o painel deixa escolher por vídeo),
    estreita ainda mais a lista do config.yaml — nunca a amplia, então uma
    fonte desligada em `footage.sources` continua desligada mesmo que o
    usuário a marque.
    """
    todas = cfg["footage"]["sources"]
    if allowed_sources is not None:
        todas = [s for s in todas if s in allowed_sources]
    roteadas = (cfg["footage"].get("strategy_sources") or {}).get(strategy)
    if not roteadas:
        base = todas
    else:
        # respeita o que está desligado em sources
        base = [s for s in roteadas if s in todas] or todas
    return sorted(
        base, key=lambda s: SOURCE_PRIORITY.index(s) if s in SOURCE_PRIORITY else len(SOURCE_PRIORITY)
    )


_SEARCH_RETRY_ATTEMPTS = 2
_SEARCH_RETRY_DELAY_SECONDS = 2.0


def _call_search_func(search_func, term: str, cfg: dict, source_name: str) -> list[dict]:
    """Chama uma função de busca com uma retentativa curta antes de desistir.

    Visto na prática: um blip de rede derrubava TODAS as fontes de um shot de
    uma vez (mesmo instante em que o status do Serper no topo do painel
    também mostrava "não consegui checar (rede)") — sem `assets/fallback/`
    ter algum clipe genérico configurado, isso vira card de texto repetido em
    vez de qualquer footage. Uma retentativa curta cobre o caso comum (blip
    passageiro) sem atrasar muito o caso raro (fonte realmente fora do ar)."""
    last_error: requests.RequestException | None = None
    for attempt in range(1, _SEARCH_RETRY_ATTEMPTS + 1):
        try:
            return search_func(term, cfg)
        except requests.RequestException as e:
            last_error = e
            if attempt < _SEARCH_RETRY_ATTEMPTS:
                time.sleep(_SEARCH_RETRY_DELAY_SECONDS)
    logger.warning(
        "Busca de footage falhou em %s para o termo '%s' após %d tentativa(s): %s",
        source_name, term, _SEARCH_RETRY_ATTEMPTS, last_error,
    )
    return []


def _search_source_batches(
    search_terms: list[str],
    strategy: str,
    cfg: dict,
    allowed_sources: list[str] | None = None,
):
    """Gera um lote de candidatos por (termo, fonte) tentada, na ordem da
    hierarquia de fontes (SOURCE_PRIORITY, filtrada pela estratégia e por
    `allowed_sources`, se passado — ver _sources_for). Só gera lotes
    não-vazios; quem consome decide se aceita esse lote ou pede o próximo
    (é o waterfall — mas quem decide parar agora é o chamador, não esta
    função, porque a decisão depende da nota do ranking, que só existe do
    lado de fora). Cada candidato tem
    {source, media_type, url, thumbnail_url, duration} — `duration` é None
    para foto."""
    fontes = _sources_for(strategy, cfg, allowed_sources)
    for term in search_terms:
        for source_name in fontes:
            found: list[dict] = []
            for search_func in _SEARCH_FUNCS[source_name]:
                found.extend(_call_search_func(search_func, term, cfg, source_name))
            if found:
                yield found[:MAX_CANDIDATES_TO_RANK]


def search_candidates(
    search_terms: list[str], strategy: str = "FOOTAGE", allowed_sources: list[str] | None = None
) -> list[dict]:
    """Busca candidatos (sem baixar): devolve só o PRIMEIRO lote não-vazio
    encontrado (mesma fonte/termo), sem considerar nota de ranking nenhuma —
    usado por quem só quer "algo", não o melhor entre várias fontes (CLI de
    debug do footage_ranker). search_and_download_footage (uso real do
    pipeline) usa _search_source_batches direto, pra poder continuar tentando
    fontes quando a primeira vem fraca."""
    cfg = load_config()
    return next(_search_source_batches(search_terms, strategy, cfg, allowed_sources), [])


def _candidate_cache_key(candidate: dict) -> str:
    # hash da URL do próprio candidato, não do termo de busca — trocar de
    # candidato num beat não invalida o cache de nenhum dos dois, e o mesmo
    # clipe usado em beats diferentes é baixado uma vez só.
    chave = candidate["url"]
    # No YouTube a URL identifica o vídeo inteiro, não o pedaço que baixamos:
    # sem o trecho na chave, mudar o corte no config reaproveitaria em silêncio
    # o arquivo antigo.
    segmento = candidate.get("youtube_segment")
    if segmento:
        chave = f"{chave}#{segmento[0]}-{segmento[1]}"
    return hashlib.sha1(chave.encode("utf-8")).hexdigest()[:16]


def candidate_clip_path(candidate: dict) -> str | None:
    """Caminho no cache que este candidato ocuparia, se já tiver sido baixado.

    Usado pela revisão no painel pra casar um candidato com as cenas do
    composition.json (que guardam clip_path, não a URL de origem) sem
    precisar baixar nada."""
    cached = list(cache_dir("footage").glob(f"{_candidate_cache_key(candidate)}.*"))
    return str(cached[0]) if cached else None


DOWNLOAD_ATTEMPTS = 3


def _looks_like_real_media(head: bytes, media_type: str) -> bool:
    """Detecta o caso visto na prática: a URL do candidato aponta pra um site
    de terceiro sem ser uma CDN confiável (o caso claro é Google Imagens —
    hotlink pra qualquer site indexado, ao contrário de Wikimedia/Pexels/
    Pixabay/NASA, que são APIs com link direto pro arquivo), e esse site
    devolve uma página de bloqueio/anti-hotlink em HTML com HTTP 200 no lugar
    do arquivo. O download "funciona" (Content-Length bate), mas o cache fica
    com uma imagem que nunca decodifica no Remotion — travava o render sem
    nenhum aviso antes disso.
    """
    inicio = head[:16].lstrip().lower()
    if inicio.startswith((b"<html", b"<!doc", b"<script", b"<body", b"<?xml")):
        return False
    if media_type != "image":
        return True  # vídeo: sem sniff barato de formato, só descarta o HTML acima
    return (
        head.startswith(b"\xff\xd8\xff")  # jpeg
        or head.startswith(b"\x89PNG\r\n\x1a\n")  # png
        or head.startswith(b"GIF8")  # gif
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )

# Sem login, o YouTube limita o download anônimo a 360p (medido: os formatos
# acima disso respondem 403 no arquivo, mesmo aparecendo na listagem). Com
# cookies de uma sessão logada, libera tudo — é a mesma técnica do projeto
# D:\Projetos\download-video-youtube (cookiesfrombrowser do yt-dlp). Tenta
# nesta ordem e usa o primeiro navegador cujo banco de cookies não esteja
# travado (Chrome/Edge recusam se o navegador estiver aberto).
YOUTUBE_COOKIE_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "vivaldi")
YOUTUBE_SOURCE_CACHE_SUBDIR = "footage_youtube_src"

# Descoberto uma vez por processo (evita retestar navegador travado a cada um
# dos ~50 shots do vídeo). None = ainda não tentou; {} = nenhum funcionou.
_youtube_cookie_option: dict | None = None


class _LoggerSilencioso:
    """yt_dlp.cookies.extract_cookies_from_browser exige um logger — sem um
    silencioso ele imprime aviso pra cada cookie que não consegue decifrar,
    o que é normal e não deveria poluir a saída."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _resolve_youtube_cookies() -> dict:
    global _youtube_cookie_option
    if _youtube_cookie_option is not None:
        return _youtube_cookie_option

    from yt_dlp.cookies import extract_cookies_from_browser

    logger_mudo = _LoggerSilencioso()
    for browser in YOUTUBE_COOKIE_BROWSERS:
        try:
            # Não exige cookie do domínio youtube.com: medido que mesmo um
            # perfil sem sessão logada no YouTube já é suficiente pra sair do
            # regime anônimo travado em 360p (o próprio ato de mandar QUALQUER
            # jar de cookies do navegador muda o comportamento). O que importa
            # aqui é só se o banco de cookies do navegador está acessível.
            extract_cookies_from_browser(browser, None, logger_mudo)
        except Exception:
            continue
        logger.info("YouTube: usando cookies do %s", browser)
        _youtube_cookie_option = {"cookiesfrombrowser": (browser,)}
        return _youtube_cookie_option

    logger.warning(
        "YouTube: nenhum navegador com cookies disponível (feche o Chrome/Edge "
        "se estiverem abertos); baixando sem login, limitado a 360p"
    )
    _youtube_cookie_option = {}
    return _youtube_cookie_option


def _download_youtube(candidate: dict, dest: Path) -> None:
    """Baixa o trecho escolhido do vídeo em `dest`.

    Baixa o vídeo fonte inteiro (autenticado, então em qualidade real) uma
    única vez por video-id em `cache/footage_youtube_src/`, e recorta o trecho
    localmente com ffmpeg (-c copy, sem recodificar — é cópia de bytes, não
    processamento, então é quase instantâneo). Tentei recortar direto na
    origem via `download_ranges` do yt-dlp; travava indefinidamente nos
    formatos acima de 360p porque eles vêm fragmentados (DASH) e o corte por
    seção precisa passar pelo ffmpeg como downloader externo, que não segue
    bem esse tipo de stream. Baixando completo e cortando depois, o problema
    some — e ainda dá pra reaproveitar a mesma fonte se dois shots diferentes
    pegarem o mesmo vídeo.

    Erros viram requests.RequestException porque é isso que os chamadores
    (search_and_download_footage) capturam pra cair no fallback; sem a
    conversão, uma falha de download derrubaria o pipeline inteiro.
    """
    try:
        import yt_dlp
    except ImportError as e:  # pragma: no cover
        raise requests.RequestException(f"yt-dlp não instalado: {e}") from e

    cfg = load_config()
    altura = (cfg["footage"].get("youtube") or {}).get("max_height", 1080)
    video_id = candidate.get("youtube_video_id") or hashlib.sha1(
        candidate["url"].encode("utf-8")
    ).hexdigest()[:16]

    fonte = cache_dir(YOUTUBE_SOURCE_CACHE_SUBDIR) / f"{video_id}.mp4"
    if not fonte.exists():
        parcial = fonte.with_suffix(".mp4.part")
        opts = {
            "format": f"bestvideo[height<={altura}][ext=mp4]/bestvideo[height<={altura}]/best[height<={altura}]",
            "merge_output_format": "mp4",
            "outtmpl": str(parcial),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "overwrites": True,
            **_resolve_youtube_cookies(),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([candidate["url"]])
        except Exception as e:  # yt_dlp levanta a própria hierarquia de erros
            parcial.unlink(missing_ok=True)
            raise requests.RequestException(f"yt-dlp falhou: {e}") from e
        if not parcial.exists() or parcial.stat().st_size == 0:
            raise requests.RequestException("yt-dlp terminou sem escrever o arquivo-fonte")
        parcial.replace(fonte)

    inicio, fim = candidate.get("youtube_segment") or [0, 20]
    resultado = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(inicio), "-to", str(fim),
            "-i", str(fonte),
            "-c", "copy", "-avoid_negative_ts", "make_zero",
            # dest chega como "<hash>.mp4.part" — a dupla extensão engana a
            # detecção automática de muxer do ffmpeg, que erra na segunda
            "-f", "mp4",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if resultado.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise requests.RequestException(f"corte local com ffmpeg falhou: {resultado.stderr[-300:]}")


def _download_headers(candidate: dict) -> dict:
    """User-Agent + Referer pro download do arquivo em si — não da busca.

    Wikimedia exige o UA identificável (política deles). Pras outras fontes
    (na prática, o caso que importa é google_images), finge ser um navegador
    de verdade: UA de Chrome comum + Referer pra página onde a imagem foi
    encontrada, exatamente o que falta pra passar pela proteção anti-hotlink
    mais comum."""
    if candidate.get("source") == "wikimedia":
        return {"User-Agent": WIKIMEDIA_USER_AGENT}
    headers = {"User-Agent": _BROWSER_USER_AGENT}
    referer = (candidate.get("attribution") or {}).get("page")
    if referer:
        headers["Referer"] = referer
    return headers


# Fontes cujo link direto (candidate["url"]) já costuma ser barrado por
# proteção anti-hotlink de terceiro — pra essas, abrir a página de origem num
# navegador de verdade (com os cookies do usuário) é tentado ANTES do
# download direto por requests, não depois: na prática, boa parte dos
# resultados do Google Imagens vem de rede social (Instagram/TikTok/Facebook)
# cujo endpoint de preview exige sessão de verdade, então o caminho rápido
# raramente funciona pra essas mesmo — vale pagar o navegador de cara.
_BROWSER_FIRST_SOURCES = ("google_images",)

# Cache de cookies convertidos pro formato do Playwright — mesmo raciocínio
# do cache de cookies do YouTube (_youtube_cookie_option): descoberto uma vez
# por processo, não a cada candidato.
_playwright_cookie_cache: list[dict] | None = None

# Tempo máximo esperando a resposta antes de desistir e cair pro download
# direto — generoso o bastante pra CDN lenta, sem travar o pipeline inteiro
# numa resposta que nunca chega.
_BROWSER_NAV_TIMEOUT_MS = 20_000


def _browser_cookies_for_playwright() -> list[dict]:
    """Cookies do navegador do usuário, convertidos pro formato que o
    Playwright espera — mesma extração já usada pro YouTube
    (_resolve_youtube_cookies: yt_dlp.cookies.extract_cookies_from_browser),
    só que aqui repassados pro Chromium controlado por código em vez de pro
    yt-dlp. Sem filtro de domínio de propósito: o Google Imagens pode apontar
    pra qualquer site, não dá pra saber de antemão qual cookie vai importar."""
    global _playwright_cookie_cache
    if _playwright_cookie_cache is not None:
        return _playwright_cookie_cache

    from yt_dlp.cookies import extract_cookies_from_browser

    logger_mudo = _LoggerSilencioso()
    resultado: list[dict] = []
    for browser in YOUTUBE_COOKIE_BROWSERS:
        try:
            jar = extract_cookies_from_browser(browser, None, logger_mudo)
        except Exception:
            continue
        for cookie in jar:
            if not cookie.domain or not cookie.name:
                continue
            resultado.append(
                {
                    "name": cookie.name,
                    "value": cookie.value or "",
                    "domain": cookie.domain,
                    "path": cookie.path or "/",
                    "secure": bool(cookie.secure),
                    "expires": float(cookie.expires) if cookie.expires else -1,
                }
            )
        if resultado:
            break
    _playwright_cookie_cache = resultado
    return resultado


def _download_via_browser(candidate: dict) -> bytes | None:
    """Baixa a mídia abrindo a página de origem (não a URL do arquivo) num
    Chromium real e VISÍVEL (uma janela abre na tela, faz a navegação
    sozinha e fecha — pedido explícito do usuário: preferir ver a janela
    acontecendo a deixar escondido), com os cookies do usuário carregados —
    em vez de pedir o arquivo direto (o que a proteção anti-hotlink barra),
    abre a página como um visitante de verdade: pede a URL do arquivo
    diretamente, mas com o Referer apontando pra página de origem e os
    cookies do usuário carregados no contexto — o mesmo par (Referer + sessão
    real) que a proteção anti-hotlink mais comum checa.

    Versão anterior abria a página de origem inteira e "escutava" a rede
    esperando uma resposta com URL EXATAMENTE igual à esperada — muitos sites
    redirecionam ou servem a imagem por uma URL levemente diferente (versão
    redimensionada, parâmetro de cache-busting), então a escuta nunca batia e
    falhava em silêncio, sem log nenhum. Pedir a URL final direto (com
    Referer/cookies certos) evita esse problema de casamento.

    None se não der pra tentar (sem URL, Playwright não instalado) ou se a
    resposta não vier OK dentro do timeout — quem chama cai pro download
    direto por requests nesse caso, não é fatal."""
    page_url = (candidate.get("attribution") or {}).get("page")
    target_url = candidate.get("url")
    if not target_url:
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    logger.info("Baixando via navegador (janela visível): %s", target_url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            try:
                context = browser.new_context(user_agent=_BROWSER_USER_AGENT)
                cookies = _browser_cookies_for_playwright()
                if cookies:
                    try:
                        context.add_cookies(cookies)
                    except Exception:
                        logger.warning("Playwright: cookies do navegador rejeitados, seguindo sem eles")
                page = context.new_page()
                response = page.goto(
                    target_url,
                    timeout=_BROWSER_NAV_TIMEOUT_MS,
                    referer=page_url or "",
                )
                if response is None or not response.ok:
                    status = response.status if response else "sem resposta"
                    logger.warning("Navegador: resposta não-OK (%s) pra %s", status, target_url)
                    return None
                try:
                    return response.body()
                except Exception as e:
                    # Visto na prática (Facebook): o goto segue um redirect e a
                    # página "navega pra longe" da resposta original antes da
                    # gente conseguir ler o corpo — não é uma falha real do
                    # nosso lado, só um caso que essa fonte não dá pra pegar
                    # assim. Aviso curto em vez do traceback inteiro.
                    logger.warning("Navegador: corpo da resposta indisponível pra %s (%s)", target_url, e)
                    return None
            finally:
                browser.close()
    except Exception:
        logger.exception("Download via navegador falhou (%s)", target_url)
        return None


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

    if candidate.get("source") == "youtube":
        # mesma disciplina do caminho por requests: escreve no .part e só
        # renomeia inteiro, pra um download interrompido não virar cache
        try:
            _download_youtube(candidate, partial)
        except requests.RequestException:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(dest)
        return str(dest)

    if candidate.get("source") in _BROWSER_FIRST_SOURCES:
        # Tentado ANTES do download direto por requests, não como último
        # recurso — pra essas fontes o link direto raramente funciona mesmo
        # (ver _BROWSER_FIRST_SOURCES), então vale pagar o navegador de cara
        # em vez de gastar as 3 tentativas rápidas que quase sempre falham.
        dados = _download_via_browser(candidate)
        if dados and _looks_like_real_media(dados[:512], candidate.get("media_type", "video")):
            partial.write_bytes(dados)
            partial.replace(dest)
            return str(dest)
        if dados:
            logger.warning(
                "Navegador baixou algo, mas não parece o formato esperado — "
                "caindo pro download direto"
            )

    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with requests.get(
                candidate["url"],
                stream=True,
                timeout=60,
                headers=_download_headers(candidate),
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
            with open(partial, "rb") as f:
                head = f.read(512)
            if not _looks_like_real_media(head, candidate.get("media_type", "video")):
                # Visto na prática com Google Imagens: a URL aponta pra um site
                # de terceiro (não uma CDN confiável como Wikimedia/Pexels), e
                # esse site devolve uma página de bloqueio/anti-hotlink em HTML
                # com HTTP 200 no lugar do arquivo. O download "funciona" (bate
                # o Content-Length), mas o cache fica com uma imagem que nunca
                # decodifica — travava o render sem nenhum aviso até agora.
                raise requests.RequestException(
                    "conteúdo baixado não parece o formato esperado "
                    "(provável bloqueio do site de origem, não da nossa API)"
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


def list_review_slots(slug: str, beat_id: int) -> list[int]:
    """Todos os slots (índices de shot) deste beat que têm candidatos
    salvos pra revisão, em ordem crescente.

    NÃO é uma sequência sem buracos: shots de estratégia TEXT/MOTION_GRAPHIC
    nunca buscam (nunca gravam arquivo) e um shot que não achou candidato
    NENHUM em nenhuma fonte cai direto no fallback sem gravar nada também —
    então um beat pode ter cache nos slots 1 e 3 sem ter no 0 e no 2. Quem
    assume "para no primeiro slot faltando" (como esta função substituiu)
    esconde da revisão qualquer shot depois do buraco, mesmo achado de
    verdade."""
    directory = cache_dir("footage_candidates", slug)
    prefix = f"beat_{beat_id:03d}_shot_"
    slots = []
    for path in directory.glob(f"{prefix}*.json"):
        try:
            slots.append(int(path.stem[len(prefix):]))
        except ValueError:
            continue
    return sorted(slots)


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


_MANUAL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _probe_video_duration(path: Path) -> float | None:
    """Duração em segundos de um arquivo de vídeo local, via ffprobe. None se
    não der pra medir (ffprobe ausente, arquivo corrompido) — o tiling de
    cenas já trata duração desconhecida como corte curto por segurança."""
    try:
        resultado = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return round(float(resultado.stdout.strip()), 2)
    except Exception:
        return None


def _generate_video_thumbnail(video_path: Path, thumb_path: Path) -> bool:
    """Miniatura pra mostrar na revisão — sem isso o card do upload manual
    fica sem imagem nenhuma pra reconhecer."""
    try:
        resultado = subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.5", "-i", str(video_path), "-vframes", "1", str(thumb_path)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return resultado.returncode == 0 and thumb_path.exists()
    except Exception:
        return False


def save_manual_upload(data: bytes, filename: str) -> dict:
    """Salva um arquivo enviado manualmente na revisão do painel como um
    candidato pronto pra uso — ao contrário dos candidatos de busca, este já
    está "baixado" (é o próprio arquivo enviado), sem precisar passar por
    download_candidate. Devolve um dict no mesmo formato de um candidato de
    busca ({source, media_type, url, thumbnail_url, duration, clip_path}),
    pronto pra entrar na lista salva por save_candidates_for_review.

    Existe pra cobrir os casos em que nenhuma fonte automática achou algo bom
    o bastante (shot virou card de texto) e o usuário prefere subir a própria
    mídia em vez de aceitar o card."""
    ext = Path(filename).suffix.lower() or ".mp4"
    media_type = "image" if ext in _MANUAL_IMAGE_EXTENSIONS else "video"

    dest_name = f"manual_{uuid.uuid4().hex[:16]}{ext}"
    dest = cache_dir("footage") / dest_name
    dest.write_bytes(data)

    duration = None
    thumb_name = dest_name
    if media_type == "video":
        duration = _probe_video_duration(dest)
        candidate_thumb = cache_dir("footage") / f"{dest.stem}_thumb.jpg"
        if _generate_video_thumbnail(dest, candidate_thumb):
            thumb_name = candidate_thumb.name

    return {
        "source": "manual",
        "media_type": media_type,
        "url": f"/footage_cache/{dest_name}",
        "thumbnail_url": f"/footage_cache/{thumb_name}",
        "duration": duration,
        "clip_path": str(dest),
    }


_YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def extract_youtube_id(url: str) -> str | None:
    """Tira o ID de 11 caracteres de qualquer formato comum de link do
    YouTube (watch?v=, youtu.be/, shorts/, embed/, com ou sem parâmetros de
    timestamp/playlist na frente ou depois do v=)."""
    match = _YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def save_youtube_clip(url: str, start_seconds: float, end_seconds: float) -> dict:
    """Baixa (ou reaproveita do cache) o trecho exato de um vídeo do YouTube
    que o usuário colou e recortou manualmente na revisão — em vez de um dos
    candidatos que a busca automática achou.

    Reaproveita a mesma infraestrutura de download/corte que
    search_and_download_footage já usa pra candidatos do YouTube achados pela
    IA (download_candidate -> _download_youtube: baixa a fonte uma vez por
    video-id, corta local com ffmpeg -c copy). A diferença é só quem escolheu
    o vídeo e o trecho — o resultado final é o mesmo tipo de arquivo, cacheado
    do mesmo jeito.
    """
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Não reconheci esse link como um vídeo do YouTube.")
    if not (end_seconds > start_seconds >= 0):
        raise ValueError("O segundo final precisa ser maior que o inicial.")

    candidate = {
        "source": "youtube",
        "media_type": "video",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "youtube_video_id": video_id,
        "youtube_segment": [round(start_seconds, 2), round(end_seconds, 2)],
    }
    clip_path = Path(download_candidate(candidate))

    thumb_path = cache_dir("footage") / f"{clip_path.stem}_thumb.jpg"
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hq720.jpg"
    if thumb_path.exists() or _generate_video_thumbnail(clip_path, thumb_path):
        thumbnail_url = f"/footage_cache/{thumb_path.name}"

    return {
        "source": "manual",
        "media_type": "video",
        "url": f"/footage_cache/{clip_path.name}",
        "thumbnail_url": thumbnail_url,
        "duration": round(end_seconds - start_seconds, 2),
        "clip_path": str(clip_path),
    }


# Fontes onde a miniatura julgada pelo ranking e o arquivo final baixado vêm
# de URLs DIFERENTES (google_images: miniatura é a do resultado de busca,
# arquivo final é o site de origem; youtube: miniatura é a capa do vídeo,
# arquivo final é um trecho recortado). Nas outras fontes a miniatura já é um
# recorte do mesmo arquivo (Pexels/Pixabay/Wikimedia/NASA) — comparar seria
# certeza de bater e desperdício de CPU/IO.
_HASH_CHECK_SOURCES = {"google_images", "youtube"}
_HASH_MAX_DISTANCE = 10  # distância de Hamming entre phash miniatura vs arquivo final
_BLACK_FRAME_STDDEV_THRESHOLD = 6.0  # abaixo disso o frame não tem variação de pixel nenhuma


def _looks_like_black_video(path: Path, duration: float) -> bool:
    """True só quando TODOS os frames amostrados são essencialmente lisos —
    o sinal prático de uma página de bloqueio/erro salva como vídeo (visto na
    prática com google_images antes do fix de _looks_like_real_media), não de
    um vídeo real com uma cena escura no meio. Reaproveita a mesma extração de
    frame do Fase 3 (frame_analyzer), sem chamar IA — só um desvio-padrão de
    pixel, bem mais barato."""
    import io

    from PIL import Image, ImageStat

    from modules.frame_analyzer import _extract_frames

    frames = _extract_frames(str(path), duration)
    if not frames:
        return False  # ffmpeg não conseguiu extrair nada — não é motivo pra rejeitar o vídeo
    for _, jpeg_bytes in frames:
        try:
            img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
            if ImageStat.Stat(img).stddev[0] >= _BLACK_FRAME_STDDEV_THRESHOLD:
                return False  # achou pelo menos um frame com variação real de pixel
        except Exception:
            continue
    return True


def _thumbnail_mismatch(path: Path, candidate: dict) -> str | None:
    """Distância perceptual entre a miniatura julgada pelo ranking e o
    arquivo final baixado. Heurística, não rejeição dura — só entra como
    aviso no log (ver _validate_downloaded_media) até medir a taxa real de
    falso positivo; qualquer falha na comparação (miniatura não abre, etc.)
    não é motivo pra suspeitar de nada."""
    thumb_url = candidate.get("thumbnail_url")
    if not thumb_url:
        return None
    try:
        import io

        import imagehash
        from PIL import Image

        resp = requests.get(thumb_url, timeout=10, headers=_download_headers(candidate))
        resp.raise_for_status()
        thumb_hash = imagehash.phash(Image.open(io.BytesIO(resp.content)))
        final_hash = imagehash.phash(Image.open(path))
    except Exception:
        return None
    distance = thumb_hash - final_hash
    if distance > _HASH_MAX_DISTANCE:
        return f"distância de hash perceptual {distance} (limite {_HASH_MAX_DISTANCE})"
    return None


def _validate_downloaded_media(path: Path, candidate: dict, cfg: dict) -> tuple[bool, str]:
    """Confere o ARQUIVO baixado de verdade — não só a miniatura julgada pelo
    ranking (modules/footage_ranker), nem a largura declarada pela fonte
    ANTES do download (que pode estar errada ou ser de metadado de site de
    terceiro não confiável, caso de google_images). Roda depois de todo
    download bem-sucedido, mesmo quando reaproveitado do cache — um arquivo
    ruim cacheado antes de um fix (como o bug do HTML de bloqueio já visto
    nesta sessão) senão seria reaproveitado pra sempre.

    Retorna (ok, motivo). ok=False é rejeição DURA — chamador deve descartar
    o arquivo e tentar o próximo candidato, igual a uma falha de download.
    Checagens heurísticas (hash perceptual) nunca retornam ok=False — só
    avisam no log — pra não descartar material bom por falso positivo antes
    de medir a taxa de acerto real."""
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        return False, "arquivo baixado não abre no disco"

    media_type = candidate.get("media_type", "video")
    if not _looks_like_real_media(head, media_type):
        return False, "assinatura do arquivo não bate com o media_type esperado"

    if media_type == "image":
        try:
            from PIL import Image

            with Image.open(path) as img:
                width, _height = img.size
        except Exception:
            return False, "imagem baixada não abre (arquivo corrompido ou não é imagem de verdade)"
        min_width = cfg["footage"].get("min_downloaded_width", 640)
        if width < min_width:
            return False, f"imagem baixada tem {width}px de largura, abaixo do piso de {min_width}px"
    else:
        duration = _probe_video_duration(path)
        if duration is None:
            return False, "vídeo baixado não abre no ffprobe (arquivo corrompido)"
        if duration < 0.5:
            return False, "vídeo baixado tem duração quase zero"
        if _looks_like_black_video(path, duration):
            return False, "vídeo baixado parece tela preta/quebrada (nenhum frame com variação de pixel)"

    if media_type == "image" and candidate.get("source") in _HASH_CHECK_SOURCES:
        mismatch = _thumbnail_mismatch(path, candidate)
        if mismatch:
            logger.warning(
                "Miniatura e arquivo final parecem diferentes (fonte %s): %s",
                candidate.get("source"),
                mismatch,
            )

    return True, ""


def _download_first_available(
    ranked_batch: list[dict], beat_id: int, slot: int, cfg: dict | None = None
) -> tuple[dict | None, str | None, int]:
    """Tenta baixar os candidatos de um lote (já ranqueado) em ordem de nota,
    até um baixar de verdade E passar na revalidação do arquivo final (ver
    _validate_downloaded_media). Um candidato com nota alta mas link quebrado
    (comum em google_images, que aponta pra site de terceiro com bloqueio
    anti-hotlink) não pode derrubar o shot inteiro pro fallback genérico
    enquanto outros candidatos do mesmo lote — ou de outra fonte — ainda nem
    foram tentados. Devolve (candidato, clip_path, índice no lote); em caso de
    nenhum baixar, (None, None, -1)."""
    cfg = cfg or load_config()
    for index, candidate in enumerate(ranked_batch):
        try:
            clip_path = download_candidate(candidate)
        except requests.RequestException as e:
            logger.warning(
                "Download de footage falhou, tentando próximo candidato (beat %d, shot %d, %s): %s",
                beat_id,
                slot,
                candidate.get("source"),
                e,
            )
            continue
        ok, motivo = _validate_downloaded_media(Path(clip_path), candidate, cfg)
        if not ok:
            logger.warning(
                "Arquivo baixado reprovou na revalidação, descartando e tentando próximo candidato "
                "(beat %d, shot %d, %s): %s",
                beat_id,
                slot,
                candidate.get("source"),
                motivo,
            )
            Path(clip_path).unlink(missing_ok=True)
            continue
        return candidate, clip_path, index
    return None, None, -1


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


def _result_from_candidate(
    candidate: dict, clip_path: str, search_terms: list[str], context: str = ""
) -> dict:
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

    # Fase 3: em vídeo bem mais longo que uma cena, pergunta pra IA quais
    # trechos mostram algo útil (frame_analyzer só analisa de fato acima de
    # MIN_DURATION_FOR_ANALYSIS — abaixo disso devolve [] sem custo nenhum).
    # composition_builder usa isso pra escolher onde cada reuso do clipe
    # começa, em vez do rodízio matemático cego de antes.
    if result["media_type"] == "video" and clip_path:
        from modules.frame_analyzer import find_good_segments

        good_ranges = find_good_segments(clip_path, result["duration"], context)
        if good_ranges:
            result["good_ranges"] = good_ranges

    return result


def search_and_download_footage(
    beat_id: int,
    beat_text: str,
    search_terms: list[str],
    slug: str | None = None,
    slot: int = 0,
    strategy: str = "FOOTAGE",
    entities: list[str] | None = None,
    allowed_sources: list[str] | None = None,
    google_images_recency: str | None = None,
    subject: str = "",
    identity_required: bool = False,
    original_language_query: str = "",
) -> dict:
    """Busca, ranqueia por IA (modules/footage_ranker) e baixa o melhor
    candidato pra um shot do beat. Se `slug` for passado, reaproveita uma
    escolha já salva (evita rechamar a IA de ranking à toa numa regeneração)
    e grava a lista ranqueada completa pra revisão manual no painel web.

    `slot` identifica o shot dentro do beat (um beat longo tem vários).
    `allowed_sources` restringe a busca às fontes escolhidas no painel pra
    este vídeo (None = usa todas as habilitadas no config.yaml).
    `google_images_recency` sobrepõe footage.google_images.recency do
    config.yaml só pra este job — None mantém o que estiver no arquivo,
    "" (string vazia, mandada pelo painel pra "Sem filtro") desliga o
    filtro mesmo que o config.yaml tenha um valor padrão.
    `subject`/`original_language_query` vêm da análise estruturada do shot
    (modules/keyword_extractor) e só entram no contexto passado pro ranker,
    sem afetar a busca em si. `identity_required` quando True faz o ranker
    (modules/footage_ranker) derrubar a nota de candidato com identidade
    incerta/contraditória, mesmo que o resto do candidato pareça bom.

    Retorna {"clip_path", "source", "media_type", "duration", "search_terms"}.
    `duration` é a duração do clipe em segundos (None pra imagem ou fallback)
    — usada pelo composition_builder pra nunca criar uma cena mais longa que
    o clipe, o que congelava o último frame por minutos. Se a busca não achar
    nada, usa um clipe genérico de assets/fallback/ em vez de quebrar o
    pipeline; se nem isso existir, "clip_path" vem None. Em vídeo bem mais
    longo que uma cena, vem também "good_ranges" (Fase 3, ver
    modules/frame_analyzer): trechos recomendados por IA, usados pelo
    composition_builder ao decidir onde cada reuso do clipe começa.
    """
    from modules.footage_ranker import rank_candidates

    cfg = load_config()
    if google_images_recency is not None:
        # load_config() lê o YAML do zero a cada chamada (sem cache
        # compartilhado), então sobrescrever aqui é seguro — não vaza pra
        # outra chamada/job.
        cfg["footage"]["google_images"] = {"recency": google_images_recency or None}

    # contexto curto e focado no shot em vez do beat inteiro: um beat pode ter
    # centenas de palavras cobrindo vários assuntos, e mandar tudo faria a IA
    # julgar a miniatura (ranking) ou os frames (Fase 3) contra o assunto
    # errado, além de custar tokens à toa. Usado tanto no ranking de
    # candidatos quanto na análise de trecho do candidato escolhido.
    partes = [f'Trecho: "{beat_text[:300]}"']
    if entities:
        # entidades explícitas no contexto: é o que faz a IA cobrar o assunto
        # exato em vez de aceitar um parecido
        partes.append(f"Entidades citadas (o visual precisa bater com elas): {', '.join(entities)}")
    if subject:
        partes.append(f"O shot precisa mostrar: {subject}")
    if identity_required:
        partes.append(
            "Identidade específica obrigatória: só aceite se o candidato mostrar "
            "claramente esse assunto exato, não um parecido genérico."
        )
    if original_language_query:
        partes.append(f"Busca no idioma original: {original_language_query}")
    partes.append(f"Tipo de visual pedido: {strategy}")
    partes.append(f"Esta cena deve mostrar: {', '.join(search_terms)}")
    context = " | ".join(partes)

    if slug:
        cached_review = load_candidates_for_review(slug, beat_id, slot)
        if cached_review is not None and cached_review["candidates"]:
            chosen = cached_review["candidates"][cached_review["chosen_index"]]
            try:
                return _result_from_candidate(
                    chosen, download_candidate(chosen), search_terms, context
                )
            except requests.RequestException:
                logger.exception(
                    "Falha ao reaproveitar candidato salvo (beat %d, shot %d), buscando de novo",
                    beat_id,
                    slot,
                )

    # Tenta as fontes uma a uma (ordem da hierarquia) e RANQUEIA cada lote —
    # só para de tentar a próxima fonte quando a nota do melhor candidato já
    # bate o limiar de aceite automático (não o piso de revisão — esses dois
    # eram o MESMO valor antes, então a busca parava na primeira fonte que
    # trouxesse algo só "razoável" (60), sem chegar a tentar uma fonte
    # melhor. Com o limiar mais alto (80), só para cedo quando acha algo
    # realmente bom; "razoável" ainda é guardado como candidato, mas a busca
    # continua tentando fazer melhor antes de se contentar com ele.
    threshold = cfg["ranking"]["auto_accept_threshold"]
    source_batches = _search_source_batches(search_terms, strategy, cfg, allowed_sources)
    fetched_batches: list[list[dict]] = []
    best_index: int | None = None
    best_score = -1
    for batch in source_batches:
        ranked_batch = rank_candidates(context, batch, identity_required=identity_required)
        fetched_batches.append(ranked_batch)
        top_score = ranked_batch[0].get("relevance_score")
        if top_score is None:
            # ranking indisponível (sem chave, IA fora do ar, lote com 1 só
            # candidato) — aceita esse lote sem tentar mais fontes, como antes
            best_index = len(fetched_batches) - 1
            break
        if top_score > best_score:
            best_score = top_score
            best_index = len(fetched_batches) - 1
        if top_score >= threshold:
            break

    if best_index is None:
        logger.warning(
            "Nenhum footage encontrado (beat %d, shot %d, termos: %s). Usando fallback genérico.",
            beat_id,
            slot,
            search_terms,
        )
        return _fallback_result(cfg, search_terms)

    # Tenta baixar: primeiro os candidatos do lote com melhor nota, depois os
    # das outras fontes já buscadas (ordem de prioridade), e só então — se o
    # lote vencedor bateu o threshold cedo e cortou a busca — puxa fontes que
    # nem chegaram a ser tentadas. Um link quebrado não pode mais derrubar o
    # shot inteiro pro fallback genérico enquanto outra fonte ainda nem foi
    # tentada (ver _download_first_available).
    ordered_batches = [fetched_batches[best_index]] + [
        b for i, b in enumerate(fetched_batches) if i != best_index
    ]

    chosen = clip_path = chosen_batch = None
    chosen_index = -1
    for ranked_batch in ordered_batches:
        chosen, clip_path, chosen_index = _download_first_available(ranked_batch, beat_id, slot, cfg)
        if clip_path is not None:
            chosen_batch = ranked_batch
            break

    if clip_path is None:
        for batch in source_batches:  # continua a mesma iteração, fontes que sobraram
            ranked_batch = rank_candidates(context, batch, identity_required=identity_required)
            chosen, clip_path, chosen_index = _download_first_available(ranked_batch, beat_id, slot, cfg)
            if clip_path is not None:
                chosen_batch = ranked_batch
                break

    if clip_path is None:
        logger.warning(
            "Download de footage falhou em todas as fontes tentadas (beat %d, shot %d, termos: %s). "
            "Usando fallback genérico.",
            beat_id,
            slot,
            search_terms,
        )
        return _fallback_result(cfg, search_terms)

    if slug:
        save_candidates_for_review(slug, beat_id, chosen_batch, chosen_index=chosen_index, slot=slot)

    return _result_from_candidate(chosen, clip_path, search_terms, context)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso: python -m modules.footage_search <texto-do-beat> <termo1> [termo2 ...]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    result = search_and_download_footage(0, sys.argv[1], sys.argv[2:])
    print(json.dumps(result, ensure_ascii=False, indent=2))
