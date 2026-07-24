from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, AsyncGenerator

from api.services.db import get_pool

logger = logging.getLogger(__name__)

INPUT_DIR = Path(os.environ.get("INPUT_DIR", str(Path(__file__).resolve().parent.parent.parent / "inputs")))

MAX_CONCURRENT_JOBS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Phase 1 (EXIF + faces + entity creation) is CPU/IO bound, not LLM bound, so
# it can run with much higher concurrency than phase 2. Face detection runs in
# a subprocess with its own memory footprint, so 10 is a deliberate cap that
# keeps peak RSS under the container mem_limit while still parallelizing the
# slow EXIF/entity-creation wave across a batch upload.
MAX_EXIF_CONCURRENT = 10
_exif_semaphore = asyncio.Semaphore(MAX_EXIF_CONCURRENT)

_running_tasks: dict[str, asyncio.Task] = {}
_event_queues: dict[str, asyncio.Queue] = {}

# Debounced batch coordinator trigger. When /images/jobs is called repeatedly
# in quick succession (batch upload), each call enqueues its job and (re)arms a
# short timer; when the timer fires we run run_batch_phases over all jobs that
# were still pending at that point. This coalesces a burst of uploads into one
# two-phase batch so phase 1 runs for all of them before any phase 2 starts.
_batch_timer: asyncio.TimerHandle | None = None
_batch_lock = asyncio.Lock()


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


async def create_job(
    file_source: str,
    file_path: str,
    skip_exif: bool = False,
    skip_faces: bool = False,
    insert: bool = True,
    note: str = "",
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
    import time

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
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO jobs (id, file_source, file_path, status, stage, error, skip_exif, skip_faces, insert, created_at, updated_at, note)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
            job.id, job.file_source, job.file_path, job.status, job.stage,
            job.error, job.skip_exif, job.skip_faces, job.insert,
            job.created_at, job.updated_at, job.note,
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

    if isinstance(event_data, str):
        try:
            event_data = json.loads(event_data)
        except (json.JSONDecodeError, TypeError):
            event_data = {"raw": event_data}

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO job_events (job_id, event_type, event_data, created_at) VALUES ($1, $2, $3::jsonb, $4)""",
            job_id, event_type, json.dumps(event_data), time.time(),
        )
    q = _event_queues.get(job_id)
    if q is not None:
        try:
            q.put_nowait({"event_type": event_type, "event_data": event_data})
        except asyncio.QueueFull:
            dropped = q.get_nowait()
            logger.warning(
                "SSE event queue full for job %s (slow client?) — dropping oldest event %r to make room",
                job_id, dropped.get("event_type"),
            )
            q.put_nowait({"event_type": event_type, "event_data": event_data})


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


async def start_processing(job: Job, phase: str = "both") -> None:
    """Start a job (or one phase of it) as a background task.

    ``phase``:
      - ``"both"``: run phase 1 then phase 2 sequentially for this one job
        (single-image upload / reprocess path).
      - ``"exif"``: run only phase 1, then leave the job in ``exif_complete``
        stage for the batch coordinator to pick up for phase 2.
      - ``"ai"``: run only phase 2 (resume after phase 1 already finished).
    """
    task = asyncio.create_task(_run_job(job, phase))
    _running_tasks[job.id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(job.id, None))


async def _run_job(job: Job, phase: str = "both") -> None:
    # Phase 1 is CPU/IO bound and runs with high concurrency (_exif_semaphore,
    # 10). Phase 2 is LLM-bound and runs with the regular _semaphore (3). The
    # "both" path (single image) is bounded by the stricter phase-2 semaphore
    # since it runs both phases back-to-back for one job.
    sem = _exif_semaphore if phase == "exif" else _semaphore
    async with sem:
        try:
            await update_job_status(job.id, "processing", "starting")
            if phase == "exif":
                event_generator = _phase1_generator(job)
                await _drain_generator(job, event_generator, phase)
            elif phase == "ai":
                event_generator = _phase2_generator(job)
                await _drain_generator(job, event_generator, phase)
            else:
                event_generator = _both_phase_generator(job)
                await _drain_generator(job, event_generator, "both")
        except asyncio.CancelledError:
            await update_job_status(job.id, "cancelled", "cancelled")
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            await update_job_status(job.id, "failed", "error", str(exc))
            await store_event(job.id, "pipeline_failed", {"error": str(exc)})
        finally:
            _event_queues.pop(job.id, None)


async def _phase1_generator(job: Job):
    from api.routes.images import _run_exif_phase
    async for sse_event in _run_exif_phase(
        job.file_path,
        job.file_source,
        job.skip_exif,
        job.skip_faces,
        job.insert,
        note=job.note,
    ):
        yield sse_event


async def _phase2_generator(job: Job):
    from api.routes.images import _run_ai_phase
    async for sse_event in _run_ai_phase(
        job.file_path,
        job.file_source,
        None,
        note=job.note,
    ):
        yield sse_event


async def _both_phase_generator(job: Job):
    from api.routes.images import _process_and_stream
    async for sse_event in _process_and_stream(
        job.file_path,
        job.file_source,
        job.skip_exif,
        job.skip_faces,
        job.insert,
        note=job.note,
    ):
        yield sse_event


async def _drain_generator(job: Job, event_generator, phase: str) -> None:
    """Consume SSE events from a phase generator, update job status/stage,
    persist EXIF when it appears, and store every event for replay.

    Phase-aware: a phase-1-only run ends at ``exif_complete`` stage (left for
    the coordinator to promote to phase 2); a phase-2-only run ends at
    ``pipeline_complete``; a ``both`` run ends at ``pipeline_complete``.
    """
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
            if event_name == "exif_complete":
                exif = event_data.get("exif") or event_data.get("exif_data")
                if exif:
                    from api.services.db import save_photo_exif
                    await save_photo_exif(job.file_source, exif)
            await store_event(job.id, event_name, event_data if isinstance(event_data, dict) else {"raw": event_data})
        except (json.JSONDecodeError, TypeError):
            await store_event(job.id, "raw", {"data": sse_event.data})

    if phase == "exif":
        # Phase 1 finished: mark the phase boundary so the coordinator and
        # resume logic know phase 2 still needs to run.
        await update_job_status(job.id, "processing", "exif_complete")
    else:
        await update_job_status(job.id, "complete", "pipeline_complete")


async def run_batch_phases(jobs: list[Job]) -> None:
    """Two-phase batch coordinator.

    Phase 1: run ALL jobs through EXIF + faces + entity creation with high
    concurrency (``_exif_semaphore``, 10). Phase 2: after every phase-1 job
    has finished, run phase 2 (VLM + LightRAG + linking) with the regular
    ``_semaphore`` (3 — VLM is LLM-bound and serialized by ``_vlm_semaphore``
    anyway, so 3 only helps overlap the LightRAG wait).

    A job whose phase 1 fails is skipped in phase 2 (its status is already
    ``failed``). Jobs with ``insert=False`` complete in phase 1 and are not
    promoted to phase 2.
    """
    phase1_tasks = [
        asyncio.create_task(_run_job(job, phase="exif"))
        for job in jobs
    ]
    await asyncio.gather(*phase1_tasks, return_exceptions=True)

    # Reload job state — phase 1 updated status/stage in the DB. Only promote
    # jobs that reached the phase boundary intact.
    phase2_jobs: list[Job] = []
    for job in jobs:
        fresh = await get_job(job.id)
        if fresh is None:
            continue
        if fresh.status == "processing" and fresh.stage == "exif_complete":
            phase2_jobs.append(fresh)
        elif fresh.status == "complete":
            # insert=False jobs finish in phase 1; nothing to do.
            continue
        # failed/cancelled jobs are intentionally not promoted.

    phase2_tasks = [
        asyncio.create_task(_run_job(job, phase="ai"))
        for job in phase2_jobs
    ]
    await asyncio.gather(*phase2_tasks, return_exceptions=True)


_BATCH_DEBOUNCE_SECONDS = 1.5


async def enqueue_and_maybe_batch(job: Job) -> None:
    """Enqueue a freshly-created job and start the two-phase batch coordinator
    if it isn't already running.

    Debounces by ``_BATCH_DEBOUNCE_SECONDS`` so a burst of /images/jobs uploads
    coalesces into one batch: each call (re)arms the timer, and only when uploads
    go quiet for the debounce window does the coordinator fire over all pending
    jobs at that moment. A single upload with no other pending jobs still goes
    through the coordinator (a batch of one) — the coordinator runs phase 1 then
    phase 2 for that one job, preserving the single-image end-to-end behavior.
    """
    global _batch_timer
    async with _batch_lock:
        loop = asyncio.get_running_loop()
        if _batch_timer is not None:
            _batch_timer.cancel()
        _batch_timer = loop.call_later(
            _BATCH_DEBOUNCE_SECONDS,
            lambda: asyncio.create_task(_fire_batch(job.id)),
        )


async def _fire_batch(trigger_job_id: str) -> None:
    global _batch_timer
    async with _batch_lock:
        _batch_timer = None
    # Snapshot every job that's still pending (not yet started) plus the
    # triggering job. Jobs already moved to processing/exif_complete from a
    # prior batch are left alone — they're being driven by that batch.
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at"
        )
    pending = [Job(**dict(r)) for r in rows]
    if not pending:
        return
    logger.info("Batch coordinator firing for %d pending job(s)", len(pending))
    await run_batch_phases(pending)


def _map_event_to_stage(event_name: str) -> str:
    if event_name in ("extracting_exif", "exif_complete", "detecting_faces", "faces_complete", "captions_built", "exif_dimensions_ready"):
        return "extracting_metadata"
    if event_name in ("injecting_exif_relations", "creating_exif_entities", "photo_node_created", "exif_node_created", "exif_relation_created", "exif_entities_complete"):
        return "creating_entities"
    if event_name == "exif_phase_complete":
        return "exif_complete"
    if event_name in ("describing_image", "upload_complete", "lightrag_upload_complete", "lightrag_processing_waiting", "lightrag_processing_timeout"):
        return "processing_ai"
    if event_name in ("lightrag_processing_complete",) or event_name.startswith("visual_"):
        return "linking_entities"
    if event_name == "pipeline_complete":
        return "complete"
    if event_name.endswith("_failed") or event_name.endswith("_error") or event_name.endswith("_timeout"):
        return "error"
    return ""


_STUCK_JOB_TIMEOUT_SECONDS = 30 * 60  # 30 minutes

async def resume_pending_jobs() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM jobs WHERE status IN ('pending', 'processing') ORDER BY created_at")
    resumed = []
    now = time.time()

    # Group resumed jobs by phase so we can drive them through the two-phase
    # batch coordinator: phase-1 jobs run with high concurrency, then phase-2
    # jobs run after. Single jobs in either phase still go through the
    # coordinator (a batch of one), which keeps the stage transitions uniform.
    phase1_jobs: list[Job] = []
    phase2_jobs: list[Job] = []

    for row in rows:
        job = Job(**dict(row))

        # Pending jobs never started — begin from phase 1.
        if job.status == "pending":
            logger.info("Resuming pending job %s for %s (phase 1)", job.id, job.file_source)
            phase1_jobs.append(job)
            resumed.append(job.id)
            continue

        # Processing jobs: classify by how far they got before the restart.
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

        # Phase boundary reached: phase 1 fully done, phase 2 not started.
        if job.stage == "exif_complete" or "exif_phase_complete" in prior_types:
            logger.info("Resuming job %s for %s at phase 2 (phase 1 already complete)", job.id, job.file_source)
            phase2_jobs.append(job)
            resumed.append(job.id)
            continue

        # Interrupted mid-phase-1: restart from phase 1, but patch up a
        # dangling face-detection interruption so the UI doesn't hang.
        if job.stage in ("extracting_metadata", "creating_entities", "starting", ""):
            if "detecting_faces" in prior_types and "faces_complete" not in prior_types:
                if not job.skip_faces:
                    job.skip_faces = True
                    logger.warning(
                        "Job %s was interrupted during face detection — skipping faces on resume",
                        job.id,
                    )
                # Emit a closing faces_complete so consumers (UI stepper, status
                # mapping) reconcile the dangling detecting_faces event instead of
                # hanging on "Running facial recognition..." forever.
                await store_event(job.id, "faces_complete", {"faces": {}, "resumed": True})
            logger.info("Re-running interrupted job %s for %s (phase 1 restart)", job.id, job.file_source)
            phase1_jobs.append(job)
            resumed.append(job.id)
            continue

        # Interrupted mid-phase-2 (processing_ai / linking_entities): restart
        # phase 2 from the beginning. Phase-1 output is already in the DB, so
        # _run_ai_phase reads metadata_text back from photo_metadata.
        if job.stage in ("processing_ai", "linking_entities"):
            logger.info("Resuming job %s for %s at phase 2 (was mid-AI, restarting phase 2)", job.id, job.file_source)
            phase2_jobs.append(job)
            resumed.append(job.id)
            continue

        # Unknown stage — safest to restart from phase 1.
        logger.info("Resuming job %s for %s with unknown stage %r (phase 1 restart)", job.id, job.file_source, job.stage)
        phase1_jobs.append(job)
        resumed.append(job.id)

    # Drive each phase batch through the coordinator. Empty batches are fine.
    if phase1_jobs:
        # Phase-1-only batch: after phase 1 completes these land in
        # exif_complete and need phase 2, so run the full two-phase coordinator.
        asyncio.create_task(run_batch_phases(phase1_jobs))
    if phase2_jobs:
        # Phase-2-only batch: these already passed the phase boundary, so run
        # them directly as phase-2 tasks rather than through run_batch_phases
        # (which would re-run phase 1).
        for job in phase2_jobs:
            await start_processing(job, phase="ai")

    return resumed