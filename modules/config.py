"""Carregamento compartilhado de config.yaml + .env para todos os módulos."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cache_dir(*parts: str) -> Path:
    cfg = load_config()
    path = PROJECT_ROOT / cfg["paths"]["cache_dir"]
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir(*parts: str) -> Path:
    cfg = load_config()
    path = PROJECT_ROOT / cfg["paths"]["output_dir"]
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(text: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "roteiro"
