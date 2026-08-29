"""
Orquestra os jobs do painel web: recebe os blocos do roteiro (já narrados
individualmente pela fila de blocos, ver webapp/server.py:/api/narration-blocks)
mais a voz escolhida, roda o resto do pipeline em background reaproveitando os
módulos já existentes (composition_builder, renderer — nenhum deles foi
reescrito, só ganharam callbacks opcionais de progresso), e transmite o
progresso por uma fila de eventos por job, consumida via SSE em
webapp/server.py.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.composition_builder import build_composition_from_beats
from modules.config import output_dir
from modules.github_render import render_via_github_actions
from modules.renderer import render_with_remotion
from modules.script_parser import Beat

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    slug: str
    beats: list[dict]
    status: str = "running"  # running | awaiting_review | done | error
    video_path: Path | None = None
    error: str | None = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    # setado por webapp/server.py:/api/jobs/{id}/confirm-render quando o
    # usuário termina de revisar o footage escolhido pra cada beat (ou aceita
    # a escolha da IA sem mexer) — _run fica esperando isso antes de renderizar
    review_event: asyncio.Event = field(default_factory=asyncio.Event)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def create_job(
        self,
        slug: str,
        beats: list[Beat],
        voice_id: str,
        language: str,
        speed: float,
        remote: bool = True,
        allowed_sources: list[str] | None = None,
        google_images_recency: str | None = None,
        media_mode: str = "ai_search",
        subscribe_identity: dict | None = None,
    ) -> Job:
        # o slug do rascunho já vem do painel web (gerado quando a voz foi
        # escolhida) — os blocos já foram narrados individualmente sob esse
        # mesmo slug pela fila de blocos, então build_narration só vai achar
        # tudo em cache e recombinar, sem chamar a Cartesia de novo aqui.
        # voice_id/language/speed só entram em jogo de fato se o cache tiver
        # sido limpo entre a fila e a criação do job.
        job = Job(id=uuid.uuid4().hex[:12], slug=slug, beats=[b.to_dict() for b in beats])
        self._jobs[job.id] = job

        asyncio.create_task(
            self._run(
                job, beats, voice_id, language, speed, remote,
                allowed_sources, google_images_recency, media_mode,
                subscribe_identity,
            )
        )
        return job

    async def _run(
        self,
        job: Job,
        beats: list[Beat],
        voice_id: str,
        language: str,
        speed: float,
        remote: bool,
        allowed_sources: list[str] | None = None,
        google_images_recency: str | None = None,
        media_mode: str = "ai_search",
        subscribe_identity: dict | None = None,
    ) -> None:
        loop = asyncio.get_running_loop()

        def emit(event: str, data: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(job.queue.put_nowait, {"event": event, "data": data})

        def on_beat_progress(beat_id: int, stage: str, status: str) -> None:
            emit("beat_progress", {"beat_id": beat_id, "stage": stage, "status": status})

        def on_render_progress(frame: int, total: int) -> None:
            emit("render_progress", {"frame": frame, "total": total})

        def on_render_status(message: str) -> None:
            emit("render_status", {"message": message})

        try:
            await asyncio.to_thread(
                build_composition_from_beats,
                beats,
                job.slug,
                on_beat_progress=on_beat_progress,
                voice_id=voice_id,
                language=language,
                speed=speed,
                allowed_sources=allowed_sources,
                google_images_recency=google_images_recency,
                media_mode=media_mode,
                subscribe_identity=subscribe_identity,
            )
            composition_path = output_dir(job.slug) / "composition.json"

            job.status = "awaiting_review"
            emit("composition_ready", {})
            await job.review_event.wait()
            job.status = "running"

            if remote:
                video_path = await asyncio.to_thread(
                    render_via_github_actions, composition_path, job.slug, on_status=on_render_status
                )
            else:
                video_path = await asyncio.to_thread(
                    render_with_remotion, composition_path, job.slug, on_progress=on_render_progress
                )
            job.video_path = video_path
            job.status = "done"
            emit("job_done", {"video_url": f"/api/jobs/{job.id}/video"})
        except Exception as e:
            logger.exception("Job %s falhou", job.id)
            job.status = "error"
            job.error = str(e)
            emit("job_error", {"message": str(e)})


job_manager = JobManager()
