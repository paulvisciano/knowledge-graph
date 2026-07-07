from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.request import Request, urlopen

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from processors.exif_extractor import extract_exif_metadata
from processors.face_recognizer import analyze_attributes, identify_person

logger = logging.getLogger("api.processor")

LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://localhost:9621")


@dataclass
class ProcessingEvent:
    event: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProcessingResult:
    exif: dict[str, Any] | None
    faces: dict[str, Any] | None
    captions: list[str]
    content_list: list[dict[str, Any]]
    events: list[ProcessingEvent]


def _build_metadata_text(
    exif_data: dict[str, Any] | None,
    face_data: dict[str, Any] | None,
    image_path: str,
) -> str:
    """Build structured text from extracted metadata for LightRAG text insertion.

    This becomes a separate knowledge graph document containing all the
    EXIF/face information so entities like locations, people, dates, and
    camera settings become queryable nodes.
    """
    sections: list[str] = []

    filename = Path(image_path).name
    sections.append(f"Image metadata for {filename}")

    if exif_data:
        lines: list[str] = []
        date = exif_data.get("date_taken_friendly") or exif_data.get("date_taken")
        if date:
            lines.append(f"Date taken: {date}")
        camera = exif_data.get("camera") or exif_data.get("camera_make") or exif_data.get("camera_model")
        if camera:
            lines.append(f"Camera: {camera}")
        location = exif_data.get("location")
        if location:
            if isinstance(location, str):
                lines.append(f"Location: {location}")
            elif isinstance(location, dict):
                loc_str = ", ".join(str(v) for v in location.values() if v)
                if loc_str:
                    lines.append(f"Location: {loc_str}")
        for key in ("f_number", "exposure_time", "iso", "focal_length"):
            val = exif_data.get(key)
            if val:
                lines.append(f"{key}: {val}")
        gps = exif_data.get("gps")
        if gps and isinstance(gps, dict):
            lat = gps.get("latitude")
            lon = gps.get("longitude")
            if lat and lon:
                lines.append(f"GPS coordinates: {lat}, {lon}")
        if lines:
            sections.append("\n".join(lines))

    if face_data and face_data.get("faces"):
        face_lines: list[str] = []
        for face in face_data["faces"]:
            name = face.get("name", "Unknown person")
            descriptors: list[str] = []
            for attr in ("age", "gender", "emotion", "race"):
                val = face.get(attr)
                if val:
                    descriptors.append(f"{attr}: {val}")
            if descriptors:
                face_lines.append(f"{name} ({', '.join(descriptors)})")
            else:
                face_lines.append(name)
        if face_lines:
            sections.append("People in this image: " + "; ".join(face_lines))

    metadata_text = exif_data.get("metadata_text") if exif_data else None
    if metadata_text:
        sections.append(metadata_text)

    return "\n\n".join(sections) if sections else f"Image: {filename}"


def _build_captions(
    exif_data: dict[str, Any] | None,
    face_data: dict[str, Any] | None,
    image_path: str,
) -> list[str]:
    captions: list[str] = []

    if exif_data:
        metadata_text = exif_data.get("metadata_text")

        if metadata_text:
            captions.append(metadata_text)
        else:
            parts: list[str] = []
            date = exif_data.get("date_taken_friendly") or exif_data.get("date_taken")
            if date:
                parts.append(f"Photo taken on {date}")

            camera = exif_data.get("camera")
            if not camera:
                camera = exif_data.get("camera_make") or exif_data.get("camera_model")
            if camera:
                parts.append(f"with {camera}")

            location = exif_data.get("location")
            if location:
                loc_str = (
                    location if isinstance(location, str)
                    else ", ".join(str(v) for v in location.values() if v)
                    if isinstance(location, dict)
                    else str(location)
                )
                if loc_str:
                    parts.append(f"at {loc_str}")

            settings_parts: list[str] = []
            for key in ("f_number", "exposure_time", "iso", "focal_length"):
                val = exif_data.get(key)
                if val:
                    settings_parts.append(str(val))
            if settings_parts:
                parts.append(", ".join(settings_parts))

            if parts:
                caption = " ".join(parts).replace("  ", " ").strip()
                if not caption.endswith("."):
                    caption += "."
                captions.append(caption)

    if face_data and face_data.get("faces"):
        person_descriptions: list[str] = []
        for face in face_data["faces"]:
            name = face.get("name", "Unknown")
            descriptors: list[str] = []
            for attr in ("emotion", "age", "gender", "race"):
                val = face.get(attr)
                if val:
                    descriptors.append(str(val))
            if descriptors:
                person_descriptions.append(f"{name} ({', '.join(descriptors)})")
            else:
                person_descriptions.append(name)
        if person_descriptions:
            captions.append("Identified persons: " + ", ".join(person_descriptions))

    if not captions:
        captions.append(f"Image: {Path(image_path).name}")

    return captions


def _build_content_list(image_path: str, captions: list[str]) -> list[dict[str, Any]]:
    return [{
        "type": "image",
        "image_caption": captions,
        "image_path": str(Path(image_path).resolve()),
    }]


def _process_exif(image_path: str) -> dict[str, Any] | None:
    try:
        result = extract_exif_metadata(image_path)
        logger.info("EXIF extraction succeeded for %s", image_path)
        return result
    except Exception:
        logger.exception("EXIF extraction failed for %s", image_path)
        return None


def _process_faces(image_path: str, known_faces_path: str) -> dict[str, Any] | None:
    try:
        identifications = identify_person(image_path, known_faces_path)
        attributes = analyze_attributes(image_path)
        combined: list[dict[str, Any]] = []

        if isinstance(identifications, list):
            attr_list = attributes if isinstance(attributes, list) else []
            for idx, person in enumerate(identifications):
                name = person.get("identity", "Unknown") if isinstance(person, dict) else str(person)
                face_attrs = attr_list[idx] if idx < len(attr_list) else {}
                combined.append({
                    "name": name,
                    "age": face_attrs.get("age"),
                    "gender": face_attrs.get("gender"),
                    "emotion": face_attrs.get("emotion"),
                    "race": face_attrs.get("race"),
                })

        logger.info("Face recognition succeeded for %s — %d face(s)", image_path, len(combined))
        return {"faces": combined}
    except Exception:
        logger.exception("Face recognition failed for %s", image_path)
        return None


async def process_image(
    image_path: str,
    known_faces_path: str,
    skip_exif: bool = False,
    skip_faces: bool = False,
) -> AsyncGenerator[ProcessingEvent, None]:
    exif_data: dict[str, Any] | None = None
    face_data: dict[str, Any] | None = None

    if not skip_exif:
        yield ProcessingEvent(event="extracting_exif", data={"image_path": image_path})
        exif_data = await asyncio.to_thread(_process_exif, image_path)
        yield ProcessingEvent(event="exif_complete", data={"exif": exif_data or {}})

    if not skip_faces:
        yield ProcessingEvent(event="detecting_faces", data={"image_path": image_path})
        face_data = await asyncio.to_thread(_process_faces, image_path, known_faces_path)
        yield ProcessingEvent(event="faces_complete", data={"faces": face_data or {}})

    captions = _build_captions(exif_data, face_data, image_path)
    content_list = _build_content_list(image_path, captions)
    metadata_text = _build_metadata_text(exif_data, face_data, image_path)

    yield ProcessingEvent(event="captions_built", data={
        "captions": captions,
        "content_list": content_list,
        "metadata_text": metadata_text,
    })


async def upload_image_to_lightrag(
    lightrag_url: str,
    image_path: str,
    filename: str | None = None,
) -> dict[str, Any]:
    """Upload the image file to LightRAG for VLM processing and entity extraction.

    This triggers the full LightRAG pipeline: VLM describes the image,
    then entities and relationships are extracted from the VLM description.

    Returns the LightRAG API response.
    """
    import httpx

    url = f"{lightrag_url.rstrip('/')}/documents/upload"
    file_name = filename or Path(image_path).name

    async with httpx.AsyncClient(timeout=300) as client:
        with open(image_path, "rb") as f:
            files = {"file": (file_name, f)}
            resp = await client.post(url, files=files)

    logger.info("LightRAG upload response (%d): %s", resp.status_code, resp.text[:500])

    if resp.status_code != 200:
        logger.error("LightRAG upload failed: %d %s", resp.status_code, resp.text)
        return {"status": "error", "reason": f"upload_failed_{resp.status_code}", "detail": resp.text[:500]}

    return resp.json()


async def insert_metadata_into_lightrag(
    lightrag_url: str,
    metadata_text: str,
    file_source: str,
) -> dict[str, Any]:
    """Insert extracted metadata as text into LightRAG.

    This creates a separate knowledge graph document with EXIF locations,
    face names, dates, camera settings, etc. as structured entities
    alongside the VLM-generated description from the image upload.
    """
    payload = json.dumps({
        "text": metadata_text,
        "file_source": f"{file_source}:metadata",
    }).encode("utf-8")

    url = f"{lightrag_url.rstrip('/')}/documents/text"

    def _post() -> dict[str, Any]:
        req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            logger.info("LightRAG metadata insertion (%d): %s", resp.status, body[:500])
            return json.loads(body)

    try:
        return await asyncio.to_thread(_post)
    except Exception:
        logger.exception("LightRAG metadata insertion failed for %s", file_source)
        return {"status": "error", "reason": "metadata_insertion_failed"}