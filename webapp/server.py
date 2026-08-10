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

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from modules import footage_search
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


class ChannelRequest(BaseModel):
    name: str


class FootageChoiceRequest(BaseModel):
    candidate_index: int


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


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

    beats = [Beat(id=b.id, text=b.text) for b in req.blocks]
    job = job_manager.create_job(
        req.slug, beats, req.voice_id, req.language, req.speed, remote=req.remote_render
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


def _relative_clip_path(candidate: dict) -> str | None:
    """Caminho do candidato como aparece no composition.json (relativo à raiz),
    ou None se ele ainda não foi baixado."""
    absolute = footage_search.candidate_clip_path(candidate)
    if absolute is None:
        return None
    return Path(absolute).resolve().relative_to(PROJECT_ROOT).as_posix()


def _scene_summary(scenes: list[dict], clip_path: str) -> dict:
    """Quantas cenas do beat usam esse clipe e quanto tempo de tela ele ocupa
    — é o que deixa claro na revisão o peso real de cada escolha."""
    matching = [s for s in scenes if (s.get("footage") or {}).get("clip_path") == clip_path]
    return {
        "scene_count": len(matching),
        "screen_seconds": round(
            sum(s["end_seconds"] - s["start_seconds"] for s in matching), 1
        ),
    }


@app.get("/api/jobs/{job_id}/footage-candidates")
async def get_footage_candidates(job_id: str) -> list[dict]:
    """Um beat longo é preenchido por vários shots, cada um com sua própria
    lista de candidatos — a revisão é por (beat, shot), não por beat."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")

    composition_path = output_dir(job.slug) / "composition.json"
    scenes_by_beat: dict[int, list[dict]] = {}
    if composition_path.exists():
        composition = json.loads(composition_path.read_text(encoding="utf-8"))
        scenes_by_beat = {b["id"]: b.get("scenes", []) for b in composition["beats"]}

    result = []
    for beat in job.beats:
        beat_scenes = scenes_by_beat.get(beat["id"], [])
        shots = []
        slot = 0
        while True:
            review = footage_search.load_candidates_for_review(job.slug, beat["id"], slot)
            if review is None:
                break
            chosen = review["candidates"][review["chosen_index"]] if review["candidates"] else None
            chosen_path = _relative_clip_path(chosen) if chosen else None
            shots.append(
                {
                    "slot": slot,
                    "candidates": review["candidates"],
                    "chosen_index": review["chosen_index"],
                    "usage": _scene_summary(beat_scenes, chosen_path) if chosen_path else None,
                }
            )
            slot += 1
        result.append({"beat_id": beat["id"], "text": beat["text"], "shots": shots})
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

    previous_path = _relative_clip_path(review["candidates"][review["chosen_index"]])
    chosen = review["candidates"][req.candidate_index]
    try:
        clip_path = await asyncio.to_thread(footage_search.download_candidate, chosen)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao baixar o candidato escolhido: {e}")

    footage_search.save_candidates_for_review(
        job.slug, beat_id, review["candidates"], req.candidate_index, slot=slot
    )

    # Troca o clipe em todas as cenas do beat que usavam o candidato anterior
    # deste shot — é o composition.json em disco que o render (local ou
    # GitHub) de fato lê depois da confirmação.
    composition_path = output_dir(job.slug) / "composition.json"
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    relative_path = Path(clip_path).resolve().relative_to(PROJECT_ROOT).as_posix()
    updated_scenes = 0
    for beat_entry in composition["beats"]:
        if beat_entry["id"] != beat_id:
            continue
        for scene in beat_entry.get("scenes", []):
            footage = scene.get("footage") or {}
            if footage.get("clip_path") != previous_path:
                continue
            scene["footage"] = {
                "clip_path": relative_path,
                "source": chosen["source"],
                "media_type": chosen.get("media_type", "video"),
                "search_terms": footage.get("search_terms", []),
            }
            # o offset era calculado pra duração do clipe antigo; zera pra não
            # começar depois do fim de um clipe mais curto (tela preta)
            scene["clip_start_seconds"] = 0.0
            updated_scenes += 1
        break
    validate_composition(composition)
    composition_path.write_text(json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "beat_id": beat_id,
        "slot": slot,
        "chosen_index": req.candidate_index,
        "source": chosen["source"],
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
