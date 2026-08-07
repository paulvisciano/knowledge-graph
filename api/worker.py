"""Worker process entrypoint for the Knowledge Graph image pipeline.

Runs as a separate process (``python -m api.worker``) alongside the FastAPI
API. The worker owns ALL image processing so the API's event loop / thread
pool is never touched by processor I/O (LightRAG polling, VLM calls, face
detection subprocesses), which previously saturated the shared default
ThreadPoolExecutor and made the website unresponsive while a batch ran.

Responsibilities:
  * ``init_db`` — ensures the jobs/job_events tables exist (idempotent, same
    as the API process).
  * ``resume_pending_jobs`` — on startup, reclaim jobs left pending or
    interrupted by a prior crash and drive them through the two-phase
    coordinator.
  * Poll loop — every ~1s, atomically claim ``status='pending'`` rows with
    ``SELECT ... FOR UPDATE SKIP LOCKED``, flip each to ``processing``/
    ``claimed``, and start a processing task for it. Capped by a semaphore.
  * Overnight VLM batch scheduler — fires ``run_overnight_vlm_batch`` at the
    configured hour (moved here from the API).
  * LISTENs on the ``worker_control`` Postgres channel for manual triggers
    from the API's ``/images/jobs/process-ai-queue`` endpoint and runs the
    overnight batch on signal.
  * Graceful SIGTERM/SIGINT handling — cancels in-flight tasks and closes the
    pool.

Message bus = Postgres. The ``jobs`` table is the durable work queue;
``job_events`` + ``pg_notify`` (emitted by ``store_event``) is the live event
stream the API's SSE endpoints LISTEN on. No Redis, no Celery.
"""

from __future__ import annotations

import asyncio
import asyncpg
import datetime
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

from api.services import config
from api.services import db as db_module
from api.services.db import get_pool
from api.services.job_manager import (
    Job,
    get_events,
    get_job,
    store_event,
    update_job_status,
)
from api.services.phases import (
    run_batch_phases,
    run_overnight_vlm_batch,
    start_processing,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")


# How many pending rows to claim per poll iteration.
CLAIM_BATCH_SIZE = int(os.environ.get("WORKER_CLAIM_BATCH_SIZE", "5"))

# Cap on simultaneously in-flight claimed jobs. Phase-1 (EXIF) concurrency is
# further bounded inside phases.py by _exif_semaphore; this caps the total
# number of jobs the worker has started but not yet finished (any phase).
_claim_semaphore = asyncio.Semaphore(int(os.environ.get("MAX_CONCURRENT_JOBS", "3")))

# A job whose updated_at hasn't moved in this long is assumed stuck (crashed
# worker) and marked failed on resume.
_STUCK_JOB_TIMEOUT_SECONDS = 48 * 60 * 60  # 48 hours


async def resume_pending_jobs() -> list[str]:
    """Reclaim jobs left pending or interrupted by a prior worker crash.

    Adapted from the old api.services.job_manager.resume_pending_jobs: it now
    runs IN the worker process, so asyncio.create_task(run_batch_phases(...))
    and start_processing(...) target the worker's own event loop / task set.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM jobs WHERE status IN ('pending', 'processing') ORDER BY created_at")
    resumed = []
    now = time.time()

    phase1_jobs: list[Job] = []
    phase2_jobs: list[Job] = []

    for row in rows:
        job = Job(**dict(row))

        if job.status == "pending":
            logger.info("Resuming pending job %s for %s (phase 1)", job.id, job.file_source)
            phase1_jobs.append(job)
            resumed.append(job.id)
            continue

        if (now - job.updated_at) > _STUCK_JOB_TIMEOUT_SECONDS:
            logger.warning(
                "Marking stuck job %s as failed (no update for %.0f minutes)",
                job.id, (now - job.updated_at) / 60,
            )
            await update_job_status(job.id, "failed", "error", "Job stuck: no progress for 30 minutes")
            continue

        if not Path(job.file_path).exists():
            await update_job_status(job.id, "failed", "error", "File no longer exists")
            continue

        prior_events = await get_events(job.id)
        prior_types = {e["event_type"] for e in prior_events}

        if job.stage == "exif_complete" or "exif_phase_complete" in prior_types:
            if config.vlm_batch_enabled():
                logger.info("Job %s for %s at exif_complete — leaving for overnight/manual VLM batch", job.id, job.file_source)
                resumed.append(job.id)
                continue
            logger.info("Resuming job %s for %s at phase 2 (phase 1 already complete)", job.id, job.file_source)
            phase2_jobs.append(job)
            resumed.append(job.id)
            continue

        if job.stage in ("extracting_metadata", "creating_entities", "starting", ""):
            if "detecting_faces" in prior_types and "faces_complete" not in prior_types:
                if not job.skip_faces:
                    job.skip_faces = True
                    logger.warning(
                        "Job %s was interrupted during face detection — skipping faces on resume",
                        job.id,
                    )
                await store_event(job.id, "faces_complete", {"faces": {}, "resumed": True})
            logger.info("Re-running interrupted job %s for %s (phase 1 restart)", job.id, job.file_source)
            phase1_jobs.append(job)
            resumed.append(job.id)
            continue

        if job.stage in ("processing_ai", "linking_entities"):
            logger.info("Resuming job %s for %s at phase 2 (was mid-AI, restarting phase 2)", job.id, job.file_source)
            phase2_jobs.append(job)
            resumed.append(job.id)
            continue

        logger.info("Resuming job %s for %s with unknown stage %r (phase 1 restart)", job.id, job.file_source, job.stage)
        phase1_jobs.append(job)
        resumed.append(job.id)

    if phase1_jobs:
        asyncio.create_task(run_batch_phases(phase1_jobs))
    if phase2_jobs:
        for job in phase2_jobs:
            await start_processing(job, phase="ai")

    return resumed


async def _claim_pending_jobs() -> None:
    """One poll iteration: claim up to CLAIM_BATCH_SIZE pending jobs and start them."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, file_source, file_path, skip_exif, skip_faces, insert, note
                   FROM jobs WHERE status = 'pending'
                   ORDER BY created_at
                   LIMIT $1
                   FOR UPDATE SKIP LOCKED""",
                CLAIM_BATCH_SIZE,
            )
            if not rows:
                return
            # Flip each claimed row to processing/claimed, but only if it's
            # still pending (guards against races with the resume path or a
            # concurrent worker — though SKIP LOCKED already prevents two
            # workers claiming the same row, this UPDATE is belt-and-suspenders).
            for row in rows:
                await conn.execute(
                    """UPDATE jobs SET status = 'processing', stage = 'claimed', updated_at = $2
                       WHERE id = $1 AND status = 'pending'""",
                    row["id"], time.time(),
                )

    for row in rows:
        job = Job(
            id=row["id"],
            file_source=row["file_source"],
            file_path=row["file_path"],
            status="processing",
            stage="claimed",
            skip_exif=row["skip_exif"],
            skip_faces=row["skip_faces"],
            insert=row["insert"],
            note=row["note"] or "",
        )
        await _claim_semaphore.acquire()
        t = asyncio.create_task(_run_claimed(job))
        _claim_tasks.add(t)
        t.add_done_callback(_claim_tasks.discard)


_claim_tasks: set[asyncio.Task] = set()


async def _run_claimed(job: Job) -> None:
    """Run a claimed job through the pipeline, releasing the claim semaphore on exit.

    When VLM batch mode is enabled (the default), only phase 1 (EXIF extraction
    + entity creation) runs automatically so images sit at ``exif_complete``
    until the user triggers AI processing via the "Process AI Queue" button or
    the overnight scheduler fires. When batch mode is off, both phases run
    immediately (legacy behavior).
    """
    phase = "exif" if config.vlm_batch_enabled() else "both"
    try:
        await start_processing(job, phase=phase)
    finally:
        _claim_semaphore.release()


async def _poll_loop() -> None:
    while True:
        try:
            await _claim_pending_jobs()
        except Exception:
            logger.exception("Poll loop iteration failed")
        await asyncio.sleep(1.0)


async def _vlm_batch_scheduler() -> None:
    """Background loop that fires the overnight VLM batch at the configured hour."""
    while True:
        now = datetime.datetime.now()
        target_hour = config.vlm_batch_start_hour()
        if target_hour < 0:
            await asyncio.sleep(3600)
            continue

        next_run = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        delay = (next_run - now).total_seconds()
        logger.info("VLM batch scheduler: next run at %s (in %.0f min)", next_run.strftime("%Y-%m-%d %H:%M"), delay / 60)
        await asyncio.sleep(delay)
        try:
            count = await run_overnight_vlm_batch()
            logger.info("VLM batch scheduler: processed %d job(s)", count)
        except Exception:
            logger.exception("VLM batch scheduler failed")


async def _control_listener() -> None:
    """LISTEN on worker_control for manual triggers from the API.

    The API's /images/jobs/process-ai-queue endpoint sends
    {"action": "run_overnight_vlm_batch"} here; we run the batch on receipt.
    """
    pool = await get_pool()
    conn = await asyncpg.connect(db_module.DATABASE_URL)
    trigger_queue: asyncio.Queue = asyncio.Queue()

    def _on_control(c, pid, channel, payload):
        try:
            trigger_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    try:
        await conn.add_listener("worker_control", _on_control)
        while True:
            payload = await trigger_queue.get()
            try:
                msg = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                msg = {}
            action = msg.get("action")
            if action == "run_overnight_vlm_batch":
                try:
                    count = await run_overnight_vlm_batch()
                    logger.info("Manual VLM batch (worker_control signal): processed %d job(s)", count)
                except Exception:
                    logger.exception("Manual VLM batch failed")
            else:
                logger.warning("Unknown worker_control action: %r", action)
    finally:
        try:
            await conn.remove_listener("worker_control", _on_control)
        except Exception:
            pass
        try:
            await conn.close()
        except Exception:
            pass


async def main() -> None:
    logger.info("Knowledge Graph worker starting up")
    await db_module.init_db()
    resumed = await resume_pending_jobs()
    if resumed:
        logger.info("Resumed %d pending/interrupted jobs", len(resumed))

    poll_task = asyncio.create_task(_poll_loop())
    control_task = asyncio.create_task(_control_listener())
    scheduler_task: asyncio.Task | None = None
    if config.vlm_batch_enabled():
        scheduler_task = asyncio.create_task(_vlm_batch_scheduler())

    stop_event = asyncio.Event()

    def _stop(*_: Any) -> None:
        logger.info("Worker received stop signal, shutting down")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            # add_signal_handler is unavailable on some platforms (e.g.
            # Windows); fall back to the default signal handling.
            signal.signal(sig, _stop)

    await stop_event.wait()

    for t in (poll_task, control_task):
        t.cancel()
    if scheduler_task is not None:
        scheduler_task.cancel()
    for t in list(_claim_tasks):
        t.cancel()
    await asyncio.gather(
        poll_task, control_task,
        scheduler_task if scheduler_task is not None else asyncio.sleep(0),
        *list(_claim_tasks),
        return_exceptions=True,
    )
    await db_module.close_db()
    logger.info("Knowledge Graph worker shut down")


if __name__ == "__main__":
    asyncio.run(main())