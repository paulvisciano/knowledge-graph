from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile
from sse_starlette.sse import ServerSentEvent, EventSourceResponse

from api.services.processor import ProcessingEvent, process_image, upload_image_to_lightrag, describe_image_with_vlm, inject_exif_relations, link_exif_to_visual_entities

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

    if insert:
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

        if exif_data:
            yield ServerSentEvent(
                event="message",
                data=json.dumps({"event": "injecting_exif_relations", "data": {"file_source": file_source}, "timestamp": time.time()}),
            )
            try:
                injection_result = await inject_exif_relations(LIGHTRAG_URL, file_source, exif_data)
                yield ServerSentEvent(
                    event="message",
                    data=json.dumps({"event": "exif_relations_complete", "data": injection_result, "timestamp": time.time()}),
                )
            except Exception as exc:
                logger.exception("EXIF relation injection failed for %s", file_source)
                yield ServerSentEvent(
                    event="message",
                    data=json.dumps({"event": "exif_relations_failed", "data": {"error": str(exc)}, "timestamp": time.time()}),
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

        if insert_bool:
            upload_result = None

            try:
                upload_result = await upload_image_to_lightrag(
                    LIGHTRAG_URL, tmp.name, filename=file_source, metadata_text=metadata_text,
                )
                events.append(ProcessingEvent(event="upload_complete", data=upload_result))
            except Exception as exc:
                events.append(ProcessingEvent(event="upload_failed", data={"error": str(exc)}))

            if exif_data:
                try:
                    injection_result = await inject_exif_relations(LIGHTRAG_URL, file_source, exif_data)
                    events.append(ProcessingEvent(event="exif_relations_complete", data=injection_result))
                except Exception as exc:
                    logger.exception("EXIF relation injection failed for %s", file_source)
                    events.append(ProcessingEvent(event="exif_relations_failed", data={"error": str(exc)}))

        return {"events": [asdict(e) for e in events]}
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


@router.get("/health")
async def images_health():
    return {"status": "ok", "lightrag_url": LIGHTRAG_URL, "known_faces_path": KNOWN_FACES_PATH}


@router.post("/link-exif-entities")
async def link_exif_entities(file_source: str, photo_name: str):
    """Link LLM-extracted visual entities to the photo node in the knowledge graph.

    Call this after LightRAG has finished processing a document. It finds
    entities with matching file_path and creates edges from them to the photo node.
    """
    result = await link_exif_to_visual_entities(LIGHTRAG_URL, file_source, photo_name)
    return result