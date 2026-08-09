"""
Canais e vozes favoritas por canal — persistência simples em JSON local.

Não é cache regenerável (como cache/), é preferência do usuário: guardamos o
objeto da voz inteiro (não só o id) pra não depender do manifest daquele
idioma continuar igual depois.
"""
from __future__ import annotations

import json
from pathlib import Path

from modules.config import PROJECT_ROOT

STATE_DIR = PROJECT_ROOT / "state"
CHANNELS_FILE = STATE_DIR / "channels.json"


def _load() -> dict:
    if not CHANNELS_FILE.exists():
        return {}
    return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CHANNELS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_channels() -> list[str]:
    return sorted(_load().keys())


def create_channel(name: str) -> list[str]:
    data = _load()
    if name not in data:
        data[name] = {"favorites": []}
        _save(data)
    return sorted(data.keys())


def get_favorites(channel: str) -> list[dict]:
    return _load().get(channel, {}).get("favorites", [])


def add_favorite(channel: str, voice: dict) -> list[dict]:
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    if not any(v["id"] == voice["id"] for v in entry["favorites"]):
        entry["favorites"].append(voice)
        _save(data)
    return entry["favorites"]


def remove_favorite(channel: str, voice_id: str) -> list[dict]:
    data = _load()
    entry = data.setdefault(channel, {"favorites": []})
    entry["favorites"] = [v for v in entry["favorites"] if v["id"] != voice_id]
    _save(data)
    return entry["favorites"]
