from __future__ import annotations

import json
import logging
import os
import time
import asyncio
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, UploadFile
from sse_starlette.sse import ServerSentEvent, EventSourceResponse

from api.services.processor import (
    ProcessingEvent, process_image, upload_image_to_lightrag,
    describe_image_with_vlm, create_exif_relations,
    link_exif_to_visual_entities, wait_for_lightrag_processing,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])

LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://localhost:9621")
KNOWN_FACES_PATH = os.environ.get("KNOWN_FACES_PATH", str(Path(__file__).resolve().parent.parent.parent / "known_faces"))


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


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

    # Phase 1: Extract metadata (EXIF, faces, VLM captions)
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
        yield ServerSentEvent(
            event="message",
            data=json.dumps(asdict(event)),
        )

    if not insert:
        yield ServerSentEvent(
            event="message",
            data=json.dumps({"event": "pipeline_complete", "data": {"file_source": file_source, "status": "no_insert"}, "timestamp": time.time()}),
        )
        return

    # Phase 2: Build EXIF dimensions, emit SSE events for frontend,
    # and create entities in LightRAG BEFORE uploading the document
    # (when the pipeline is idle and won't return 409).
    photo_name = f"{file_source} (Photo)"
    exif_dimensions: list[dict[str, Any]] = []

    if exif_data:
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

        # Create entities in LightRAG now — pipeline is idle, no 409 conflict
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

    # Phase 3: Upload to LightRAG (VLM description + insert)
    yield ServerSentEvent(
        event="message",
        data=json.dumps({"event": "describing_image", "data": {"file_source": file_source}, "timestamp": time.time()}),
    )
    try:
        upload_result = await upload_image_to_lightrag(
            LIGHTRAG_URL, file_path, filename=file_source, metadata_text=metadata_text,
        )
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
    # Poll until LightRAG has finished processing and visual entities appear
    initial_label_count = len(exif_dimensions) + 1 if exif_dimensions else 0
    for _poll in range(30):
        await asyncio.sleep(3.0)
        try:
            import urllib.request as _urllib_request
            def _count_labels() -> int:
                req = _urllib_request.Request(f"{LIGHTRAG_URL}/graph/label/list", headers={"Content-Type": "application/json"})
                with _urllib_request.urlopen(req, timeout=10) as resp:
                    return len(json.loads(resp.read().decode("utf-8")))
            label_count = await asyncio.to_thread(_count_labels)
            if label_count > initial_label_count:
                await asyncio.sleep(2.0)
                break
        except Exception:
            pass

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
    skip_faces_bool = _parse_bool(skip_faces)
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
    skip_faces_bool = _parse_bool(skip_faces)
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