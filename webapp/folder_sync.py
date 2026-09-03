"""
Sincronização automática de pasta pro editor de timeline manual: observa
uma pasta local (poll simples, sem dependência nova tipo watchdog — não
precisa ser instantâneo, o usuário está baixando arquivo um a um) e, a
cada arquivo novo que aparecer, atribui na ORDEM de chegada (mtime) ao
próximo trecho vazio do bloco, reaproveitando o mesmo upload que o botão
manual usa (modules/media_pool.py::save_pool_upload).

Só preenche trechos com efeito de MÍDIA ÚNICA (padrão/parallax_pan) —
efeito de galeria (2+ posições, ex. split_screen) fica de fora por
decisão explícita do usuário: ele quer isso automático só pro caso comum
(1 trecho = 1 mídia), e continuar escolhendo manualmente quando o trecho
pede mais de um arquivo.

Arquivo consumido é MOVIDO (não copiado) pra uma subpasta "usados/" dentro
da pasta observada — assim nunca reprocessa o mesmo arquivo de novo no
próximo poll, sem apagar nada.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from modules import media_pool
from modules import timeline as timeline_module

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.5
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


@dataclass
class _Watch:
    slug: str
    block_id: int
    folder: Path
    consumed: list[str] = field(default_factory=list)
    last_error: str | None = None
    task: asyncio.Task | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


_watches: dict[tuple[str, int], _Watch] = {}


def _media_ext_type(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    return None


def _next_empty_position(manifest: list[dict]) -> tuple[int, int] | None:
    """(índice do trecho, posição de mídia) da próxima vaga livre — só em
    trechos de mídia única. None quando não sobra vaga preenchível
    automaticamente (trechos completos ou só sobrou efeito de galeria)."""
    for slot in manifest:
        effect = slot.get("effect") or timeline_module.DEFAULT_EFFECT
        if effect in timeline_module.GALLERY_EFFECTS:
            continue
        _, max_media = timeline_module.effect_media_bounds(effect)
        media_list = slot.get("media") or []
        for i in range(max_media):
            item = media_list[i] if i < len(media_list) else None
            if not item:
                return slot["index"], i
    return None


async def _poll_once(watch: _Watch) -> None:
    manifest = timeline_module.load_manifest(watch.slug, watch.block_id)
    if manifest is None:
        return

    candidates = sorted(
        (p for p in watch.folder.iterdir() if p.is_file() and _media_ext_type(p)),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        return

    used_dir = watch.folder / "usados"
    changed = False

    for path in candidates:
        position = _next_empty_position(manifest)
        if position is None:
            break  # nada mais pra preencher automaticamente neste bloco

        slot_index, media_index = position
        media_type = _media_ext_type(path)
        data = path.read_bytes()
        saved = await asyncio.to_thread(media_pool.save_pool_upload, watch.slug, path.name, data)

        media: dict = {"pool_filename": saved["filename"], "media_type": media_type}
        if media_type == "video":
            # sem interação do usuário pra escolher o recorte — sempre
            # começa do início do vídeo (mesmo padrão de fallback já usado
            # quando clip_start_seconds não é informado no anexo manual).
            media["clip_start_seconds"] = 0.0

        slot = manifest[slot_index]
        media_list = slot.setdefault("media", [])
        while len(media_list) <= media_index:
            media_list.append(None)
        media_list[media_index] = media

        used_dir.mkdir(exist_ok=True)
        path.rename(used_dir / path.name)
        watch.consumed.append(path.name)
        changed = True

    if changed:
        timeline_module.save_manifest(watch.slug, watch.block_id, manifest)


async def _run(watch: _Watch) -> None:
    while not watch.stop_event.is_set():
        try:
            await _poll_once(watch)
            watch.last_error = None
        except Exception as e:
            watch.last_error = str(e)
            logger.exception(
                "Sincronizador de pasta falhou (slug=%s, block=%s)", watch.slug, watch.block_id
            )
        try:
            await asyncio.wait_for(watch.stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


def start(slug: str, block_id: int, folder: Path) -> None:
    stop(slug, block_id)  # troca a pasta observada anterior pra este bloco, se houver
    watch = _Watch(slug=slug, block_id=block_id, folder=folder)
    watch.task = asyncio.create_task(_run(watch))
    _watches[(slug, block_id)] = watch


def stop(slug: str, block_id: int) -> None:
    watch = _watches.pop((slug, block_id), None)
    if watch is not None:
        # só sinaliza — nunca cancela a task à força, pra não interromper
        # um _poll_once no meio de mover/salvar um arquivo.
        watch.stop_event.set()


def status(slug: str, block_id: int) -> dict:
    watch = _watches.get((slug, block_id))
    if watch is None:
        return {"watching": False, "folder": None, "consumed_count": 0, "last_error": None, "all_filled": None}

    manifest = timeline_module.load_manifest(slug, block_id)
    all_filled = _next_empty_position(manifest) is None if manifest is not None else None
    return {
        "watching": True,
        "folder": str(watch.folder),
        "consumed_count": len(watch.consumed),
        "last_error": watch.last_error,
        "all_filled": all_filled,
    }
