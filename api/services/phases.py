"""Two-phase image processing pipeline runners, shared by the worker and the
legacy synchronous API endpoints.

This module was extracted out of ``api/routes/images.py`` and
``api/services/job_manager.py`` so that both the worker process
(:mod:`api.worker`) and the FastAPI process (legacy sync endpoints in
``api.routes.images``) can import the phase generators and the job-driving
helpers without creating a circular dependency on the router layer.

Import hygiene: this module must NOT import from ``api.routes.images``.
Constants and helpers it needs (``KNOWN_FACES_PATH`` etc.) live here; the
router imports them back from here.

The semaphores (``_semaphore``, ``_exif_semaphore``) and the
``_running_tasks`` dict are per-process state and live here in the worker
process. The VLM semaphore in ``processor.py`` (``_get_vlm_semaphore``) is
separate and imported by both processes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncGenerator

from sse_starlette.sse import ServerSentEvent

from api.services import config
from api.services.processor import (
    create_exif_relations,
    link_exif_to_visual_entities,
    prepare_vlm_image,
    cleanup_vlm_image,
    process_image,
    upload_image_to_lightrag,
    wait_for_lightrag_processing,
)
from api.services.job_manager import (
    Job,
    get_events,
    get_job,
    store_event,
    update_job_status,
)

logger = logging.getLogger(__name__)

# Constants moved here from api/routes/images.py so the phase runners don't
# import the router. images.py imports these back from this module.
KNOWN_FACES_PATH = os.environ.get(
    "KNOWN_FACES_PATH",
    str(Path(__file__).resolve().parent.parent.parent / "known_faces"),
)


# --- Concurrency caps (per-process; live in the worker process) -------------

MAX_CONCURRENT_JOBS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Phase 1 (EXIF + faces + entity creation) is CPU/IO bound, not LLM bound, so
# it can run with much higher concurrency than phase 2. Face detection runs in
# a subprocess with its own memory footprint, so 10 is a deliberate cap that
# keeps peak RSS under the container mem_limit while still parallelizing the
# slow EXIF/entity-creation wave across a batch upload.
MAX_EXIF_CONCURRENT = 10
_exif_semaphore = asyncio.Semaphore(MAX_EXIF_CONCURRENT)

# Per-worker-process tracking of in-flight job tasks.
_running_tasks: dict[str, asyncio.Task] = {}


# --- Phase generators (moved from api/routes/images.py) ---------------------


async def _emit_exif_entities(
    file_source: str,
    exif_data: dict,
) -> AsyncGenerator[ServerSentEvent, None]:
    photo_name = f"{file_source} (Photo)"
    exif_dimensions: list[dict[str, Any]] = []

    date_taken_friendly = exif_data.get("date_taken_friendly")
    if date_taken_friendly:
        date_only = date_taken_friendly.split(" at ")[0].split(" ")[0]
        exif_dimensions.append({"name": f"{date_only} (Date)", "entity_type": "Date", "description": f"Calendar date {date_taken_friendly}", "edge_keyword": "taken_on", "edge_description": f"Photo taken on {date_taken_friendly}"})

    location = exif_data.get("location")
    if location:
        loc_str = location if isinstance(location, str) else str(location)
        if loc_str:
            exif_dimensions.append({"name": f"{loc_str} (Location)", "entity_type": "Location", "description": f"Location in {loc_str}", "edge_keyword": "taken_at", "edge_description": f"Photo taken at {loc_str}"})

    camera = exif_data.get("camera") or exif_data.get("camera_make") or exif_data.get("camera_model")
    if camera:
        exif_dimensions.append({"name": f"{camera} (Camera)", "entity_type": "Camera", "description": f"Camera device: {camera}", "edge_keyword": "taken_with", "edge_description": f"Photo taken with {camera}"})

    yield ServerSentEvent(event="message", data=json.dumps({"event": "injecting_exif_relations", "data": {"file_source": file_source}, "timestamp": time.time()}))
    yield ServerSentEvent(event="message", data=json.dumps({"event": "photo_node_created", "data": {"entity_name": photo_name, "entity_type": "Photo", "labels": ["Photo"], "source_id": file_source}, "timestamp": time.time()}))
    for dim in exif_dimensions:
        yield ServerSentEvent(event="message", data=json.dumps({"event": "exif_node_created", "data": {"entity_name": dim["name"], "entity_type": dim.get("entity_type", "ExifEntity"), "labels": [dim.get("entity_type", "ExifEntity")]}}))

    if exif_dimensions:
        yield ServerSentEvent(event="message", data=json.dumps({"event": "creating_exif_entities", "data": {"file_source": file_source, "dimensions_count": len(exif_dimensions)}, "timestamp": time.time()}))
        exif_result = None
        max_create_attempts = 3
        for attempt in range(max_create_attempts):
            try:
                exif_result = await create_exif_relations(config.lightrag_url(), file_source, photo_name, exif_dimensions)
                failed_entities = [e for e in exif_result.get("entities_created", []) if e.get("status") == "error"]
                if not failed_entities:
                    break
                logger.warning("[EXIF] attempt %d/%d had %d failed entities for %s, retrying",
                               attempt + 1, max_create_attempts, len(failed_entities), file_source)
            except Exception as exc:
                logger.warning("[EXIF] attempt %d/%d raised for %s: %s",
                               attempt + 1, max_create_attempts, file_source, exc)
                exif_result = None
            if attempt < max_create_attempts - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
        if exif_result:
            for entity in exif_result.get("entities_created", []):
                if "entity_name" not in entity and "data" in entity and isinstance(entity["data"], dict):
                    entity["entity_name"] = entity["data"].get("entity_name", "")
                if "entity_type" not in entity and "data" in entity and isinstance(entity["data"], dict):
                    entity["entity_type"] = entity["data"].get("entity_type", "")
            for relation in exif_result.get("relations_created", []):
                src = relation.get("source") or relation.get("source_entity") or ""
                tgt = relation.get("target") or relation.get("target_entity") or ""
                yield ServerSentEvent(event="message", data=json.dumps({"event": "exif_relation_created", "data": {"source": src, "target": tgt, "relation_type": relation.get("keywords", "has_exif")}, "timestamp": time.time()}))
            yield ServerSentEvent(event="message", data=json.dumps({"event": "exif_entities_complete", "data": {"file_source": file_source, "entities": len(exif_result.get("entities_created", [])), "relations": len(exif_result.get("relations_created", []))}, "timestamp": time.time()}))
        else:
            logger.error("EXIF entity creation failed for %s after %d attempts", file_source, max_create_attempts)
            yield ServerSentEvent(event="message", data=json.dumps({"event": "exif_entities_failed", "data": {"error": "max retries exceeded", "exif_dimensions": exif_dimensions}, "timestamp": time.time()}))


async def _run_exif_phase(
    file_path: str,
    file_source: str,
    skip_exif: bool,
    skip_faces: bool,
    insert: bool,
    note: str = "",
):
    """Phase 1 of the two-phase pipeline: EXIF extraction, face detection,
    and Photo/Date/Location/Camera entity creation in LightRAG.

    Emits the same SSE events as the original ``_process_and_stream`` up
    through ``exif_entities_complete`` (or ``pipeline_complete`` when
    ``insert`` is False). Persists EXIF + ``metadata_text`` to
    ``photo_metadata`` so phase 2 can resume from DB state.

    Returns the ``metadata_text`` built from EXIF + faces (or None when no
    EXIF/faces ran or ``insert`` is False). Phase 2 reads it back from the DB
    by ``file_source`` when the job object is the only state crossing the
    phase boundary.
    """
    content_list: list[dict] | None = None
    metadata_text: str | None = None
    exif_data: dict | None = None
    exif_entities_emitted = False

    async for event in process_image(
        file_path,
        known_faces_path=KNOWN_FACES_PATH,
        skip_exif=skip_exif,
        skip_faces=skip_faces,
    ):
        if event.event == "captions_built":
            content_list = event.data.get("content_list")
            metadata_text = event.data.get("metadata_text")
            exif_data = event.data.get("exif_data")

        if event.event == "exif_complete":
            exif_data = event.data.get("exif") or event.data.get("exif_data")

        yield ServerSentEvent(
            event="message",
            data=json.dumps(asdict(event)),
        )

        # Emit Photo/EXIF node events immediately after EXIF data is available,
        # before face recognition (which is slow and can OOM the container).
        if event.event == "exif_complete" and insert and not exif_entities_emitted:
            exif_entities_emitted = True
            async for sse_event in _emit_exif_entities(file_source, exif_data):
                yield sse_event

    if not insert:
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "pipeline_complete", "data": {"file_source": file_source, "status": "no_insert"}, "timestamp": time.time()}),
        )
        return

    # Fallback: if EXIF extraction was skipped but captions_built still has exif_data
    if not exif_entities_emitted and exif_data:
        async for sse_event in _emit_exif_entities(file_source, exif_data):
            yield sse_event

    # Mark the phase-1 boundary so the job manager/coordinator and the UI can
    # distinguish "all EXIF/entities done, waiting for AI wave" from the
    # transient extracting_metadata/creating_entities stages.
    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "exif_phase_complete", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )

    # Persist EXIF + metadata_text so phase 2 (which may run much later, on a
    # different task, or after a restart) can reconstruct the phase-1 output
    # from DB state alone. save_photo_exif is idempotent (UPSERT on file_source).
    if exif_data is not None:
        try:
            from api.services.db import save_photo_exif
            await save_photo_exif(file_source, exif_data, metadata_text=metadata_text)
        except Exception:
            logger.exception("Failed to persist EXIF + metadata_text for %s", file_source)

    return


async def _run_ai_phase(
    file_path: str,
    file_source: str,
    metadata_text: str | None,
    note: str = "",
    photo_name: str | None = None,
):
    """Phase 2 of the two-phase pipeline: VLM description, LightRAG
    ingestion, and visual-entity linking.

    Reads ``metadata_text`` back from the DB (``photo_metadata``) when the
    caller passes None — phase 2 may run in a fresh process that only has the
    Job object, so the bridge state lives in the DB. Emits ``describing_image``
    through ``pipeline_complete`` events, identical to the original
    ``_process_and_stream`` phase 3-5.
    """
    if metadata_text is None:
        try:
            from api.services.db import get_photo_metadata_text
            metadata_text = await get_photo_metadata_text(file_source)
        except Exception:
            logger.exception("Failed to load metadata_text for %s", file_source)

    # Signal the UI that we're about to wait for the VLM semaphore/queue before
    # the describing_image (active AI run) event fires.
    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "queued_for_ai", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )
    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "describing_image", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )
    try:
        if note:
            metadata_text = (f"User note: {note}\n\n" + metadata_text) if metadata_text else f"User note: {note}"
        vlm_image_path = prepare_vlm_image(file_path)
        try:
            upload_result = await upload_image_to_lightrag(
                config.lightrag_url(), vlm_image_path, filename=file_source, metadata_text=metadata_text,
            )
        finally:
            cleanup_vlm_image(vlm_image_path)
        if upload_result.get("status") == "error":
            yield ServerSentEvent(
                event="message",
                data=json.dumps({"event": "upload_failed", "data": upload_result, "timestamp": time.time()}),
            )
            yield ServerSentEvent(
                event="message",
                data=json.dumps({"event": "pipeline_complete", "data": {"file_source": file_source, "status": "upload_failed"}, "timestamp": time.time()}),
            )
            return
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "upload_complete", "data": upload_result, "timestamp": time.time()}),
        )
    except Exception as exc:
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "upload_failed", "data": {"error": str(exc)}, "timestamp": time.time()}),
        )
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "pipeline_complete", "data": {"file_source": file_source, "status": "upload_failed"}, "timestamp": time.time()}),
        )
        return

    # Phase 4: Wait for LightRAG to finish processing the document
    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "lightrag_upload_complete", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )

    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "lightrag_processing_waiting", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )
    try:
        final_status = await wait_for_lightrag_processing(config.lightrag_url(), file_source)
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "lightrag_processing_complete", "data": {"file_source": file_source, "status": final_status}, "timestamp": time.time()}),
        )
    except TimeoutError as exc:
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "lightrag_processing_timeout", "data": {"error": str(exc)}, "timestamp": time.time()}),
        )
    except Exception as exc:
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "lightrag_processing_error", "data": {"error": str(exc)}, "timestamp": time.time()}),
        )

    # Phase 5: Link LLM-extracted visual entities to the photo node
    # wait_for_lightrag_processing already confirmed our document reached a
    # terminal state, so visual entities should exist.  The previous code
    # polled the *global* label count which races with concurrent jobs —
    # another job creating entities could trigger an early break before our
    # document's entities exist.  Just proceed to linking directly.
    if photo_name is None:
        photo_name = f"{file_source} (Photo)"

    try:
        visual_result = await link_exif_to_visual_entities(config.lightrag_url(), file_source, photo_name)
        for link in visual_result.get("visual_links_created", []):
            if "source" not in link:
                link["source"] = link.get("source_entity") or link.get("src") or ""
            if "target" not in link:
                link["target"] = link.get("target_entity") or link.get("tgt") or photo_name
            yield ServerSentEvent(
                event="message",
                data=json.dumps({"event": "visual_entity_linked", "data": link, "timestamp": time.time()}),
            )
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "visual_links_complete", "data": visual_result, "timestamp": time.time()}),
        )
    except Exception as exc:
        logger.exception("Visual entity linking failed for %s", file_source)
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "visual_links_failed", "data": {"error": str(exc)}, "timestamp": time.time()}),
        )

    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "pipeline_complete", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )


async def _process_and_stream(
    file_path: str,
    file_source: str,
    skip_exif: bool,
    skip_faces: bool,
    insert: bool,
    note: str = "",
):
    """Legacy single-image pipeline: run phase 1 then phase 2 sequentially.

    Kept for the synchronous single-upload endpoints (``/process``,
    ``/reprocess``) that stream both phases to one SSE client. The batch
    coordinator in ``job_manager`` drives the two phases independently via
    ``_run_exif_phase`` / ``_run_ai_phase``.
    """
    metadata_text: str | None = None
    async for ev in _run_exif_phase(file_path, file_source, skip_exif, skip_faces, insert, note):
        yield ev

    if not insert:
        return

    # Phase 1 persisted metadata_text to the DB; phase 2 reads it back so the
    # two phases share state through the DB rather than an in-memory value
    # that would be lost on restart. This also keeps phase 2's contract
    # identical whether it runs chained or standalone.
    async for ev in _run_ai_phase(file_path, file_source, None, note=note):
        yield ev


# --- Job-driving helpers (moved from api/services/job_manager.py) ----------


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


async def _phase1_generator(job: Job):
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
    async for sse_event in _run_ai_phase(
        job.file_path,
        job.file_source,
        None,
        note=job.note,
    ):
        yield sse_event


async def _both_phase_generator(job: Job):
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

    When ``config.vlm_batch_enabled()`` is True (default), phase 2 is
    deferred — jobs stay at ``exif_complete`` and are picked up by the
    overnight scheduler or a manual trigger. When False, phase 2 runs
    immediately after phase 1 (legacy behavior).
    """
    phase1_tasks = [
        asyncio.create_task(_run_job(job, phase="exif"))
        for job in jobs
    ]
    await asyncio.gather(*phase1_tasks, return_exceptions=True)

    if config.vlm_batch_enabled():
        # Phase 2 is deferred — leave jobs at exif_complete for the
        # overnight scheduler or manual trigger.
        promoted = sum(
            1 for job in jobs
            if (fresh := await get_job(job.id))
            and fresh.status == "processing"
            and fresh.stage == "exif_complete"
        )
        if promoted:
            logger.info("VLM batch enabled — %d job(s) left at exif_complete for overnight/manual processing", promoted)
        return

    # Legacy path: promote immediately.
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


async def run_overnight_vlm_batch() -> int:
    """Process all jobs sitting at ``exif_complete`` through phase 2 (VLM).

    Called by the overnight scheduler or the manual ``/process-ai-queue``
    endpoint. Selects every job with ``status='processing'`` and
    ``stage='exif_complete'``, runs phase 2 for each with the existing
    ``_semaphore`` concurrency cap, and returns the count processed.
    """
    from api.services.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM jobs WHERE status = 'processing' AND stage = 'exif_complete' ORDER BY created_at"
        )
    jobs = [Job(**dict(r)) for r in rows]
    if not jobs:
        logger.info("Overnight VLM batch: no exif_complete jobs to process")
        return 0

    logger.info("Overnight VLM batch: processing %d job(s)", len(jobs))
    tasks = [asyncio.create_task(_run_job(job, phase="ai")) for job in jobs]
    await asyncio.gather(*tasks, return_exceptions=True)
    return len(jobs)