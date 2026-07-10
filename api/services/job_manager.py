from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, AsyncGenerator

from api.services.db import get_pool

logger = logging.getLogger(__name__)

INPUT_DIR = Path(os.environ.get("INPUT_DIR", str(Path(__file__).resolve().parent.parent.parent / "inputs")))

MAX_CONCURRENT_JOBS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

_running_tasks: dict[str, asyncio.Task] = {}
_event_queues: dict[str, asyncio.Queue] = {}


@dataclass
class Job:
    id: str
    file_source: str
    file_path: str
    status: str = "pending"
    stage: str = ""
    error: str = ""
    skip_exif: bool = False
    skip_faces: bool = False
    insert: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0


async def create_job(
    file_source: str,
    file_path: str,
    skip_exif: bool = False,
    skip_faces: bool = False,
    insert: bool = True,
) -> Job:
    import time

    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    job = Job(
        id=job_id,
        file_source=file_source,
        file_path=file_path,
        status="pending",
        skip_exif=skip_exif,
        skip_faces=skip_faces,
        insert=insert,
        created_at=now,
        updated_at=now,
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO jobs (id, file_source, file_path, status, stage, error, skip_exif, skip_faces, insert, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            job.id, job.file_source, job.file_path, job.status, job.stage,
            job.error, job.skip_exif, job.skip_faces, job.insert,
            job.created_at, job.updated_at,
        )
    return job


async def get_job(job_id: str) -> Job | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    if not row:
        return None
    return Job(**dict(row))


async def list_jobs(status: str | None = None) -> list[Job]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch("SELECT * FROM jobs WHERE status = $1 ORDER BY created_at DESC", status)
        else:
            rows = await conn.fetch("SELECT * FROM jobs ORDER BY created_at DESC")
    return [Job(**dict(r)) for r in rows]


async def update_job_status(job_id: str, status: str, stage: str = "", error: str = "") -> None:
    import time

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE jobs SET status = $2, stage = $3, error = $4, updated_at = $5 WHERE id = $1""",
            job_id, status, stage, error, time.time(),
        )


async def store_event(job_id: str, event_type: str, event_data: dict) -> None:
    import time

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO job_events (job_id, event_type, event_data, created_at) VALUES ($1, $2, $3, $4)""",
            job_id, event_type, json.dumps(event_data), time.time(),
        )
    q = _event_queues.get(job_id)
    if q:
        await q.put({"event_type": event_type, "event_data": event_data})


async def get_events(job_id: str, after_event_id: int = 0) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if after_event_id > 0:
            rows = await conn.fetch(
                "SELECT id, event_type, event_data, created_at FROM job_events WHERE job_id = $1 AND id > $2 ORDER BY id",
                job_id, after_event_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, event_type, event_data, created_at FROM job_events WHERE job_id = $1 ORDER BY id",
                job_id,
            )
    return [dict(r) for r in rows]


async def delete_job(job_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM jobs WHERE id = $1", job_id)
    return result == "DELETE 1"


def subscribe_events(job_id: str) -> asyncio.Queue:
    if job_id not in _event_queues:
        _event_queues[job_id] = asyncio.Queue(maxsize=1000)
    return _event_queues[job_id]


def unsubscribe_events(job_id: str) -> None:
    _event_queues.pop(job_id, None)


async def persist_uploaded_file(upload_file_path: str, original_filename: str) -> str:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = INPUT_DIR / original_filename
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = INPUT_DIR / f"{stem}_{counter}{suffix}"
            counter += 1
    shutil.move(upload_file_path, str(dest))
    return str(dest)


async def start_processing(job: Job) -> None:
    task = asyncio.create_task(_run_job(job))
    _running_tasks[job.id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(job.id, None))


async def _run_job(job: Job) -> None:
    from api.routes.images import _process_and_stream
    from sse_starlette.sse import ServerSentEvent

    async with _semaphore:
        try:
            await update_job_status(job.id, "processing", "starting")
            event_generator = _process_and_stream(
                job.file_path,
                job.file_source,
                job.skip_exif,
                job.skip_faces,
                job.insert,
            )
            async for sse_event in event_generator:
                if not sse_event.data:
                    continue
                try:
                    payload = json.loads(sse_event.data)
                    event_name = payload.get("event", "")
                    event_data = payload.get("data", {})
                    stage = _map_event_to_stage(event_name)
                    if stage:
                        await update_job_status(job.id, "processing", stage)
                    await store_event(job.id, event_name, event_data if isinstance(event_data, dict) else {"raw": event_data})
                except (json.JSONDecodeError, TypeError):
                    await store_event(job.id, "raw", {"data": sse_event.data})
            await update_job_status(job.id, "complete", "pipeline_complete")
        except asyncio.CancelledError:
            await update_job_status(job.id, "cancelled", "cancelled")
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            await update_job_status(job.id, "failed", "error", str(exc))
            await store_event(job.id, "pipeline_failed", {"error": str(exc)})
        finally:
            _event_queues.pop(job.id, None)


def _map_event_to_stage(event_name: str) -> str:
    if event_name in ("extracting_exif", "exif_complete", "detecting_faces", "faces_complete", "captions_built", "exif_dimensions_ready"):
        return "extracting_metadata"
    if event_name in ("injecting_exif_relations", "creating_exif_entities", "photo_node_created", "exif_node_created", "exif_relation_created", "exif_entities_complete"):
        return "creating_entities"
    if event_name in ("describing_image", "upload_complete", "lightrag_upload_complete", "lightrag_processing_waiting"):
        return "processing_ai"
    if event_name in ("lightrag_processing_complete",) or event_name.startswith("visual_"):
        return "linking_entities"
    if event_name == "pipeline_complete":
        return "complete"
    if event_name.endswith("_failed") or event_name.endswith("_error") or event_name.endswith("_timeout"):
        return "error"
    return ""


async def resume_pending_jobs() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM jobs WHERE status IN ('pending', 'processing') ORDER BY created_at")
    resumed = []
    for row in rows:
        job = Job(**dict(row))
        if job.status == "pending":
            logger.info("Resuming pending job %s for %s", job.id, job.file_source)
            await start_processing(job)
            resumed.append(job.id)
        elif job.status == "processing":
            if not Path(job.file_path).exists():
                await update_job_status(job.id, "failed", "error", "File no longer exists")
                continue
            logger.info("Re-running interrupted job %s for %s", job.id, job.file_source)
            await start_processing(job)
            resumed.append(job.id)
    return resumed