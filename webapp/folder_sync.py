"""
Sincronização automática de pasta pro editor de timeline manual: observa
uma pasta local (poll simples, sem dependência nova tipo watchdog — não
precisa ser instantâneo, o usuário está baixando arquivo um a um) e, a
cada arquivo novo que aparecer, atribui na ORDEM de chegada (mtime) ao
próximo trecho vazio do RASCUNHO INTEIRO (todo bloco já fatiado, do
primeiro pro último), reaproveitando o mesmo upload que o botão manual usa
(modules/media_pool.py::save_pool_upload).

Um watch por SLUG (não mais por bloco) — pedido explícito do usuário:
gerar vários áudios de uma vez e deixar um sincronizador só preencher tudo
em sequência, avançando de bloco em bloco sozinho, sem precisar desligar e
religar apontando pro próximo. O bloco "atual" nunca é escolhido à mão:
é sempre o primeiro (na ordem dos ids) que ainda tiver vaga — inclusive
um bloco gerado DEPOIS do watch já estar ligado entra na fila sozinho, só
por o manifesto dele passar a existir em disco (ver _list_block_ids).

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
import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from modules import media_pool
from modules import timeline as timeline_module
from modules.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.5
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}

# Aviso de possível desalinhamento: um download que falha faz o PRÓXIMO
# bem-sucedido demorar bem mais que o normal pra chegar (o usuário
# percebe/reenvia) — comparamos o intervalo entre downloads consecutivos
# contra o "normal" aprendido (ver _record_gap/_expected_gap_seconds) e
# marcamos o trecho como suspeito quando estoura esse fator. É só uma
# DICA pro usuário conferir com a ferramenta de realinhamento
# (shift_media_from) — nunca corrige nada sozinho, e falso positivo
# (usuário só demorou mais numa vez) é esperado.
SLOW_GAP_FACTOR = 1.6
_MIN_SAMPLES_FOR_BASELINE = 5
_TIMING_HISTORY_MAX = 50
_TIMING_STATS_FILE = PROJECT_ROOT / "state" / "folder_sync_timing.json"


@dataclass
class _Watch:
    slug: str
    folder: Path
    consumed: list[str] = field(default_factory=list)
    # Último bloco em que uma vaga foi (ou está sendo) oferecida — só pro
    # status mostrar onde o preenchimento está agora; não é usado pra
    # decidir a próxima vaga (isso é sempre recalculado do zero varrendo
    # os blocos em ordem, ver _next_empty_position_in_draft).
    current_block_id: int | None = None
    # mtime do último arquivo consumido por ESTE watch — usado só pra medir
    # o intervalo até o próximo (ver SLOW_GAP_FACTOR acima). None até o
    # primeiro arquivo (nada pra comparar ainda).
    last_consumed_mtime: float | None = None
    last_error: str | None = None
    task: asyncio.Task | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


_watches: dict[str, _Watch] = {}


def _load_timing_history() -> list[float]:
    """Janela deslizante dos últimos intervalos NORMAIS entre downloads
    bem-sucedidos — persistida em state/ (não é cache regenerável, é o
    "aprendizado" acumulado do usuário, mesmo padrão de
    webapp/channels.py) e GLOBAL (não por slug/vídeo): é assim que o app
    aprende com cada vídeo gerado, não só dentro de uma sessão."""
    if not _TIMING_STATS_FILE.exists():
        return []
    try:
        data = json.loads(_TIMING_STATS_FILE.read_text(encoding="utf-8"))
        return [float(g) for g in data.get("recent_gaps_seconds", [])]
    except Exception:
        return []


def _record_gap(gap_seconds: float) -> None:
    """Alimenta a janela aprendida — só chamado pra um intervalo
    considerado NORMAL (ver _poll_once); um intervalo já marcado como
    suspeito nunca entra aqui, senão uma sequência de falhas reais ia
    empurrar a mediana aprendida pra cima e o detector ia ficando cego
    com o tempo."""
    gaps = _load_timing_history()
    gaps.append(round(gap_seconds, 2))
    gaps = gaps[-_TIMING_HISTORY_MAX:]
    _TIMING_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TIMING_STATS_FILE.write_text(
        json.dumps({"recent_gaps_seconds": gaps}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _expected_gap_seconds() -> float | None:
    """Mediana da janela aprendida — None enquanto não houver amostra
    suficiente (rascunho novo, poucos downloads ainda), pra nunca marcar
    aviso nenhum sem uma base minimamente confiável. Mediana (não média):
    resiste melhor a um outlier isolado (usuário parou pra tomar café no
    meio) distorcer o valor aprendido."""
    gaps = _load_timing_history()
    if len(gaps) < _MIN_SAMPLES_FOR_BASELINE:
        return None
    return statistics.median(gaps)


def _media_ext_type(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    return None


def _next_empty_position(manifest: list[dict]) -> tuple[int, int] | None:
    """(índice do trecho, posição de mídia) da próxima vaga livre DENTRO
    DE UM MANIFESTO — só em trechos de mídia única. None quando não sobra
    vaga preenchível automaticamente (trechos completos, só sobrou efeito
    de galeria, ou trecho marcado needs_media=false pela IA/usuário)."""
    for slot in manifest:
        if not timeline_module.is_single_media_slot(slot):
            continue
        _, max_media = timeline_module.effect_media_bounds(slot.get("effect") or timeline_module.DEFAULT_EFFECT)
        media_list = slot.get("media") or []
        for i in range(max_media):
            item = media_list[i] if i < len(media_list) else None
            if not item:
                return slot["index"], i
    return None


def _next_empty_position_in_draft(slug: str) -> tuple[int, int, int] | None:
    """(block_id, índice do trecho, posição de mídia) da próxima vaga
    livre no RASCUNHO INTEIRO — varre os blocos em ordem crescente e para
    no primeiro que ainda tiver vaga, então o bloco 1 esgota antes do 2
    começar a receber, sem precisar de nenhum estado de progresso: é só
    escolher sempre o primeiro bloco com vaga a cada chamada."""
    for block_id in timeline_module.list_block_ids(slug):
        manifest = timeline_module.load_manifest(slug, block_id)
        if manifest is None:
            continue
        position = _next_empty_position(manifest)
        if position is not None:
            return block_id, position[0], position[1]
    return None


async def _poll_once(watch: _Watch) -> None:
    candidates = sorted(
        (p for p in watch.folder.iterdir() if p.is_file() and _media_ext_type(p)),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        return

    used_dir = watch.folder / "usados"

    for path in candidates:
        position = _next_empty_position_in_draft(watch.slug)
        if position is None:
            break  # nada mais pra preencher automaticamente no rascunho (ou nada gerado ainda)

        block_id, slot_index, media_index = position
        manifest = timeline_module.load_manifest(watch.slug, block_id)

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

        # Intervalo até o download anterior — possível sinal de que ROLOU
        # um reenvio no meio (o download desta mídia teria sido rápido, mas
        # teve uma tentativa falha antes que o usuário não nos conta).
        mtime = path.stat().st_mtime
        if watch.last_consumed_mtime is not None:
            gap = mtime - watch.last_consumed_mtime
            expected = _expected_gap_seconds()
            if expected is not None and gap > expected * SLOW_GAP_FACTOR:
                slot["sync_warning"] = {
                    "gap_seconds": round(gap, 1),
                    "expected_seconds": round(expected, 1),
                }
            else:
                slot.pop("sync_warning", None)
                _record_gap(gap)
        watch.last_consumed_mtime = mtime

        # salva a cada arquivo (não só no fim do poll): um poll pode
        # atravessar a fronteira de dois blocos diferentes, cada um com
        # seu próprio manifesto em disco.
        timeline_module.save_manifest(watch.slug, block_id, manifest)

        watch.current_block_id = block_id
        used_dir.mkdir(exist_ok=True)
        path.rename(used_dir / path.name)
        watch.consumed.append(path.name)


async def _run(watch: _Watch) -> None:
    while not watch.stop_event.is_set():
        try:
            await _poll_once(watch)
            watch.last_error = None
        except Exception as e:
            watch.last_error = str(e)
            logger.exception("Sincronizador de pasta falhou (slug=%s)", watch.slug)
        try:
            await asyncio.wait_for(watch.stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


def start(slug: str, folder: Path) -> None:
    stop(slug)  # troca a pasta observada anterior pra este rascunho, se houver
    watch = _Watch(slug=slug, folder=folder)
    watch.task = asyncio.create_task(_run(watch))
    _watches[slug] = watch


def stop(slug: str) -> None:
    watch = _watches.pop(slug, None)
    if watch is not None:
        # só sinaliza — nunca cancela a task à força, pra não interromper
        # um _poll_once no meio de mover/salvar um arquivo.
        watch.stop_event.set()


def status(slug: str) -> dict:
    watch = _watches.get(slug)
    if watch is None:
        return {
            "watching": False,
            "folder": None,
            "consumed_count": 0,
            "last_error": None,
            "all_filled": None,
            "current_block_id": None,
        }

    all_filled = _next_empty_position_in_draft(slug) is None
    return {
        "watching": True,
        "folder": str(watch.folder),
        "consumed_count": len(watch.consumed),
        "last_error": watch.last_error,
        "all_filled": all_filled,
        "current_block_id": watch.current_block_id,
    }
