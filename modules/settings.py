"""
Configurações que o usuário troca pelo painel sem editar o .env (hoje só a
chave do Serper). Fica em state/settings.json — não é cache regenerável, é
preferência do usuário; quando presente, sobrepõe o valor do .env.
"""
from __future__ import annotations

import json
import os

from modules.config import PROJECT_ROOT

STATE_DIR = PROJECT_ROOT / "state"
SETTINGS_FILE = STATE_DIR / "settings.json"


def _load() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_serper_api_key() -> str | None:
    override = _load().get("serper_api_key")
    return override or os.environ.get("SERPER_API_KEY")


def set_serper_api_key(value: str) -> None:
    data = _load()
    if value:
        data["serper_api_key"] = value
    else:
        data.pop("serper_api_key", None)
    _save(data)
