from __future__ import annotations

import io
import json
import logging
import os
import time
import asyncio
import tempfile
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

# Per-thumbnail generation locks. Keyed by cache_path so concurrent requests
# for the same uncached thumbnail serialize instead of racing on the same
# file (which produced corrupted JPEGs that got cached permanently).
_thumb_locks: dict[str, asyncio.Lock] = {}
_thumb_locks_guard = asyncio.Lock()

# Caps concurrent thumbnail generations. PIL's native (non-Python) memory
# for decoding a 3MB RAW JPEG is ~400MB RSS per image; unbounded concurrency
# (37+ parallel photo loads on initial page render) exhausted the 1g
# mem_limit and triggered OOM kills (exit 137), dropping every in-flight
# connection with "Empty reply from server". 2 keeps peak native RSS under
# 1g while letting the event loop stay responsive to other requests.
_thumb_gen_sem = asyncio.Semaphore(2)

from fastapi import APIRouter, File, Form, UploadFile
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from sse_starlette.sse import ServerSentEvent, EventSourceResponse

from api.services.processor import (
    ProcessingEvent, process_image, upload_image_to_lightrag,
    describe_image_with_vlm, describe_face_crop, create_exif_relations,
    link_exif_to_visual_entities, wait_for_lightrag_processing,
    delete_photo_entities,
)
from api.services import db as db_module
from api.services.job_manager import (
    create_job, get_job, list_jobs, delete_job,
    persist_uploaded_file, start_processing, subscribe_events,
    unsubscribe_events, get_events, update_job_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


async def _resolve_skip_faces(request_skip_faces: bool) -> bool:
    settings = await db_module.get_app_settings()
    if not settings.get("face_detection_enabled", False):
        return True
    return request_skip_faces

LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://localhost:9621")
KNOWN_FACES_PATH = os.environ.get("KNOWN_FACES_PATH", str(Path(__file__).resolve().parent.parent.parent / "known_faces"))
INPUT_DIR = Path(os.environ.get("INPUT_DIR", str(Path(__file__).resolve().parent.parent.parent / "inputs")))
FACES_CACHE_DIR = Path(os.environ.get("FACES_CACHE_DIR", str(Path(__file__).resolve().parent.parent.parent / "face_crops")))
FACES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
THUMBS_CACHE_DIR = Path(os.environ.get("THUMBS_CACHE_DIR", str(Path(__file__).resolve().parent.parent.parent / "dist" / "thumbs")))
THUMBS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


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
        try:
            exif_result = await create_exif_relations(LIGHTRAG_URL, file_source, photo_name, exif_dimensions)
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
        except Exception as exc:
            logger.exception("EXIF entity creation failed for %s", file_source)
            yield ServerSentEvent(event="message", data=json.dumps({"event": "exif_entities_failed", "data": {"error": str(exc)}, "timestamp": time.time()}))


async def _process_and_stream(
    file_path: str,
    file_source: str,
    skip_exif: bool,
    skip_faces: bool,
    insert: bool,
):
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

    # Phase 3: Upload to LightRAG (VLM description + insert)
    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "describing_image", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )
    try:
        upload_result = await upload_image_to_lightrag(
            LIGHTRAG_URL, file_path, filename=file_source, metadata_text=metadata_text,
        )
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
        final_status = await wait_for_lightrag_processing(LIGHTRAG_URL, file_source)
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
    photo_name = f"{file_source} (Photo)"

    try:
        visual_result = await link_exif_to_visual_entities(LIGHTRAG_URL, file_source, photo_name)
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


@router.post("/process")
async def process_image_sse(
    file: UploadFile = File(...),
    skip_exif: Optional[str] = Form(None),
    skip_faces: Optional[str] = Form(None),
    insert: Optional[str] = Form(None),
):
    skip_exif_bool = _parse_bool(skip_exif)
    skip_faces_bool = await _resolve_skip_faces(_parse_bool(skip_faces))
    insert_bool = _parse_bool(insert, default=True)

    suffix = os.path.splitext(file.filename or "image.jpg")[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()

        file_source = file.filename or "uploaded_image"

        event_generator = _process_and_stream(
            tmp.name,
            file_source,
            skip_exif_bool,
            skip_faces_bool,
            insert_bool,
        )
        return EventSourceResponse(event_generator)
    except Exception:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise


@router.post("/process-json")
async def process_image_json(
    file: UploadFile = File(...),
    skip_exif: Optional[str] = Form(None),
    skip_faces: Optional[str] = Form(None),
    insert: Optional[str] = Form(None),
):
    skip_exif_bool = _parse_bool(skip_exif)
    skip_faces_bool = await _resolve_skip_faces(_parse_bool(skip_faces))
    insert_bool = _parse_bool(insert, default=True)

    suffix = os.path.splitext(file.filename or "image.jpg")[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()

        file_source = file.filename or "uploaded_image"

        events: list[ProcessingEvent] = []
        content_list: list[dict] | None = None
        metadata_text: str | None = None
        exif_data: dict | None = None

        async for event in process_image(
            tmp.name,
            known_faces_path=KNOWN_FACES_PATH,
            skip_exif=skip_exif_bool,
            skip_faces=skip_faces_bool,
        ):
            events.append(event)
            if event.event == "captions_built":
                content_list = event.data.get("content_list")
                metadata_text = event.data.get("metadata_text")
                exif_data = event.data.get("exif_data")

        if not insert_bool:
            events.append(ProcessingEvent(event="pipeline_complete", data={"file_source": file_source, "status": "no_insert"}))
            return {"events": [asdict(e) for e in events]}

        # Phase 2: Build EXIF dimensions and create entities in LightRAG
        # before uploading the document (pipeline is idle, no 409 conflict).
        photo_name = f"{file_source} (Photo)"
        exif_dimensions: list[dict[str, Any]] = []

        if exif_data:
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

            if exif_dimensions:
                try:
                    exif_result = await create_exif_relations(LIGHTRAG_URL, file_source, photo_name, exif_dimensions)
                    for entity in exif_result.get("entities_created", []):
                        if "entity_name" not in entity and "data" in entity and isinstance(entity["data"], dict):
                            entity["entity_name"] = entity["data"].get("entity_name", "")
                        if "entity_type" not in entity and "data" in entity and isinstance(entity["data"], dict):
                            entity["entity_type"] = entity["data"].get("entity_type", "")
                    for relation in exif_result.get("relations_created", []):
                        src = relation.get("source") or relation.get("source_entity") or ""
                        tgt = relation.get("target") or relation.get("target_entity") or ""
                        relation["source"] = src
                        relation["target"] = tgt
                    events.append(ProcessingEvent(event="exif_entities_complete", data={"file_source": file_source, "entities": len(exif_result.get("entities_created", [])), "relations": len(exif_result.get("relations_created", []))}))
                except Exception as exc:
                    logger.exception("EXIF entity creation failed for %s", file_source)
                    events.append(ProcessingEvent(event="exif_entities_failed", data={"error": str(exc)}))

        # Phase 3: Upload to LightRAG
        upload_result = None
        try:
            upload_result = await upload_image_to_lightrag(
                LIGHTRAG_URL, tmp.name, filename=file_source, metadata_text=metadata_text,
            )
            if upload_result.get("status") == "error":
                events.append(ProcessingEvent(event="upload_failed", data=upload_result))
                events.append(ProcessingEvent(event="pipeline_complete", data={"file_source": file_source, "status": "upload_failed"}))
                return {"events": [asdict(e) for e in events]}
            events.append(ProcessingEvent(event="upload_complete", data=upload_result))
        except Exception as exc:
            events.append(ProcessingEvent(event="upload_failed", data={"error": str(exc)}))
            events.append(ProcessingEvent(event="pipeline_complete", data={"file_source": file_source, "status": "upload_failed"}))
            return {"events": [asdict(e) for e in events]}

        events.append(ProcessingEvent(event="lightrag_upload_complete", data={"file_source": file_source}))

        # Phase 4: Wait for LightRAG processing
        events.append(ProcessingEvent(event="lightrag_processing_waiting", data={"file_source": file_source}))
        try:
            final_status = await wait_for_lightrag_processing(LIGHTRAG_URL, file_source)
            events.append(ProcessingEvent(event="lightrag_processing_complete", data={"file_source": file_source, "status": final_status}))
        except TimeoutError as exc:
            events.append(ProcessingEvent(event="lightrag_processing_timeout", data={"error": str(exc)}))
        except Exception as exc:
            events.append(ProcessingEvent(event="lightrag_processing_error", data={"error": str(exc)}))

        # Phase 5: Link visual entities
        try:
            visual_result = await link_exif_to_visual_entities(LIGHTRAG_URL, file_source, photo_name)
            for link in visual_result.get("visual_links_created", []):
                if "source" not in link:
                    link["source"] = link.get("source_entity") or link.get("src") or ""
                if "target" not in link:
                    link["target"] = link.get("target_entity") or link.get("tgt") or photo_name
                events.append(ProcessingEvent(event="visual_entity_linked", data=link))
            events.append(ProcessingEvent(event="visual_links_complete", data=visual_result))
        except Exception as exc:
            logger.exception("Visual entity linking failed for %s", file_source)
            events.append(ProcessingEvent(event="visual_links_failed", data={"error": str(exc)}))

        events.append(ProcessingEvent(event="pipeline_complete", data={"file_source": file_source}))

        return {"events": [asdict(e) for e in events]}
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


@router.get("/health")
async def images_health():
    return {"status": "ok", "lightrag_url": LIGHTRAG_URL, "known_faces_path": KNOWN_FACES_PATH}


@router.post("/link-exif-entities")
async def link_exif_entities(
    file_source: str,
    photo_name: str,
    exif_dimensions: Optional[str] = None,
):
    """Link EXIF and visual entities to the photo node in the knowledge graph.

    Call this after LightRAG has finished processing a document. It:
    1. Creates relation edges between EXIF entities and the Photo node (if exif_dimensions provided)
    2. Finds LLM-extracted visual entities with matching file_path and creates appears_in edges
    """
    photo_name = photo_name.replace("+", " ")
    exif_result = None
    if exif_dimensions:
        import json as _json
        dimensions = _json.loads(exif_dimensions)
        exif_result = await create_exif_relations(
            LIGHTRAG_URL, file_source, photo_name, dimensions,
        )

    visual_result = await link_exif_to_visual_entities(LIGHTRAG_URL, file_source, photo_name)

    return {
        "exif_relations": exif_result,
        "visual_links": visual_result,
    }


@router.get("/photo/{filename:path}")
async def get_photo(filename: str, w: Optional[str] = None):
    """Serve a photo image file from the inputs directory.

    Used by the UI to display photo textures in the 3D graph on page reload.
    """
    file_path = INPUT_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Photo not found: {filename}")
    # Prevent path traversal
    try:
        file_path.resolve().relative_to(INPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid path")

    if w is None or w == "full" or w == "":
        return FileResponse(str(file_path))

    try:
        w_int = int(w)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="w must be a positive integer or 'full'")
    if w_int <= 0:
        raise HTTPException(status_code=400, detail="w must be a positive integer or 'full'")

    safe_filename = filename.replace("/", "_").replace("\\", "_").replace("..", "")
    cache_path = THUMBS_CACHE_DIR / f"{safe_filename}_{w_int}.jpg"

    cache_headers = {"Cache-Control": "public, max-age=31536000, immutable"}

    if cache_path.is_file():
        return FileResponse(str(cache_path), media_type="image/jpeg", headers=cache_headers)

    async with _thumb_locks_guard:
        lock = _thumb_locks.get(str(cache_path))
        if lock is None:
            lock = asyncio.Lock()
            _thumb_locks[str(cache_path)] = lock

    async with lock:
        if cache_path.is_file():
            return FileResponse(str(cache_path), media_type="image/jpeg", headers=cache_headers)

        try:
            from PIL import Image, ImageOps

            def _generate() -> bytes:
                img = Image.open(str(file_path))
                img = ImageOps.exif_transpose(img)
                img = img.copy()
                img.thumbnail((w_int, w_int), Image.Resampling.LANCZOS)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=85)
                return buf.getvalue()

            async with _thumb_gen_sem:
                data = await asyncio.to_thread(_generate)
            tmp_path = cache_path.with_suffix(".jpg.tmp")
            tmp_path.write_bytes(data)
            os.replace(str(tmp_path), str(cache_path))
            return FileResponse(str(cache_path), media_type="image/jpeg", headers=cache_headers)
        except Exception as exc:
            logger.warning("Thumbnail generation failed for '%s' (w=%s): %s — serving raw", filename, w, exc)
            return FileResponse(str(file_path))


@router.get("/exif/bulk-dates")
async def get_bulk_photo_dates():
    """Return EXIF date_taken (and friendly form) for every photo that has one.

    The UI merges these into Photo node properties at graph-load time so the
    canvas positions photos by the date they were taken (EXIF) rather than by
    the LightRAG node-creation timestamp (upload time).
    """
    return await db_module.get_bulk_photo_dates()


@router.get("/exif/{file_source:path}")
async def get_photo_exif(file_source: str):
    """Return persisted EXIF metadata for a photo, keyed by file_source."""
    exif = await db_module.get_photo_exif(file_source)
    if not exif:
        raise HTTPException(status_code=404, detail=f"No EXIF data found for {file_source}")

    key_map = {
        "camera": "camera",
        "date_taken_friendly": "date_taken_friendly",
        "location": "location",
        "lens": "lens",
        "f_number": "f_number",
        "iso": "iso",
        "focal_length_mm": "focal_length",
        "exposure_time": "exposure_time",
        "image_width": "image_width",
        "image_height": "image_height",
        "flash": "flash",
        "white_balance": "white_balance",
        "orientation": "orientation",
    }
    mapped: dict[str, Any] = {}
    for backend_key, frontend_key in key_map.items():
        val = exif.get(backend_key)
        if val is not None and val != "":
            mapped[frontend_key] = val
    return mapped


def _load_face_mapping() -> dict[str, Any]:
    mapping_path = FACES_CACHE_DIR / "face_mapping.json"
    if not mapping_path.is_file():
        return {}
    try:
        return json.loads(mapping_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# Registered BEFORE the {name:path} route so the more specific by-id path wins.
@router.get("/faces/crops/by-id/{face_id}")
async def get_face_crop_by_id(face_id: str):
    """Serve a cropped face image keyed by face_id.

    Looks up ``face_id`` in ``face_mapping.json`` and serves the stored crop
    file. Falls back to on-the-fly detection using the mapping's ``source_id``,
    ``face_index`` and ``bbox`` when the crop file is missing.
    """
    from PIL import Image
    from processors.face_recognizer import detect_faces

    mapping = _load_face_mapping()
    entry = mapping.get(face_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"face_id '{face_id}' not found in face mapping")

    crop_file = entry.get("crop_file") or f"{face_id}.jpg"
    crop_path = FACES_CACHE_DIR / crop_file
    if crop_path.is_file():
        return FileResponse(str(crop_path), media_type="image/jpeg")

    source_id = entry.get("source_id", "")
    if not source_id:
        raise HTTPException(status_code=404, detail=f"No source image for face_id '{face_id}'")

    image_path: Path | None = None
    candidate = INPUT_DIR / source_id
    if candidate.is_file():
        image_path = candidate
    else:
        for ext in ["", ".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"]:
            c = INPUT_DIR / f"{source_id}{ext}"
            if c.is_file():
                image_path = c
                break
    if image_path is None:
        raise HTTPException(status_code=404, detail=f"Source image not found for face_id '{face_id}'")

    bbox = entry.get("bbox", [])
    if len(bbox) == 4:
        try:
            img = await asyncio.to_thread(Image.open, str(image_path))
            img_w, img_h = img.size
            x1, y1, x2, y2 = bbox
            fw, fh = x2 - x1, y2 - y1
            px, py = int(fw * 0.3), int(fh * 0.3)
            cx1, cy1 = max(0, x1 - px), max(0, y1 - py)
            cx2, cy2 = min(img_w, x2 + px), min(img_h, y2 + py)
            cropped = img.crop((cx1, cy1, cx2, cy2))
            cropped.thumbnail((256, 256), Image.Resampling.LANCZOS)
            await asyncio.to_thread(cropped.save, str(crop_path), "JPEG", quality=85)
            img_bytes = io.BytesIO()
            cropped.save(img_bytes, format="JPEG", quality=85)
            img_bytes.seek(0)
            return Response(content=img_bytes.getvalue(), media_type="image/jpeg")
        except Exception as exc:
            logger.error("Failed to crop face for id '%s': %s", face_id, exc)

    # Fall back to re-detection by face_index
    face_index = entry.get("face_index", 0)
    try:
        faces = await asyncio.to_thread(detect_faces, str(image_path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Face detection failed: {exc}")
    if not faces or face_index >= len(faces):
        raise HTTPException(status_code=404, detail=f"Face {face_index} not detected in source image")
    fb = faces[face_index].get("bbox", [])
    if len(fb) != 4:
        raise HTTPException(status_code=404, detail="Invalid bounding box for face")
    try:
        img = await asyncio.to_thread(Image.open, str(image_path))
        img_w, img_h = img.size
        x1, y1, x2, y2 = fb
        fw, fh = x2 - x1, y2 - y1
        px, py = int(fw * 0.3), int(fh * 0.3)
        cx1, cy1 = max(0, x1 - px), max(0, y1 - py)
        cx2, cy2 = min(img_w, x2 + px), min(img_h, y2 + py)
        cropped = img.crop((cx1, cy1, cx2, cy2))
        cropped.thumbnail((256, 256), Image.Resampling.LANCZOS)
        await asyncio.to_thread(cropped.save, str(crop_path), "JPEG", quality=85)
        img_bytes = io.BytesIO()
        cropped.save(img_bytes, format="JPEG", quality=85)
        img_bytes.seek(0)
        return Response(content=img_bytes.getvalue(), media_type="image/jpeg")
    except Exception as exc:
        logger.error("Failed to crop face for id '%s': %s", face_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to crop face: {exc}")


@router.get("/faces/crops/{name:path}")
async def get_face_crop(name: str):
    """Serve a cropped face image for a person entity.

    First checks the face_mapping.json for the correct face-to-person mapping
    (created during image processing). Falls back to on-the-fly detection
    if no mapping exists. Crops are cached on disk.
    """
    import httpx
    from PIL import Image
    from processors.face_recognizer import detect_faces

    safe_name = name.replace("/", "_").replace("\\", "_").replace("..", "")
    cache_path = FACES_CACHE_DIR / f"{safe_name}.jpg"

    if cache_path.is_file():
        return FileResponse(str(cache_path), media_type="image/jpeg")

    # Check face_mapping.json for the person-to-face mapping
    mapping_path = FACES_CACHE_DIR / "face_mapping.json"
    mapping: dict[str, Any] = {}
    if mapping_path.is_file():
        try:
            mapping = json.loads(mapping_path.read_text())
        except (json.JSONDecodeError, OSError):
            mapping = {}

    if name in mapping:
        entry = mapping[name]
        source_id = entry.get("source_id", "")
        if source_id:
            image_path = INPUT_DIR / source_id
            if not image_path.is_file():
                for ext in ["", ".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"]:
                    candidate = INPUT_DIR / f"{source_id}{ext}"
                    if candidate.is_file():
                        image_path = candidate
                        break

            if image_path.is_file():
                faces = await asyncio.to_thread(detect_faces, str(image_path))
                face_index = entry.get("face_index", 0)
                if faces and face_index < len(faces):
                    bbox = faces[face_index].get("bbox", [])
                    if len(bbox) == 4:
                        try:
                            img = await asyncio.to_thread(Image.open, str(image_path))
                            img_w, img_h = img.size
                            x1, y1, x2, y2 = bbox
                            face_w = x2 - x1
                            face_h = y2 - y1
                            pad_x = int(face_w * 0.3)
                            pad_y = int(face_h * 0.3)
                            cx1 = max(0, x1 - pad_x)
                            cy1 = max(0, y1 - pad_y)
                            cx2 = min(img_w, x2 + pad_x)
                            cy2 = min(img_h, y2 + pad_y)
                            cropped = img.crop((cx1, cy1, cx2, cy2))
                            cropped.thumbnail((256, 256), Image.Resampling.LANCZOS)
                            await asyncio.to_thread(cropped.save, str(cache_path), "JPEG", quality=85)
                            img_bytes = io.BytesIO()
                            cropped.save(img_bytes, format="JPEG", quality=85)
                            img_bytes.seek(0)
                            return Response(content=img_bytes.getvalue(), media_type="image/jpeg")
                        except Exception as exc:
                            logger.error("Failed to crop face for '%s': %s", name, exc)

    # Fallback: query LightRAG for the person node, detect faces, use VLM to match
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{LIGHTRAG_URL}/graphs",
                params={"label": name},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail=f"Person entity '{name}' not found")
            graph_data = resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to query LightRAG: {exc}")

    person_node = None
    source_file = None
    all_person_nodes = []
    for node in graph_data.get("nodes", []):
        props = node.get("properties", {})
        if props.get("entity_type", "").lower() == "person":
            all_person_nodes.append(node)
            if node.get("id") == name or name.lower() in str(node.get("id", "")).lower():
                person_node = node
                source_file = props.get("file_path") or props.get("source_id")

    if not person_node:
        raise HTTPException(status_code=404, detail=f"Person entity '{name}' not found")

    if not source_file:
        raise HTTPException(status_code=404, detail=f"No source image found for '{name}'")

    # LightRAG may concatenate multiple source files with <SEP>
    source_files = [f.strip() for f in source_file.split("<SEP>") if f.strip()]
    image_path = None
    for sf in source_files:
        candidate = INPUT_DIR / sf
        if candidate.is_file():
            image_path = candidate
            break
        for ext in ["", ".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"]:
            candidate = INPUT_DIR / f"{sf}{ext}"
            if candidate.is_file():
                image_path = candidate
                break
        if image_path:
            break

    if not image_path:
        raise HTTPException(status_code=404, detail=f"Source image not found: {source_file}")

    faces = await asyncio.to_thread(detect_faces, str(image_path))
    if not faces:
        raise HTTPException(status_code=404, detail=f"No faces detected in source image for '{name}'")

    # Sort by face area descending (most prominent first)
    for f in faces:
        bx1, by1, bx2, by2 = f.get("bbox", [0, 0, 0, 0])
        f["_area"] = max(0, (bx2 - bx1)) * max(0, (by2 - by1))
    faces.sort(key=lambda f: f.get("_area", 0), reverse=True)
    for f in faces:
        f.pop("_area", None)

    # If only one person or one face, use direct area-based mapping
    if len(all_person_nodes) <= 1 or len(faces) <= 1:
        face_index = 0
        import re
        match = re.search(r"(\d+)", name)
        if match:
            face_index = min(int(match.group(1)) - 1, len(faces) - 1)
    else:
        # Use VLM to match face crops to person descriptions
        person_desc = (person_node.get("properties", {}).get("description") or "").lower()

        # Crop each face to a temp file for VLM description
        try:
            img = await asyncio.to_thread(Image.open, str(image_path))
            img_w, img_h = img.size
        except Exception:
            img = None

        best_face_idx = 0
        best_score = -1

        if img and person_desc:
            face_descriptions = []
            for idx, face in enumerate(faces):
                bbox = face.get("bbox", [])
                if len(bbox) != 4:
                    face_descriptions.append("")
                    continue
                x1, y1, x2, y2 = bbox
                fw, fh = x2 - x1, y2 - y1
                px, py = int(fw * 0.3), int(fh * 0.3)
                cx1, cy1 = max(0, x1 - px), max(0, y1 - py)
                cx2, cy2 = min(img_w, x2 + px), min(img_h, y2 + py)

                tmp_path = FACES_CACHE_DIR / f"_tmp_{name}_{idx}.jpg"
                try:
                    cropped = img.crop((cx1, cy1, cx2, cy2))
                    cropped.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    await asyncio.to_thread(cropped.save, str(tmp_path), "JPEG", quality=85)
                    desc = await describe_face_crop(str(tmp_path))
                    face_descriptions.append(desc.lower())
                except Exception as exc:
                    logger.warning("VLM face crop failed for face %d: %s", idx, exc)
                    face_descriptions.append("")
                finally:
                    if tmp_path.is_file():
                        tmp_path.unlink()

            # Match by attribute overlap between VLM face desc and person description
            desc_lower = person_desc
            _HAIR_MAP = {"bald": {"bald", "shaved", "hairless"}, "blonde": {"blonde", "blond", "light-haired"}, "brown": {"brown-haired", "brown"}, "black": {"black-haired", "dark-haired", "dark hair"}, "red": {"red-haired", "redhead", "ginger"}}
            _BUILD_MAP = {"muscular": {"muscular", "muscular", "athletic", "fit", "toned"}, "slim": {"slim", "thin", "lean", "skinny"}, "heavy": {"heavy", "large", "big", "overweight", "stocky"}}
            _CLOTHING_MAP = {"shirtless": {"shirtless", "no shirt", "bare-chested", "topless"}, "t-shirt": {"t-shirt", "tshirt", "tee shirt"}, "shorts": {"shorts"}, "dress": {"dress", "gown"}, "suit": {"suit", "jacket", "blazer"}, "hat": {"hat", "cap", "beanie"}}

            def extract_attrs(text: str) -> set[str]:
                attrs = set()
                t = text.lower()
                for attr, words in {**_HAIR_MAP, **_BUILD_MAP, **_CLOTHING_MAP}.items():
                    for w in words:
                        if w in t:
                            attrs.add(attr)
                            break
                # Direct word matches for common attributes
                for word in ["bald", "beard", "mustache", "glasses", "hat"]:
                    if word in t:
                        attrs.add(word)
                # Color words
                for color in ["white", "black", "grey", "gray", "blue", "red", "dark", "light"]:
                    if color in t:
                        attrs.add(f"color_{color}")
                return attrs

            person_attrs = extract_attrs(desc_lower)
            for idx, fdesc in enumerate(face_descriptions):
                if not fdesc:
                    continue
                face_attrs = extract_attrs(fdesc)
                overlap = len(person_attrs & face_attrs)
                logger.info("Face %d match: desc='%s' attrs=%s overlap=%d (person_attrs=%s)", idx, fdesc[:60], face_attrs, overlap, person_attrs)
                if overlap > best_score:
                    best_score = overlap
                    best_face_idx = idx

        face_index = best_face_idx

    face = faces[face_index]
    bbox = face.get("bbox", [])
    if len(bbox) != 4:
        raise HTTPException(status_code=500, detail=f"Invalid face bounding box for '{name}'")

    x1, y1, x2, y2 = bbox
    face_w = x2 - x1
    face_h = y2 - y1
    pad_x = int(face_w * 0.3)
    pad_y = int(face_h * 0.3)

    try:
        img = await asyncio.to_thread(Image.open, str(image_path))
        img_w, img_h = img.size
        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_y)
        cx2 = min(img_w, x2 + pad_x)
        cy2 = min(img_h, y2 + pad_y)
        cropped = img.crop((cx1, cy1, cx2, cy2))
        cropped.thumbnail((256, 256), Image.Resampling.LANCZOS)
        await asyncio.to_thread(cropped.save, str(cache_path), "JPEG", quality=85)
        img_bytes = io.BytesIO()
        cropped.save(img_bytes, format="JPEG", quality=85)
        img_bytes.seek(0)
        return Response(content=img_bytes.getvalue(), media_type="image/jpeg")
    except Exception as exc:
        logger.error("Failed to crop face for '%s': %s", name, exc)
        raise HTTPException(status_code=500, detail=f"Failed to crop face: {exc}")


@router.post("/faces/label")
async def label_face(request: dict[str, Any]):
    """Label a detected face: save a reference photo and rename the LightRAG entity.

    Accepts JSON ``{face_id, new_name}``. Saves the existing crop to
    ``known_faces/{new_name}/reference.jpg`` (making it a DeepFace reference),
    renames the LightRAG person entity via ``POST /graph/entity/edit``, and
    updates ``face_mapping.json`` with the new name.
    """
    import httpx

    face_id = request.get("face_id")
    new_name = request.get("new_name")
    if not face_id or not new_name:
        raise HTTPException(status_code=400, detail="face_id and new_name are required")
    if not isinstance(face_id, str) or not isinstance(new_name, str):
        raise HTTPException(status_code=400, detail="face_id and new_name must be strings")

    safe_new = new_name.replace("/", "_").replace("\\", "_").replace("..", "").strip()
    if not safe_new:
        raise HTTPException(status_code=400, detail="new_name is invalid")

    mapping = _load_face_mapping()
    entry = mapping.get(face_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"face_id '{face_id}' not found in face mapping")

    crop_file = entry.get("crop_file") or f"{face_id}.jpg"
    crop_path = FACES_CACHE_DIR / crop_file
    if not crop_path.is_file():
        raise HTTPException(status_code=404, detail=f"Crop file '{crop_file}' not found for face_id '{face_id}'")

    known_root = Path(KNOWN_FACES_PATH)
    ref_dir = known_root / safe_new
    ref_path = ref_dir / "reference.jpg"
    try:
        ref_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        await asyncio.to_thread(shutil.copyfile, str(crop_path), str(ref_path))
        logger.info("Saved reference photo for '%s' → %s", safe_new, ref_path)
    except Exception as exc:
        logger.error("Failed to save reference photo for '%s': %s", safe_new, exc)
        raise HTTPException(status_code=500, detail=f"Failed to save reference photo: {exc}")

    # Find the LightRAG entity that has this face_id property
    old_name = entry.get("name", "")
    entity_renamed = False
    lightag_entity_name: str | None = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{LIGHTRAG_URL}/graph/label/list")
            labels = resp.json() if resp.status_code == 200 else []
        for label in labels:
            if label.endswith((" (Date)", " (Camera)", " (Location)", " (Photo)")):
                continue
            async with httpx.AsyncClient(timeout=10) as client:
                lr = await client.get(
                    f"{LIGHTRAG_URL}/graphs",
                    params={"label": label},
                )
            if lr.status_code != 200:
                continue
            graph_data = lr.json()
            for node in graph_data.get("nodes", []):
                if node.get("id") != label:
                    continue
                props = node.get("properties", {})
                et = props.get("entity_type", "")
                if et.lower() not in ("person", "people"):
                    continue
                if props.get("face_id") == face_id:
                    lightag_entity_name = label
                    break
            if lightag_entity_name:
                break
    except Exception as exc:
        logger.warning("Failed to find LightRAG entity for face_id '%s': %s", face_id, exc)

    if lightag_entity_name and lightag_entity_name != new_name:
        payload = json.dumps({"entity_name": lightag_entity_name, "updated_data": {"entity_name": new_name}, "allow_rename": True}).encode("utf-8")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{LIGHTRAG_URL}/graph/entity/edit",
                    content=payload,
                    headers={"Content-Type": "application/json"},
                )
            if resp.status_code == 404:
                logger.warning("Entity '%s' not found in LightRAG — skipping rename", lightag_entity_name)
            elif resp.status_code >= 400:
                logger.warning("LightRAG entity edit returned %d for '%s': %s", resp.status_code, lightag_entity_name, resp.text[:200])
            else:
                entity_renamed = True
                logger.info("Renamed LightRAG entity '%s' → '%s'", lightag_entity_name, new_name)
        except httpx.RequestError as exc:
            logger.error("LightRAG entity edit request failed for '%s': %s", lightag_entity_name, exc)
            raise HTTPException(status_code=502, detail=f"Failed to reach LightRAG: {exc}")
    else:
        logger.info("Skipping entity rename (entity not found or name unchanged)")

    entry["name"] = new_name
    mapping[face_id] = entry
    mapping_path = FACES_CACHE_DIR / "face_mapping.json"
    try:
        tmp = mapping_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(mapping, indent=2))
        tmp.replace(mapping_path)
    except OSError as exc:
        logger.error("Failed to update face_mapping.json: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to update face mapping: {exc}")

    return {"status": "ok", "face_id": face_id, "old_name": lightag_entity_name or old_name, "new_name": new_name, "entity_renamed": entity_renamed}


@router.delete("/photo-entities")
async def delete_photo_entities_endpoint(file_source: str):
    """Delete the Photo node and EXIF entities for a file source.

    Call this after deleting a document from LightRAG to clean up the
    manually created Photo and EXIF entities that persist after deletion.
    """
    result = await delete_photo_entities(LIGHTRAG_URL, file_source)
    return result


@router.post("/reprocess")
async def reprocess_image(
    file_source: str = Form(...),
    skip_exif: Optional[str] = Form(None),
    skip_faces: Optional[str] = Form(None),
):
    """Re-process an image that already exists in the INPUT_DIR.

    Finds the file by its original filename and re-runs the full processing
    pipeline (EXIF, faces, VLM, LightRAG insert). Returns an SSE stream.
    """
    skip_exif_bool = _parse_bool(skip_exif)
    skip_faces_bool = await _resolve_skip_faces(_parse_bool(skip_faces))

    file_path = INPUT_DIR / file_source
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_source}")

    event_generator = _process_and_stream(
        str(file_path),
        file_source,
        skip_exif=skip_exif_bool,
        skip_faces=skip_faces_bool,
        insert=True,
    )
    return EventSourceResponse(event_generator)


@router.post("/jobs")
async def create_image_job(
    file: UploadFile = File(...),
    skip_exif: Optional[str] = Form(None),
    skip_faces: Optional[str] = Form(None),
    insert: Optional[str] = Form(None),
):
    skip_exif_bool = _parse_bool(skip_exif)
    skip_faces_bool = await _resolve_skip_faces(_parse_bool(skip_faces))
    insert_bool = _parse_bool(insert, default=True)

    file_source = file.filename or "uploaded_image"
    suffix = os.path.splitext(file_source)[1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()
        persisted_path = await persist_uploaded_file(tmp.name, file_source)
    except Exception:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise

    job = await create_job(
        file_source=file_source,
        file_path=persisted_path,
        skip_exif=skip_exif_bool,
        skip_faces=skip_faces_bool,
        insert=insert_bool,
    )
    await start_processing(job)
    return {"job_id": job.id, "status": job.status, "file_source": job.file_source}


@router.get("/jobs")
async def list_image_jobs(status: Optional[str] = None):
    jobs = await list_jobs(status)
    return [
        {
            "job_id": j.id,
            "file_source": j.file_source,
            "status": j.status,
            "stage": j.stage,
            "error": j.error if j.error else None,
            "created_at": j.created_at,
            "updated_at": j.updated_at,
        }
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_image_job(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.id,
        "file_source": job.file_source,
        "status": job.status,
        "stage": job.stage,
        "error": job.error if job.error else None,
        "skip_exif": job.skip_exif,
        "skip_faces": job.skip_faces,
        "insert": job.insert,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


@router.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str, after: int = 0):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        queue = subscribe_events(job_id)
        try:
            past_events = await get_events(job_id, after_event_id=after)
            for evt in past_events:
                event_data = evt["event_data"]
                if isinstance(event_data, str):
                    try:
                        event_data = json.loads(event_data)
                    except (json.JSONDecodeError, TypeError):
                        event_data = {"raw": event_data}
                yield ServerSentEvent(
                    event="message",
                    data=json.dumps({
                        "event": evt["event_type"],
                        "data": event_data,
                        "event_id": evt["id"],
                        "timestamp": evt["created_at"],
                    }),
                )

            if job.status in ("complete", "failed", "cancelled"):
                unsubscribe_events(job_id)
                return

            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    current = await get_job(job_id)
                    if current and current.status in ("complete", "failed", "cancelled"):
                        yield ServerSentEvent(
                            event="message",
                            data=json.dumps({
                                "event": "pipeline_complete" if current.status == "complete" else "pipeline_failed",
                                "data": {"file_source": current.file_source, "status": current.status, "error": current.error},
                                "timestamp": time.time(),
                            }),
                        )
                        break
                    yield ServerSentEvent(event="heartbeat", data="")
                    continue

                yield ServerSentEvent(
                    event="message",
                    data=json.dumps({
                        "event": evt["event_type"],
                        "data": evt["event_data"] if isinstance(evt["event_data"], dict) else json.loads(evt["event_data"]) if isinstance(evt["event_data"], str) else {"raw": evt["event_data"]},
                        "timestamp": time.time(),
                    }),
                )

                current = await get_job(job_id)
                if current and current.status in ("complete", "failed", "cancelled"):
                    break
        finally:
            unsubscribe_events(job_id)

    return EventSourceResponse(event_stream())