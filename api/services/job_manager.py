from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from api.services.db import get_pool

logger = logging.getLogger(__name__)

INPUT_DIR = Path(os.environ.get("INPUT_DIR", str(Path(__file__).resolve().parent.parent.parent / "inputs")))


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
    note: str = ""
    file_type: str = "image"


async def create_job(
    file_source: str,
    file_path: str,
    skip_exif: bool = False,
    skip_faces: bool = False,
    insert: bool = True,
    note: str = "",
    file_type: str = "image",
) -> Job:
    """Create a processing job for a file, deduplicating on file_source.

    Re-uploading a file that already has a successful (or still-running) job
    must not spawn a second job — otherwise the second job re-runs the whole
    pipeline (EXIF entities, relations, VLM) against an already-ingested
    photo, producing the flood of LightRAG "already exists" 400s and leaving
    duplicate job rows.  If the most recent job for this file_source is
    complete or in-flight, return it.  Only failed/cancelled jobs allow a
    fresh job (legitimate retry).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM jobs WHERE file_source = $1 ORDER BY created_at DESC LIMIT 1",
            file_source,
        )

        if existing and existing["status"] in ("complete", "processing", "pending"):
            return Job(**dict(existing))

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
        note=note,
        file_type=file_type,
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO jobs (id, file_source, file_path, status, stage, error, skip_exif, skip_faces, insert, created_at, updated_at, note, file_type)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)""",
            job.id, job.file_source, job.file_path, job.status, job.stage,
            job.error, job.skip_exif, job.skip_faces, job.insert,
            job.created_at, job.updated_at, job.note, job.file_type,
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
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE jobs SET status = $2, stage = $3, error = $4, updated_at = $5 WHERE id = $1""",
            job_id, status, stage, error, time.time(),
        )


async def store_event(job_id: str, event_type: str, event_data: dict) -> None:
    """Persist a job event to ``job_events`` and ``pg_notify`` a per-job channel.

    Used by BOTH the API process (legacy sync endpoints) and the worker
    process. The NOTIFY lets the API's SSE endpoint (which LISTENs on
    ``job_events_{job_id}``) stream the event to connected clients in real
    time without any in-process queue. The INSERT + NOTIFY run on the same
    connection so they are atomic from the listener's perspective.
    """
    if isinstance(event_data, str):
        try:
            event_data = json.loads(event_data)
        except (json.JSONDecodeError, TypeError):
            event_data = {"raw": event_data}

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO job_events (job_id, event_type, event_data, created_at)
               VALUES ($1, $2, $3::jsonb, $4) RETURNING id""",
            job_id, event_type, json.dumps(event_data), time.time(),
        )
        event_id = row["id"] if row else None
        channel = f"job_events_{job_id}"
        payload = json.dumps({"id": event_id, "job_id": job_id, "event_type": event_type})
        await conn.execute("SELECT pg_notify($1, $2)", channel, payload)


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