"""
Servidor local do painel web (FastAPI). Roda em cima dos mesmos módulos do
pipeline (modules/*) — este arquivo só expõe rotas HTTP/SSE, não reimplementa
nenhuma lógica de dados/ML.

Rodar com: uvicorn webapp.server:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import hashlib
import io

import requests
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from modules import footage_search, github_render, media_pool, settings as settings_module
from modules import timeline as timeline_module
from modules.composition_builder import validate_composition
from modules.config import PROJECT_ROOT, cache_dir, load_config, output_dir
from modules.image_effects import get_blurred_background
from modules.narration import synthesize_beat
from modules.script_parser import Beat
from webapp import channels as channels_module
from webapp import folder_sync
from webapp import voices as voices_module
from webapp.job_runner import Job, job_manager

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Painel do pipeline de vídeo")

STATIC_DIR = Path(__file__).resolve().parent / "static"

cfg = load_config()
VOICE_PREVIEWS_DIR = PROJECT_ROOT / cfg["paths"]["cache_dir"] / "voice_previews"
VOICE_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
NARRATION_CACHE_DIR = cache_dir("narration")
FOOTAGE_CACHE_DIR = cache_dir("footage")
OWN_MEDIA_CACHE_DIR = cache_dir("own_media")
CHANNEL_AVATARS_DIR = PROJECT_ROOT / "state" / "channel_avatars"
CHANNEL_AVATARS_DIR.mkdir(parents=True, exist_ok=True)


class NoCacheStaticFiles(StaticFiles):
    """HTML/CSS/JS deste painel mudam com frequência durante o desenvolvimento
    — sem isso, o navegador guarda uma cópia antiga em cache e a página
    parece "quebrada" até um hard refresh."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/voice_previews", StaticFiles(directory=str(VOICE_PREVIEWS_DIR)), name="voice_previews")
app.mount("/narration_cache", StaticFiles(directory=str(NARRATION_CACHE_DIR)), name="narration_cache")
# Serve os clipes já baixados (preview de candidato já escolhido) e os
# arquivos de upload manual — os dois só existem localmente, sem URL externa
# pra usar como thumbnail_url/url do jeito que os outros candidatos têm.
app.mount("/footage_cache", StaticFiles(directory=str(FOOTAGE_CACHE_DIR)), name="footage_cache")
# Preview do lote de mídia própria (modo alternativo à busca por IA, ver
# modules/media_pool.py) — cada slug tem sua própria subpasta.
app.mount("/own_media_cache", StaticFiles(directory=str(OWN_MEDIA_CACHE_DIR)), name="own_media_cache")
# Avatar do canal pra barra de inscrever-se — preview no painel.
app.mount("/channel_avatars", StaticFiles(directory=str(CHANNEL_AVATARS_DIR)), name="channel_avatars")


class BlockIn(BaseModel):
    id: int
    text: str


class NarrationBlockRequest(BaseModel):
    slug: str
    block_id: int
    text: str
    voice_id: str
    language: str = "pt"
    speed: float = 1.0
    force: bool = False


class CreateJobRequest(BaseModel):
    slug: str
    blocks: list[BlockIn]
    voice_id: str
    language: str = "pt"
    speed: float = 1.0
    remote_render: bool = True
    # Fontes de footage escolhidas no painel pra este vídeo. None = usa
    # footage.sources do config.yaml sem restrição extra (compatibilidade
    # com quem ainda não manda o campo).
    sources: list[str] | None = None
    # Filtro de recência do Google Imagens pra este vídeo. None = usa
    # footage.google_images.recency do config.yaml sem sobrepor nada
    # (compatibilidade com quem ainda não manda o campo). "" = sem filtro,
    # mesmo que o config.yaml tenha um valor padrão.
    google_images_recency: str | None = None
    # "ai_search" (padrão) busca automaticamente; "own_media" distribui o
    # lote de fotos/vídeos que o usuário subiu via /api/media-pool/{slug}
    # antes de criar o job (ver modules/media_pool.py) — sources/
    # google_images_recency são ignorados nesse modo.
    media_mode: str = "ai_search"
    # Canal escolhido no painel — usado só pra resolver a identidade (nome/
    # @handle/avatar) da barra de inscrever-se (ver webapp/channels.py).
    # None/vazio = vídeo sem a barra.
    channel: str | None = None


class ChannelRequest(BaseModel):
    name: str


class HandleRequest(BaseModel):
    handle: str


class FootageChoiceRequest(BaseModel):
    candidate_index: int


class YoutubeClipRequest(BaseModel):
    url: str
    start_seconds: float
    end_seconds: float


class SlotHintsRequest(BaseModel):
    language: str = "pt"


class AssignSlotRequest(BaseModel):
    pool_filename: str
    clip_start_seconds: float | None = None
    media_index: int = 0


class SetSlotEffectRequest(BaseModel):
    effect: str


class SetSlotNeedsMediaRequest(BaseModel):
    needs_media: bool


class WatchFolderRequest(BaseModel):
    folder_path: str


class SerperKeyRequest(BaseModel):
    api_key: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


SOURCE_LABELS = {
    "pexels": "Pexels",
    "pixabay": "Pixabay",
    "wikimedia": "Wikimedia Commons",
    "nasa": "NASA",
    "youtube": "YouTube",
    "google_images": "Google Imagens",
}

SOURCE_HINTS = {
    "pexels": "Vídeo/foto de stock genérico",
    "pixabay": "Vídeo/foto de stock genérico",
    "wikimedia": "Foto histórica/documental, exige crédito",
    "nasa": "Foto/vídeo espacial",
    "youtube": "Trecho de vídeo real (Creative Commons)",
    "google_images": "Foto específica via busca (Serper), sem filtro de licença",
}

RECENCY_OPTIONS = ("day", "week", "month", "year")
RECENCY_LABELS = {
    "": "Sem filtro",
    "day": "Último dia",
    "week": "Última semana",
    "month": "Último mês",
    "year": "Último ano",
}


@app.get("/api/footage-sources")
async def get_footage_sources() -> dict:
    cfg = load_config()
    habilitadas = cfg["footage"]["sources"]
    recency_default = (cfg["footage"].get("google_images") or {}).get("recency") or ""
    return {
        "sources": [
            {"id": s, "label": SOURCE_LABELS.get(s, s), "hint": SOURCE_HINTS.get(s, "")}
            for s in footage_search.SOURCE_PRIORITY
            if s in habilitadas
        ],
        "default": habilitadas,
        "recency_options": [
            {"id": r, "label": RECENCY_LABELS[r]} for r in ("", *RECENCY_OPTIONS)
        ],
        "recency_default": recency_default,
    }


@app.get("/api/voices")
async def get_voices(language: str = "pt") -> list[dict]:
    try:
        return await voices_module.list_voices(language)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/narration-blocks")
async def create_narration_block(req: NarrationBlockRequest) -> dict:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Bloco vazio.")

    beat = Beat(id=req.block_id, text=req.text)
    try:
        result = await asyncio.to_thread(
            synthesize_beat,
            beat,
            req.slug,
            voice_id=req.voice_id,
            language=req.language,
            speed=req.speed,
            force=req.force,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fatiamento em trechos de ~4s pro editor de timeline manual (ver
    # modules/timeline.py) — geometria pura sobre os timestamps por palavra
    # que a Cartesia já devolveu, sem custo nenhum, então roda sempre
    # (mesmo que este rascunho acabe usando busca por IA em vez de mídia
    # própria). Tradução/dica de IA ficam pra POST .../hints, chamado
    # separado pelo painel — é chamada de LLM, não trava esta resposta.
    slots = timeline_module.chunk_captions(result["captions"])
    # "Regenerar" (force=True) chama esta mesma rota de novo — sem isso, a
    # mídia que o usuário já tinha atribuído por trecho seria apagada só por
    # reprocessar o áudio. Reaproveita por índice (best-effort: se a fala
    # mudou de duração, o índice pode não bater exatamente com o trecho de
    # antes, mas ainda é muito melhor que perder a atribuição sempre).
    existing = timeline_module.load_manifest(req.slug, req.block_id)
    if existing:
        for i, old_slot in enumerate(existing):
            if i >= len(slots):
                continue
            if old_slot.get("media"):
                slots[i]["media"] = old_slot["media"]
                slots[i]["effect"] = old_slot.get("effect", timeline_module.DEFAULT_EFFECT)
                slots[i]["translation_pt"] = old_slot.get("translation_pt", "")
                slots[i]["hint"] = old_slot.get("hint", "")
                slots[i]["image_prompt"] = old_slot.get("image_prompt", "")
            # independente de ter mídia: uma escolha explícita do usuário
            # sobre precisar ou não de mídia não pode ser apagada só por
            # reprocessar o áudio (ver POST .../needs-media).
            if old_slot.get("needs_media_overridden"):
                slots[i]["needs_media"] = old_slot.get("needs_media", True)
                slots[i]["needs_media_overridden"] = True
    timeline_module.save_manifest(req.slug, req.block_id, slots)

    return {
        "block_id": req.block_id,
        "duration_seconds": result["duration_seconds"],
        "audio_url": f"/narration_cache/{req.slug}/beat_{req.block_id:03d}.wav",
        "captions": result["captions"],
        "slots": slots,
    }


@app.post("/api/jobs")
async def create_job(req: CreateJobRequest) -> dict:
    if not req.blocks:
        raise HTTPException(status_code=400, detail="Nenhum bloco de narração adicionado.")
    if not req.voice_id:
        raise HTTPException(status_code=400, detail="Nenhuma voz selecionada.")
    if req.media_mode not in ("ai_search", "own_media"):
        raise HTTPException(status_code=400, detail="media_mode inválido.")
    if req.media_mode == "ai_search":
        if req.sources is not None and not req.sources:
            raise HTTPException(status_code=400, detail="Selecione ao menos uma fonte de mídia.")
        if req.google_images_recency not in (None, "", *RECENCY_OPTIONS):
            raise HTTPException(status_code=400, detail="Filtro de recência inválido.")
    else:
        pool = media_pool.list_pool(req.slug)
        if not pool["photos"] and not pool["videos"]:
            raise HTTPException(status_code=400, detail="Envie ao menos uma foto ou vídeo antes de gerar.")
        # Editor de timeline manual: bloqueia a criação do job até TODO
        # trecho de todo bloco ter mídia atribuída — sem isso, o valor do
        # controle manual (o usuário decide exatamente o que aparece onde)
        # ficaria contradito por um preenchimento automático silencioso pro
        # que sobrou (ver modules/composition_builder.py::_scenes_from_manifest,
        # que consome esse manifesto direto, sem PoolDistributor).
        missing_slots = 0
        for b in req.blocks:
            manifest = timeline_module.load_manifest(req.slug, b.id)
            if manifest is None:
                missing_slots += 1
                continue
            for s in manifest:
                if s.get("needs_media") is False:
                    # a IA (ou o usuário, via override) decidiu que este
                    # trecho vira texto/gráfico — não exige anexo.
                    continue
                min_media, _ = timeline_module.effect_media_bounds(s.get("effect", timeline_module.DEFAULT_EFFECT))
                attached = sum(1 for m in (s.get("media") or []) if m)
                if attached < min_media:
                    missing_slots += 1
        if missing_slots:
            raise HTTPException(
                status_code=400,
                detail=f"Faltam {missing_slots} trecho(s) sem mídia atribuída na linha do tempo.",
            )

    # Falha cedo: sem isso, um gh sem login só estourava lá na frente, depois
    # da revisão manual de footage inteira — e jogava esse trabalho fora.
    if req.remote_render:
        auth_error = await asyncio.to_thread(github_render.check_auth)
        if auth_error:
            raise HTTPException(status_code=400, detail=auth_error)

    # Resolvido aqui (não em modules/composition_builder.py): "canal" é um
    # conceito só do painel (state/channels.json), e modules/ nunca importa
    # webapp/ — só desce um dict com dados já primitivos.
    subscribe_identity = None
    if req.channel:
        identity = channels_module.get_identity(req.channel)
        if identity["handle"]:
            subscribe_identity = {
                "channel_name": req.channel,
                "handle": identity["handle"],
                "avatar_filename": identity["avatar_filename"],
            }

    beats = [Beat(id=b.id, text=b.text) for b in req.blocks]
    job = job_manager.create_job(
        req.slug,
        beats,
        req.voice_id,
        req.language,
        req.speed,
        remote=req.remote_render,
        allowed_sources=req.sources,
        google_images_recency=req.google_images_recency,
        media_mode=req.media_mode,
        subscribe_identity=subscribe_identity,
    )
    return {"job_id": job.id, "beats": job.beats}


def _sse_format(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(job: Job):
    if job.status == "done":
        yield _sse_format("job_done", {"video_url": f"/api/jobs/{job.id}/video"})
        return
    if job.status == "error":
        yield _sse_format("job_error", {"message": job.error or "erro desconhecido"})
        return
    if job.status == "awaiting_review":
        # cliente reconectando depois que "composition_ready" já passou pela
        # fila da primeira vez — reemite pra ele não ficar sem saber
        yield _sse_format("composition_ready", {})

    while True:
        item = await job.queue.get()
        yield _sse_format(item["event"], item["data"])
        if item["event"] in ("job_done", "job_error"):
            break


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return StreamingResponse(_event_stream(job), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/video")
async def job_video(job_id: str) -> FileResponse:
    job = job_manager.get(job_id)
    if job is None or job.video_path is None or not job.video_path.exists():
        raise HTTPException(status_code=404, detail="Vídeo ainda não está pronto.")
    return FileResponse(job.video_path, media_type="video/mp4")


def _scene_summary(scenes: list[dict], slot: int) -> dict:
    """Quantas cenas do beat vieram deste shot e quanto tempo de tela ele
    ocupa — é o que deixa claro na revisão o peso real de cada escolha.
    Casa por shot_slot (não por clip_path): funciona igual pra shot que virou
    footage e pra shot que virou card de texto, e não se confunde quando dois
    shots diferentes acabam usando o mesmo clipe (ex: fallback genérico
    compartilhado)."""
    matching = [s for s in scenes if s.get("shot_slot") == slot]
    return {
        "scene_count": len(matching),
        "screen_seconds": round(
            sum(s["end_seconds"] - s["start_seconds"] for s in matching), 1
        ),
    }


def _apply_chosen_candidate(
    job: Job, beat_id: int, slot: int, candidates: list[dict], chosen_index: int, clip_path: str
) -> int:
    """Grava a escolha (troca manual ou upload) pra revisão e atualiza toda
    cena do composition.json que veio deste shot — é o composition.json em
    disco que o render (local ou GitHub) de fato lê depois da confirmação.

    Casa cena por (beat_id, shot_slot) em vez de por clip_path: um shot que
    virou card de texto não tem clip_path nenhum pra casar, então o
    casamento antigo (por clip_path) nunca alcançava esses casos — exatamente
    os que mais precisam da edição manual.
    """
    chosen = candidates[chosen_index]
    footage_search.save_candidates_for_review(job.slug, beat_id, candidates, chosen_index, slot=slot)

    composition_path = output_dir(job.slug) / "composition.json"
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    relative_path = Path(clip_path).resolve().relative_to(PROJECT_ROOT).as_posix()
    media_type = chosen.get("media_type", "video")
    new_footage = {
        "clip_path": relative_path,
        "source": chosen["source"],
        "media_type": media_type,
        "search_terms": [],
    }
    if media_type == "image":
        # Mesmo pré-cômputo de modules/composition_builder.py::_scene_footage
        # — sem isso, uma cena editada manualmente na revisão cairia de
        # volta no blur ao vivo no Chromium (ver modules/image_effects.py).
        cfg = load_config()
        new_footage["blurred_background_path"] = get_blurred_background(
            relative_path, cfg["video"]["width"], cfg["video"]["height"]
        )
    updated_scenes = 0
    for beat_entry in composition["beats"]:
        if beat_entry["id"] != beat_id:
            continue
        for scene in beat_entry.get("scenes", []):
            if scene.get("shot_slot") != slot:
                continue
            scene["kind"] = "footage"
            scene["footage"] = {
                **new_footage,
                "search_terms": (scene.get("footage") or {}).get("search_terms", []),
            }
            # o offset era calculado pra duração do clipe antigo (ou nem
            # existia, se a cena era um card); zera pra não começar depois do
            # fim de um clipe mais curto (tela preta)
            scene["clip_start_seconds"] = 0.0
            scene.pop("concept_text", None)
            updated_scenes += 1
        break
    validate_composition(composition)
    composition_path.write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")

    return updated_scenes


@app.get("/api/jobs/{job_id}/footage-candidates")
async def get_footage_candidates(job_id: str) -> list[dict]:
    """Um beat longo é preenchido por vários shots, cada um com sua própria
    lista de candidatos — a revisão é por (beat, shot), não por beat."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    composition_path = output_dir(job.slug) / "composition.json"
    scenes_by_beat: dict[int, list[dict]] = {}
    entities_by_beat: dict[int, list[str]] = {}
    if composition_path.exists():
        composition = json.loads(composition_path.read_text(encoding="utf-8"))
        scenes_by_beat = {b["id"]: b.get("scenes", []) for b in composition["beats"]}
        entities_by_beat = {b["id"]: b.get("entities", []) for b in composition["beats"]}

    result = []
    for beat in job.beats:
        beat_scenes = scenes_by_beat.get(beat["id"], [])
        shots = []
        for slot in footage_search.list_review_slots(job.slug, beat["id"]):
            review = footage_search.load_candidates_for_review(job.slug, beat["id"], slot)
            if review is None:
                continue
            cena = next((s for s in beat_scenes if s.get("shot_slot") == slot), None)
            shots.append(
                {
                    "slot": slot,
                    "candidates": review["candidates"],
                    "chosen_index": review["chosen_index"],
                    "usage": _scene_summary(beat_scenes, slot),
                    "visual_strategy": (cena or {}).get("visual_strategy"),
                }
            )

        # cenas que viraram card conceitual não têm candidato pra revisar, mas
        # precisam aparecer: é o "não achamos nada bom" ficando visível. slot
        # deixa o painel oferecer "enviar mídia manualmente" mesmo aqui —
        # inclusive pra shot que nunca achou candidato nenhum pra buscar (só
        # esses têm shot_slot; um card sem slot é o card genérico de beat
        # totalmente sem shots, caso raríssimo, sem o que editar).
        cards = [
            {
                "text": s.get("concept_text", ""),
                "seconds": round(s["end_seconds"] - s["start_seconds"], 1),
                "slot": s.get("shot_slot"),
            }
            for s in beat_scenes
            if s["kind"] == "concept"
        ]
        # shots vazio pode significar duas coisas bem diferentes: a busca por
        # IA não achou nada bom (fallback de verdade) OU o modo de mídia
        # própria foi usado (nunca salva candidato pra revisão, ver
        # modules/media_pool.py) e a mídia está lá, só não é revisável. Sem
        # essa distinção, um vídeo cheio de mídia própria mostrava a mesma
        # mensagem assustadora de "nada encontrado".
        used_own_media = any(
            s["kind"] == "footage" and (s.get("footage") or {}).get("source") == "manual"
            for s in beat_scenes
        )
        result.append(
            {
                "beat_id": beat["id"],
                "text": beat["text"],
                "entities": entities_by_beat.get(beat["id"], []),
                "shots": shots,
                "concept_cards": cards,
                "used_own_media": used_own_media,
            }
        )
    return result


@app.post("/api/jobs/{job_id}/footage-candidates/{beat_id}/{slot}")
async def choose_footage_candidate(
    job_id: str, beat_id: int, slot: int, req: FootageChoiceRequest
) -> dict:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    review = footage_search.load_candidates_for_review(job.slug, beat_id, slot)
    if review is None or not (0 <= req.candidate_index < len(review["candidates"])):
        raise HTTPException(status_code=400, detail="Candidato inválido.")

    chosen = review["candidates"][req.candidate_index]
    try:
        clip_path = await asyncio.to_thread(footage_search.download_candidate, chosen)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao baixar o candidato escolhido: {e}")

    updated_scenes = _apply_chosen_candidate(
        job, beat_id, slot, review["candidates"], req.candidate_index, str(clip_path)
    )

    return {
        "beat_id": beat_id,
        "slot": slot,
        "chosen_index": req.candidate_index,
        "source": chosen["source"],
        "updated_scenes": updated_scenes,
    }


_MANUAL_UPLOAD_MAX_BYTES = 200 * 1024 * 1024  # 200MB — sobra pra um clipe curto, barra engano/upload errado


@app.post("/api/jobs/{job_id}/footage-candidates/{beat_id}/{slot}/upload")
async def upload_footage_candidate(job_id: str, beat_id: int, slot: int, file: UploadFile) -> dict:
    """Envia um arquivo próprio pra usar num shot específico, em vez de
    aceitar os candidatos achados automaticamente (ou o card de texto, se a
    busca não achou nada bom). Funciona mesmo quando este slot nunca teve
    review salva — o card de texto de um shot que buscou e não achou nada, ou
    de um shot TEXT que nunca buscou, não tem arquivo de candidatos ainda."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")
    if len(data) > _MANUAL_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Arquivo maior que 200MB.")

    try:
        manual_candidate = await asyncio.to_thread(
            footage_search.save_manual_upload, data, file.filename or "upload.mp4"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar o arquivo: {e}")

    review = footage_search.load_candidates_for_review(job.slug, beat_id, slot)
    candidates = [*(review["candidates"] if review else []), manual_candidate]
    chosen_index = len(candidates) - 1

    updated_scenes = _apply_chosen_candidate(
        job, beat_id, slot, candidates, chosen_index, manual_candidate["clip_path"]
    )

    return {
        "beat_id": beat_id,
        "slot": slot,
        "chosen_index": chosen_index,
        "candidate": manual_candidate,
        "updated_scenes": updated_scenes,
    }


@app.post("/api/media-pool/{slug}")
async def upload_media_pool(slug: str, files: list[UploadFile]) -> dict:
    """Recebe o lote de fotos/vídeos do modo de mídia própria (ver
    modules/media_pool.py), ANTES do job existir — escopado por slug (o
    mesmo draftSlug gerado no painel ao escolher a voz), não por job_id."""
    if not files:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")

    saved = []
    for file in files:
        data = await file.read()
        if not data:
            continue
        if len(data) > _MANUAL_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=400, detail=f"{file.filename}: arquivo maior que 200MB."
            )
        result = await asyncio.to_thread(
            media_pool.save_pool_upload, slug, file.filename or "upload", data
        )
        saved.append(result)

    return {"saved": saved, "pool": _media_pool_summary(slug)}


@app.get("/api/media-pool/{slug}")
async def get_media_pool(slug: str) -> dict:
    return _media_pool_summary(slug)


def _media_pool_summary(slug: str) -> dict:
    """`filename` (não só a URL) é o que o editor de timeline manda de volta
    em POST /api/timeline/.../{slot_index} pra dizer qual arquivo do lote
    vai em cada trecho — e `duration` do vídeo poupa o popup de recorte de
    precisar de um ffprobe extra (já é calculado aqui, era só descartado)."""
    pool = media_pool.list_pool(slug)
    return {
        "photos": [
            {"filename": p.name, "url": f"/own_media_cache/{slug}/{p.name}"}
            for p in pool["photos"]
        ],
        "videos": [
            {
                "filename": v.name,
                "url": f"/own_media_cache/{slug}/{v.name}",
                "duration": media_pool._probe_duration(v),
            }
            for v in pool["videos"]
        ],
    }


@app.post("/api/narration-blocks/{slug}/{block_id}/hints")
async def generate_block_hints(slug: str, block_id: int, req: SlotHintsRequest) -> dict:
    """Tradução pra português + dica de mídia por trecho (ver
    modules/timeline.py) — round-trip separado do POST /api/narration-blocks
    porque é chamada de LLM (mais lenta); o painel já mostra a faixa de
    trechos antes disso responder e só preenche tradução/dica quando chegar."""
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Bloco ainda não foi fatiado.")

    beat_text = " ".join(s["text"] for s in manifest)
    hints = await asyncio.to_thread(
        timeline_module.generate_slot_hints, manifest, beat_text, req.language, slug, block_id
    )
    for slot, hint in zip(manifest, hints):
        slot["translation_pt"] = hint["translation_pt"]
        slot["hint"] = hint["hint"]
        slot["image_prompt"] = hint["image_prompt"]
        # Só aplica a classificação da IA se o usuário ainda não decidiu
        # manualmente (ver POST .../needs-media) — sem isso, gerar de novo
        # (ex.: "Regenerar" o bloco) apagaria uma escolha explícita.
        if not slot.get("needs_media_overridden"):
            slot["needs_media"] = hint["needs_media"]
    timeline_module.save_manifest(slug, block_id, manifest)
    return {"slots": manifest}


# Registradas ANTES de /api/timeline/{slug}/{block_id}: rotas são casadas
# na ordem de declaração (mesma forma de rota, 2 segmentos depois de
# /api/timeline/), e "watch-folder" bateria ali como um block_id inválido
# (422) se viesse depois. Um watch por SLUG (não mais por bloco, ver
# webapp/folder_sync.py) — pedido do usuário: gerar vários áudios de uma
# vez e deixar um sincronizador só preencher tudo em sequência, avançando
# de bloco em bloco sozinho.
@app.post("/api/timeline/{slug}/watch-folder")
async def start_watch_folder(slug: str, req: WatchFolderRequest) -> dict:
    """Liga o sincronizador automático (ver webapp/folder_sync.py) pro
    RASCUNHO INTEIRO: a cada arquivo novo na pasta informada, atribui ao
    próximo trecho de mídia única ainda vazio, no primeiro bloco que
    tiver vaga (bloco 1 esgota antes do 2 começar a receber). Não exige
    nenhum bloco já fatiado — pode ligar antes do primeiro áudio existir
    e esperar."""
    folder = Path(req.folder_path).expanduser()
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="Pasta não encontrada nesse caminho.")
    folder_sync.start(slug, folder)
    return folder_sync.status(slug)


@app.post("/api/timeline/{slug}/watch-folder/stop")
async def stop_watch_folder(slug: str) -> dict:
    folder_sync.stop(slug)
    return folder_sync.status(slug)


@app.get("/api/timeline/{slug}/watch-folder")
async def get_watch_folder_status(slug: str) -> dict:
    return folder_sync.status(slug)


@app.get("/api/timeline/{slug}/{block_id}")
async def get_timeline_manifest(slug: str, block_id: int) -> dict:
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Bloco ainda não foi fatiado.")
    return {"slots": manifest}


@app.delete("/api/timeline/{slug}/{block_id}")
async def delete_timeline_manifest(slug: str, block_id: int) -> dict:
    """Remove um bloco do painel: apaga o manifesto em disco também — sem
    isso, o sincronizador de pasta do rascunho inteiro continuaria
    descobrindo o arquivo e oferecendo vaga pra um bloco que o usuário já
    achava excluído (ver webapp/folder_sync.py::_list_block_ids)."""
    timeline_module.delete_manifest(slug, block_id)
    return {"deleted": True}


def _find_pool_file(slug: str, filename: str) -> tuple[Path, str] | tuple[None, None]:
    pool = media_pool.list_pool(slug)
    for p in pool["photos"]:
        if p.name == filename:
            return p, "image"
    for v in pool["videos"]:
        if v.name == filename:
            return v, "video"
    return None, None


@app.post("/api/timeline/{slug}/{block_id}/{slot_index}")
async def assign_timeline_slot(slug: str, block_id: int, slot_index: int, req: AssignSlotRequest) -> dict:
    """Atribui um arquivo já enviado (ver /api/media-pool/{slug}) a uma
    POSIÇÃO dentro de um trecho do editor de timeline manual — trecho com
    efeito de galeria (split_screen etc.) tem mais de uma posição, ver
    timeline_module.EFFECT_CATALOG."""
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Bloco ainda não foi fatiado.")
    if slot_index < 0 or slot_index >= len(manifest):
        raise HTTPException(status_code=404, detail="Trecho não encontrado.")

    slot = manifest[slot_index]
    _, max_media = timeline_module.effect_media_bounds(slot.get("effect", timeline_module.DEFAULT_EFFECT))
    if req.media_index < 0 or req.media_index >= max_media:
        raise HTTPException(status_code=400, detail="Posição de mídia inválida pra este efeito.")

    file_path, media_type = _find_pool_file(slug, req.pool_filename)
    if file_path is None:
        raise HTTPException(status_code=400, detail="Arquivo não encontrado no seu lote de mídia.")

    media: dict = {"pool_filename": req.pool_filename, "media_type": media_type}
    if media_type == "video":
        slot_duration = slot["end_seconds"] - slot["start_seconds"]
        duration = media_pool._probe_duration(file_path)
        if duration is None:
            raise HTTPException(status_code=400, detail="Não consegui medir a duração deste vídeo.")
        start = req.clip_start_seconds or 0.0
        if start < 0 or start > max(0.0, duration - slot_duration) + 0.01:
            raise HTTPException(status_code=400, detail="Início do trecho fora da duração do vídeo.")
        media["clip_start_seconds"] = round(start, 2)

    media_list = slot.setdefault("media", [])
    while len(media_list) <= req.media_index:
        media_list.append(None)
    media_list[req.media_index] = media
    timeline_module.save_manifest(slug, block_id, manifest)
    return {"slot": slot}


@app.delete("/api/timeline/{slug}/{block_id}/{slot_index}")
async def unassign_timeline_slot(slug: str, block_id: int, slot_index: int, media_index: int = 0) -> dict:
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Bloco ainda não foi fatiado.")
    if slot_index < 0 or slot_index >= len(manifest):
        raise HTTPException(status_code=404, detail="Trecho não encontrado.")

    slot = manifest[slot_index]
    media_list = slot.get("media") or []
    if 0 <= media_index < len(media_list):
        media_list[media_index] = None
    timeline_module.save_manifest(slug, block_id, manifest)
    return {"slot": slot}


@app.post("/api/timeline/{slug}/{block_id}/{slot_index}/effect")
async def set_timeline_slot_effect(slug: str, block_id: int, slot_index: int, req: SetSlotEffectRequest) -> dict:
    """Troca o efeito visual escolhido pelo usuário pra este trecho (ver
    timeline_module.EFFECT_CATALOG). Mídia já anexada em posições que não
    existem mais no novo efeito (ex.: trocar de "grade" com 4 fotos pra
    "padrão", que só aceita 1) é descartada — só a atribuição no trecho,
    não o arquivo em si, que continua na biblioteca do usuário."""
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Bloco ainda não foi fatiado.")
    if slot_index < 0 or slot_index >= len(manifest):
        raise HTTPException(status_code=404, detail="Trecho não encontrado.")
    if req.effect not in timeline_module.EFFECT_CATALOG:
        raise HTTPException(status_code=400, detail="Efeito inválido.")

    slot = manifest[slot_index]
    slot["effect"] = req.effect
    _, max_media = timeline_module.effect_media_bounds(req.effect)
    media_list = slot.get("media") or []
    if len(media_list) > max_media:
        slot["media"] = media_list[:max_media]
    timeline_module.save_manifest(slug, block_id, manifest)
    return {"slot": slot}


@app.post("/api/timeline/{slug}/{block_id}/{slot_index}/needs-media")
async def set_timeline_slot_needs_media(
    slug: str, block_id: int, slot_index: int, req: SetSlotNeedsMediaRequest
) -> dict:
    """Override manual da classificação da IA (ver
    modules/timeline.py::generate_slot_hints, campo needs_media) — o
    usuário sempre pode discordar, tanto pra dizer "isso aqui não precisa
    de mídia, deixa virar texto" quanto o contrário. Marca
    needs_media_overridden pra essa escolha sobreviver a um "Regenerar"
    do bloco (ver POST /api/narration-blocks/.../hints)."""
    manifest = timeline_module.load_manifest(slug, block_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Bloco ainda não foi fatiado.")
    if slot_index < 0 or slot_index >= len(manifest):
        raise HTTPException(status_code=404, detail="Trecho não encontrado.")

    slot = manifest[slot_index]
    slot["needs_media"] = req.needs_media
    slot["needs_media_overridden"] = True
    timeline_module.save_manifest(slug, block_id, manifest)
    return {"slot": slot}


@app.post("/api/jobs/{job_id}/footage-candidates/{beat_id}/{slot}/youtube")
async def add_youtube_clip(job_id: str, beat_id: int, slot: int, req: YoutubeClipRequest) -> dict:
    """Baixa um trecho específico (início/fim escolhidos por quem está
    revisando) de um vídeo do YouTube colado na hora, pra usar num shot —
    mesma ideia do upload manual, mas buscando o vídeo em vez de precisar já
    ter o arquivo no PC."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    try:
        clip_candidate = await asyncio.to_thread(
            footage_search.save_youtube_clip, req.url, req.start_seconds, req.end_seconds
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao baixar o trecho do YouTube: {e}")

    review = footage_search.load_candidates_for_review(job.slug, beat_id, slot)
    candidates = [*(review["candidates"] if review else []), clip_candidate]
    chosen_index = len(candidates) - 1

    updated_scenes = _apply_chosen_candidate(
        job, beat_id, slot, candidates, chosen_index, clip_candidate["clip_path"]
    )

    return {
        "beat_id": beat_id,
        "slot": slot,
        "chosen_index": chosen_index,
        "candidate": clip_candidate,
        "updated_scenes": updated_scenes,
    }


@app.post("/api/jobs/{job_id}/confirm-render")
async def confirm_render(job_id: str) -> dict:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if job.status != "awaiting_review":
        raise HTTPException(status_code=400, detail="Job não está aguardando revisão no momento.")
    job.review_event.set()
    return {"status": "ok"}


@app.get("/api/channels")
async def get_channels() -> list[str]:
    return channels_module.list_channels()


@app.post("/api/channels")
async def post_channel(req: ChannelRequest) -> list[str]:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nome do canal vazio.")
    return channels_module.create_channel(name)


@app.get("/api/channels/{name}/favorites")
async def get_favorites(name: str) -> list[dict]:
    return channels_module.get_favorites(name)


@app.post("/api/channels/{name}/favorites")
async def post_favorite(name: str, voice: dict) -> list[dict]:
    if "id" not in voice:
        raise HTTPException(status_code=400, detail="Voz sem id.")
    return channels_module.add_favorite(name, voice)


@app.delete("/api/channels/{name}/favorites/{voice_id}")
async def delete_favorite(name: str, voice_id: str) -> list[dict]:
    return channels_module.remove_favorite(name, voice_id)


def _identity_response(name: str) -> dict:
    identity = channels_module.get_identity(name)
    avatar_url = (
        f"/channel_avatars/{identity['avatar_filename']}"
        if identity["avatar_filename"]
        else None
    )
    return {"handle": identity["handle"], "avatar_url": avatar_url}


@app.get("/api/channels/{name}/identity")
async def get_identity(name: str) -> dict:
    return _identity_response(name)


@app.post("/api/channels/{name}/identity")
async def post_identity(name: str, req: HandleRequest) -> dict:
    channels_module.set_handle(name, req.handle.strip())
    return _identity_response(name)


_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # avatar, não footage — 5MB já é generoso


@app.post("/api/channels/{name}/avatar")
async def post_avatar(name: str, file: UploadFile) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Arquivo maior que 5MB.")

    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Arquivo não é uma imagem válida.")

    ext = Path(file.filename or "").suffix.lower() or ".png"
    filename = f"{hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]}{ext}"
    (CHANNEL_AVATARS_DIR / filename).write_bytes(data)

    channels_module.set_avatar_filename(name, filename)
    return _identity_response(name)


@app.get("/api/serper-status")
async def get_serper_status_route() -> dict:
    return footage_search.get_serper_status()


@app.post("/api/serper-status")
async def post_serper_key(req: SerperKeyRequest) -> dict:
    chave = req.api_key.strip()
    if not chave:
        raise HTTPException(status_code=400, detail="Chave vazia.")

    # valida ANTES de salvar: sem isso, um typo trocaria a chave boa por uma
    # inválida e a fonte só voltaria a falhar silenciosamente no próximo vídeo
    try:
        resp = requests.get(
            footage_search.SERPER_ACCOUNT_URL, headers={"X-API-KEY": chave}, timeout=10
        )
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Não consegui checar a chave (rede).")
    if resp.status_code == 403:
        raise HTTPException(status_code=400, detail="Chave inválida (a Serper recusou).")
    resp.raise_for_status()

    settings_module.set_serper_api_key(chave)
    return footage_search.get_serper_status()
