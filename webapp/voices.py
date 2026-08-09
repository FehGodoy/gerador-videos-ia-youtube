"""
Lista vozes da Cartesia por idioma e gera/cacheia um áudio de amostra para
cada uma, para o seletor de voz do painel web. Cache em disco
(cache/voice_previews/<idioma>/) — a primeira carga de um idioma demora
(gera uma amostra por voz), as próximas são instantâneas.
"""
from __future__ import annotations

import asyncio
import json
import os

import requests

from modules.config import cache_dir, load_config

CARTESIA_VOICES_URL = "https://api.cartesia.ai/voices"
CARTESIA_TTS_BYTES_URL = "https://api.cartesia.ai/tts/bytes"

MAX_VOICES_PER_LANGUAGE = 30
PREVIEW_SAMPLE_RATE = 16000
PREVIEW_BIT_RATE = 32000
PREVIEW_CONCURRENCY = 5

# Frase curta usada como amostra de voz, por idioma (fallback em inglês).
PREVIEW_TEXT = {
    "pt": "Assim começou tudo.",
    "en": "This is how it all began.",
    "es": "Así empezó todo.",
    "fr": "C'est ainsi que tout a commencé.",
    "de": "So hat alles begonnen.",
    "it": "Così è iniziato tutto.",
    "ja": "こうして全てが始まった。",
}
DEFAULT_PREVIEW_TEXT = PREVIEW_TEXT["en"]


def _api_key() -> str:
    api_key = os.environ.get("CARTESIA_API_KEY")
    if not api_key:
        raise RuntimeError("CARTESIA_API_KEY não definida no .env.")
    return api_key


def _fetch_voices_for_language(language: str, api_version: str, limit: int) -> list[dict]:
    api_key = _api_key()
    headers = {"X-API-Key": api_key, "Cartesia-Version": api_version}
    result: list[dict] = []
    starting_after = None
    while len(result) < limit:
        params = {"limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        resp = requests.get(CARTESIA_VOICES_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        for v in body.get("data", []):
            if v.get("language") == language and v.get("is_public"):
                result.append(v)
                if len(result) >= limit:
                    break
        if not body.get("has_more") or len(result) >= limit:
            break
        starting_after = body.get("next_page")
    return result


def _generate_preview_bytes(voice_id: str, language: str, api_version: str) -> bytes:
    api_key = _api_key()
    text = PREVIEW_TEXT.get(language, DEFAULT_PREVIEW_TEXT)
    resp = requests.post(
        CARTESIA_TTS_BYTES_URL,
        headers={
            "X-API-Key": api_key,
            "Cartesia-Version": api_version,
            "Content-Type": "application/json",
        },
        json={
            "model_id": "sonic-3",
            "transcript": text,
            "voice": {"mode": "id", "id": voice_id},
            "language": language,
            "output_format": {
                "container": "mp3",
                "sample_rate": PREVIEW_SAMPLE_RATE,
                "bit_rate": PREVIEW_BIT_RATE,
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


async def list_voices(language: str) -> list[dict]:
    """Retorna a lista de vozes cacheadas para o idioma (nome, gênero,
    descrição, URL do áudio de amostra). Busca na Cartesia e gera as
    amostras na primeira vez que o idioma é pedido; depois só lê o cache."""
    lang_dir = cache_dir("voice_previews", language)
    manifest_path = lang_dir / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    cfg = load_config()
    api_version = cfg["narration"]["cartesia_api_version"]

    raw_voices = await asyncio.to_thread(
        _fetch_voices_for_language, language, api_version, MAX_VOICES_PER_LANGUAGE
    )

    semaphore = asyncio.Semaphore(PREVIEW_CONCURRENCY)

    async def _process(v: dict) -> dict | None:
        async with semaphore:
            try:
                audio_bytes = await asyncio.to_thread(
                    _generate_preview_bytes, v["id"], language, api_version
                )
            except requests.RequestException:
                return None
            (lang_dir / f"{v['id']}.mp3").write_bytes(audio_bytes)
            return {
                "id": v["id"],
                "name": v.get("name", ""),
                "gender": v.get("gender"),
                "description": v.get("description", ""),
                "preview_url": f"/voice_previews/{language}/{v['id']}.mp3",
            }

    results = await asyncio.gather(*[_process(v) for v in raw_voices])
    voices = [r for r in results if r is not None]

    manifest_path.write_text(json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8")
    return voices
