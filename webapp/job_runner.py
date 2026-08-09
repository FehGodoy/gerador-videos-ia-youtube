"""
Orquestra os jobs do painel web: recebe roteiro + voz escolhida, roda o
pipeline em background reaproveitando os módulos já existentes
(script_parser, composition_builder, renderer — nenhum deles foi reescrito,
só ganharam callbacks opcionais de progresso), e transmite o progresso por
uma fila de eventos por job, consumida via SSE em webapp/server.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.composition_builder import build_composition
from modules.config import cache_dir, output_dir
from modules.renderer import render_with_remotion
from modules.script_parser import parse_script

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    slug: str
    beats: list[dict]
    status: str = "running"  # running | done | error
    video_path: Path | None = None
    error: str | None = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


def _write_temp_script(slug: str, script_text: str) -> Path:
    path = cache_dir("web_scripts") / f"{slug}.md"
    path.write_text(script_text, encoding="utf-8")
    return path


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def create_job(self, script_text: str, voice_id: str, language: str) -> Job:
        # slug depende do texto E da voz: se o usuário trocar a voz e regerar
        # o mesmo roteiro, não queremos reaproveitar narração cacheada da
        # voz antiga (cache/narration/<slug>/ ficaria com áudio da voz errada).
        digest = hashlib.sha1(f"{script_text}|{voice_id}".encode("utf-8")).hexdigest()[:10]
        slug = f"web-{digest}"

        script_path = _write_temp_script(slug, script_text)
        beats = parse_script(script_path)

        job = Job(id=uuid.uuid4().hex[:12], slug=slug, beats=[b.to_dict() for b in beats])
        self._jobs[job.id] = job

        asyncio.create_task(self._run(job, script_path, voice_id, language))
        return job

    async def _run(self, job: Job, script_path: Path, voice_id: str, language: str) -> None:
        loop = asyncio.get_running_loop()

        def emit(event: str, data: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(job.queue.put_nowait, {"event": event, "data": data})

        def on_beat_progress(beat_id: int, stage: str, status: str) -> None:
            emit("beat_progress", {"beat_id": beat_id, "stage": stage, "status": status})

        def on_render_progress(frame: int, total: int) -> None:
            emit("render_progress", {"frame": frame, "total": total})

        try:
            await asyncio.to_thread(
                build_composition,
                str(script_path),
                job.slug,
                on_beat_progress=on_beat_progress,
                voice_id=voice_id,
                language=language,
            )
            composition_path = output_dir(job.slug) / "composition.json"

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
