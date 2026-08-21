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

import requests
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from modules import footage_search, github_render, media_pool, settings as settings_module
from modules.composition_builder import validate_composition
from modules.config import PROJECT_ROOT, cache_dir, load_config, output_dir
from modules.narration import synthesize_beat
from modules.script_parser import Beat
from webapp import channels as channels_module
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


class ChannelRequest(BaseModel):
    name: str


class FootageChoiceRequest(BaseModel):
    candidate_index: int


class YoutubeClipRequest(BaseModel):
    url: str
    start_seconds: float
    end_seconds: float


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

    return {
        "block_id": req.block_id,
        "duration_seconds": result["duration_seconds"],
        "audio_url": f"/narration_cache/{req.slug}/beat_{req.block_id:03d}.wav",
        "captions": result["captions"],
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

    # Falha cedo: sem isso, um gh sem login só estourava lá na frente, depois
    # da revisão manual de footage inteira — e jogava esse trabalho fora.
    if req.remote_render:
        auth_error = await asyncio.to_thread(github_render.check_auth)
        if auth_error:
            raise HTTPException(status_code=400, detail=auth_error)

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
    updated_scenes = 0
    for beat_entry in composition["beats"]:
        if beat_entry["id"] != beat_id:
            continue
        for scene in beat_entry.get("scenes", []):
            if scene.get("shot_slot") != slot:
                continue
            scene["kind"] = "footage"
            scene["footage"] = {
                "clip_path": relative_path,
                "source": chosen["source"],
                "media_type": chosen.get("media_type", "video"),
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
        result.append(
            {
                "beat_id": beat["id"],
                "text": beat["text"],
                "entities": entities_by_beat.get(beat["id"], []),
                "shots": shots,
                "concept_cards": cards,
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
    pool = media_pool.list_pool(slug)
    return {
        "photos": [
            f"/own_media_cache/{slug}/{p.name}" for p in pool["photos"]
        ],
        "videos": [
            f"/own_media_cache/{slug}/{v.name}" for v in pool["videos"]
        ],
    }


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
