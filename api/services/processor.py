from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.error import URLError
from urllib.request import Request, urlopen

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from processors.exif_extractor import extract_exif_metadata
from processors.face_recognizer import analyze_attributes, identify_person

logger = logging.getLogger("api.processor")

LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://localhost:9621")
VLM_URL = os.environ.get("VLM_LLM_BINDING_HOST", "http://host.docker.internal:8080")
VLM_MODEL = os.environ.get("VLM_LLM_MODEL", "gemma-4-12B-it-qat-UD-Q4_K_XL")
VLM_API_KEY = os.environ.get("VLM_LLM_BINDING_API_KEY", "llama-server")
FACE_DETECTOR = os.environ.get("FACE_DETECTOR_BACKEND", "opencv")
FACE_ATTRIBUTES = os.environ.get("FACE_ATTRIBUTES", "age,gender,emotion,race").split(",")


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
    """Build contextual metadata text for embedding alongside a VLM description.

    Produces factual sentences about the image (location, camera, date, people)
    designed to be combined with the VLM visual description so the entity
    extraction LLM sees one coherent document rather than disconnected sections.
    """
    has_location = False
    has_date = False
    has_camera = False
    has_faces = False
    location_clause = ""
    date_clause = ""
    camera_clause = ""
    faces_clause = ""

    if exif_data:
        location = exif_data.get("location")
        if location:
            loc_str = location if isinstance(location, str) else ", ".join(str(v) for v in location.values() if v)
            if loc_str:
                location_clause = f"in {loc_str}"
                has_location = True
        date = exif_data.get("date_taken_friendly") or exif_data.get("date_taken")
        if date:
            date_clause = f"on {date}"
            has_date = True
        camera = exif_data.get("camera") or exif_data.get("camera_make") or exif_data.get("camera_model")
        if camera:
            camera_clause = f"with a {camera}"
            has_camera = True

    if face_data and face_data.get("faces"):
        face_parts: list[str] = []
        for face in face_data["faces"]:
            name = face.get("name", "Unknown person")
            descriptors: list[str] = []
            for attr in ("age", "gender", "emotion", "race"):
                val = face.get(attr)
                if val:
                    descriptors.append(f"{attr}: {val}")
            if descriptors:
                face_parts.append(f"{name} ({', '.join(descriptors)})")
            else:
                face_parts.append(name)
        if face_parts:
            faces_clause = "showing " + ", ".join(face_parts)
            has_faces = True

    if not (has_location or has_date or has_camera or has_faces):
        filename = Path(image_path).name
        return f"Image: {filename}"

    fragments: list[str] = []
    if has_location:
        fragments.append(location_clause)
    if has_date:
        fragments.append(date_clause)
    if has_camera:
        fragments.append(camera_clause)

    taken_part = ""
    if fragments:
        taken_part = "taken " + ", ".join(fragments)

    if has_faces and taken_part:
        sentence = f"This image was {taken_part}, and {faces_clause}."
    elif has_faces:
        sentence = f"This image was {faces_clause}."
    elif taken_part:
        sentence = f"This image was {taken_part}."
    else:
        filename = Path(image_path).name
        return f"Image: {filename}"

    detail_lines: list[str] = []
    if exif_data:
        for key in ("f_number", "exposure_time", "iso", "focal_length"):
            val = exif_data.get(key)
            if val:
                detail_lines.append(f"{key}: {val}")
        gps = exif_data.get("gps")
        if gps and isinstance(gps, dict):
            lat = gps.get("latitude")
            lon = gps.get("longitude")
            if lat and lon:
                detail_lines.append(f"GPS coordinates: {lat}, {lon}")

    if detail_lines:
        sentence += "\n" + "\n".join(detail_lines)

    return sentence


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
    """Detect faces and analyze attributes, optionally identifying known persons.

    Always detects faces and analyzes attributes (age, gender, emotion, race).
    Only attempts identification against known faces if the database directory
    exists and contains reference photos.
    """
    try:
        attributes = analyze_attributes(
            image_path,
            actions=[a.strip() for a in FACE_ATTRIBUTES if a.strip()],
            detector_backend=FACE_DETECTOR,
        )
        if not isinstance(attributes, list) or not attributes:
            logger.info("No faces detected in %s", image_path)
            return {"faces": []}

        identifications: list[dict[str, Any]] = []
        db_path = Path(known_faces_path)
        if db_path.exists() and any(db_path.iterdir()):
            id_result = identify_person(image_path, known_faces_path)
            if isinstance(id_result, list):
                identifications = id_result

        combined: list[dict[str, Any]] = []
        for idx, face_attrs in enumerate(attributes):
            name = "Unknown"
            if idx < len(identifications) and isinstance(identifications[idx], dict):
                identity = identifications[idx].get("identity", "Unknown")
                if identity and identity != "Unknown":
                    name = identity

            combined.append({
                "name": name,
                "age": face_attrs.get("age"),
                "gender": face_attrs.get("gender"),
                "emotion": face_attrs.get("emotion"),
                "race": face_attrs.get("race"),
            })

        logger.info("Face analysis succeeded for %s — %d face(s)", image_path, len(combined))
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
        "exif_data": exif_data,
    })


async def describe_image_with_vlm(
    image_path: str,
    vlm_url: str | None = None,
    vlm_model: str | None = None,
    context: str | None = None,
) -> str:
    """Send an image to the VLM and return a text description.

    Uses the OpenAI-compatible chat completions endpoint with multimodal input
    to generate a detailed description of the image content.
    """
    import httpx

    url = (vlm_url or VLM_URL).rstrip("/")
    model = vlm_model or VLM_MODEL

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")

    ext = Path(image_path).suffix.lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}
    mime_type = mime_map.get(ext, "image/jpeg")

    prompt = (
        "Analyze this image in detail for a knowledge graph. "
        "Identify every visible element using specific proper nouns where possible "
        "(e.g., brand names, model numbers, landmark names, specific locations). "
        "Describe spatial relationships (foreground, background, left, right, above, below). "
        "Transcribe any visible text verbatim — exact spelling, numbers, and labels. "
        "Note colors, materials, and quantities. "
        "Describe people with physical attributes (hair color, approximate age, clothing, expression). "
        "Identify the scene type (indoor, outdoor, event, activity). "
        f"{'Context: ' + context if context else ''}"
    )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    headers = {"Content-Type": "application/json"}
    if VLM_API_KEY:
        headers["Authorization"] = f"Bearer {VLM_API_KEY}"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{url}/v1/chat/completions", json=payload, headers=headers)

    if resp.status_code != 200:
        logger.error("VLM request failed: %d %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"VLM request failed: {resp.status_code} {resp.text[:200]}")

    result = resp.json()
    description = result["choices"][0]["message"]["content"]
    logger.info("VLM description generated (%d chars) for %s", len(description), image_path)
    return description


async def upload_image_to_lightrag(
    lightrag_url: str,
    image_path: str,
    filename: str | None = None,
    metadata_text: str | None = None,
) -> dict[str, Any]:
    """Process an image through VLM and insert the description into LightRAG.

    LightRAG's /documents/upload only accepts text-based file types, so we
    can't upload .jpg directly. Instead, we:
    1. Send the image to the VLM (Gemma 4 with mmproj) to generate a description
    2. Combine the VLM description with any available metadata (EXIF, faces)
    3. Insert the combined text as a single document into LightRAG

    Combining VLM description and metadata into one document ensures that
    entities from both sources (e.g. location from EXIF, objects from VLM)
    are extracted together and properly linked in the knowledge graph,
    rather than creating disconnected subgraphs.
    """
    try:
        vlm_context = None
        if metadata_text:
            vlm_context = metadata_text.split("\n")[0]
        description = await describe_image_with_vlm(image_path, context=vlm_context)
    except Exception as exc:
        logger.exception("VLM description failed for %s", image_path)
        return {"status": "error", "reason": "vlm_description_failed", "detail": str(exc)}

    file_name = filename or Path(image_path).name

    combined_text = f"Image: {file_name}\n\n{description}"

    return await insert_metadata_into_lightrag(
        lightrag_url,
        combined_text,
        file_name,
    )


async def insert_metadata_into_lightrag(
    lightrag_url: str,
    metadata_text: str,
    file_source: str,
) -> dict[str, Any]:
    """Insert text content into LightRAG as a document.

    Used for both combined VLM+metadata documents and standalone metadata.
    The file_source is used as-is for the document identifier.
    """
    payload = json.dumps({
        "text": metadata_text,
        "file_source": file_source,
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


async def inject_exif_relations(
    lightrag_url: str,
    file_source: str,
    exif_data: dict[str, Any],
) -> dict[str, Any]:
    """Create a photo entity node and EXIF metadata entity nodes, then link them.

    Creates:
    1. A photo entity node for the image file
    2. EXIF entity nodes (Date, Location, Camera)
    3. Edges from the photo node to each EXIF entity (taken_on, taken_at, taken_with)
    4. Edges between EXIF entities (Date↔Location, Date↔Camera, Location↔Camera)

    The photo node becomes the hub that connects EXIF metadata to the
    LLM-extracted visual entities that link_exif_to_visual_entities adds later.
    """
    base_url = lightrag_url.rstrip("/")

    photo_name = f"{file_source} (Photo)"
    photo_description = f"Photo: {file_source}"

    exif_dimensions: list[dict[str, str]] = []

    date_taken_friendly = exif_data.get("date_taken_friendly")
    if date_taken_friendly:
        date_only = date_taken_friendly.split(" at ")[0].split(" ")[0]
        exif_dimensions.append({
            "name": f"{date_only} (Date)",
            "entity_type": "Date",
            "description": f"Calendar date {date_taken_friendly}",
            "edge_keyword": "taken_on",
            "edge_description": f"Photo taken on {date_taken_friendly}",
        })

    location = exif_data.get("location")
    if location:
        loc_str = location if isinstance(location, str) else ", ".join(
            str(v) for v in location.values() if v
        ) if isinstance(location, dict) else str(location)
        if loc_str:
            exif_dimensions.append({
                "name": f"{loc_str} (Location)",
                "entity_type": "Location",
                "description": f"Location in {loc_str}",
                "edge_keyword": "taken_at",
                "edge_description": f"Photo taken at {loc_str}",
            })

    camera = exif_data.get("camera") or exif_data.get("camera_make") or exif_data.get("camera_model")
    if camera:
        exif_dimensions.append({
            "name": f"{camera} (Camera)",
            "entity_type": "Camera",
            "description": f"Camera device: {camera}",
            "edge_keyword": "taken_with",
            "edge_description": f"Photo taken with {camera}",
        })

    if not exif_dimensions:
        logger.info("[EXIF Relations] No EXIF dimensions to inject for %s", file_source)
        return {"status": "skipped", "reason": "no_exif_dimensions", "exif_dimensions": []}

    results: dict[str, Any] = {"entities_created": [], "relations_created": [], "exif_dimensions": exif_dimensions, "photo_name": photo_name}

    # Create the photo entity node
    photo_payload = json.dumps({
        "entity_name": photo_name,
        "entity_data": {
            "description": photo_description,
            "entity_type": "Photo",
            "source_id": file_source,
        },
    }).encode("utf-8")

    try:
        def _create_photo(payload: bytes = photo_payload) -> dict[str, Any]:
            req = Request(
                f"{base_url}/graph/entity/create",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                logger.info("[EXIF Relations] Created photo entity '%s': %d", photo_name, resp.status)
                return json.loads(body)

        photo_result = await asyncio.to_thread(_create_photo)
        results["entities_created"].append(photo_result)
    except URLError as exc:
        if "409" in str(exc) or "Conflict" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
            logger.info("[EXIF Relations] Photo entity '%s' already exists", photo_name)
            results["entities_created"].append({"status": "exists", "entity_name": photo_name})
        else:
            logger.warning("[EXIF Relations] Failed to create photo entity '%s': %s", photo_name, exc)
            results["entities_created"].append({"status": "error", "entity_name": photo_name, "error": str(exc)})
    except Exception as exc:
        logger.warning("[EXIF Relations] Unexpected error creating photo entity '%s': %s", photo_name, exc)
        results["entities_created"].append({"status": "error", "entity_name": photo_name, "error": str(exc)})

    # Create EXIF entity nodes and link them to the photo node and to each other.
    # For each EXIF dimension, check if a similar entity already exists (e.g.,
    # LightRAG may have created "Saint Petersburg" from the VLM text).
    # If so, link to the existing entity instead of creating a duplicate.
    existing_labels = set()
    try:
        def _get_labels() -> list[str]:
            req = Request(f"{base_url}/graph/label/list", headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        existing_labels = set(await asyncio.to_thread(_get_labels))
    except Exception as exc:
        logger.warning("[EXIF Relations] Failed to get existing labels: %s", exc)

    for dim in exif_dimensions:
        entity_name = dim["name"]
        raw_value = dim["name"].rsplit(" (", 1)[0]

        target_entity = entity_name
        if entity_name in existing_labels:
            target_entity = entity_name
        elif raw_value in existing_labels:
            target_entity = raw_value
            logger.info("[EXIF Relations] Using existing entity '%s' instead of '%s'", raw_value, entity_name)
        else:
            for label in existing_labels:
                if not label.endswith((" (Date)", " (Camera)", " (Location)", " (Photo)")):
                    if raw_value.lower().startswith(label.lower()) or label.lower().startswith(raw_value.lower()):
                        target_entity = label
                        logger.info("[EXIF Relations] Using existing entity '%s' as match for '%s'", label, entity_name)
                        break

        entity_payload = json.dumps({
            "entity_name": entity_name,
            "entity_data": {
                "description": dim["description"],
                "entity_type": dim["entity_type"],
                "source_id": file_source,
            },
        }).encode("utf-8")

        try:
            def _create_entity(payload: bytes = entity_payload, name: str = entity_name) -> dict[str, Any]:
                req = Request(
                    f"{base_url}/graph/entity/create",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8")
                    logger.info("[EXIF Relations] Created entity '%s': %d", name, resp.status)
                    return json.loads(body)

            entity_result = await asyncio.to_thread(_create_entity)
            results["entities_created"].append(entity_result)
        except URLError as exc:
            if "409" in str(exc) or "Conflict" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                # 409/400 may be stale — verify the entity actually exists and retry if not
                if target_entity not in existing_labels:
                    logger.info("[EXIF Relations] Entity '%s' reported conflict but not found in graph, retrying creation", target_entity)
                    try:
                        def _retry_create(payload: bytes = entity_payload, name: str = target_entity) -> dict[str, Any]:
                            req = Request(
                                f"{base_url}/graph/entity/create",
                                data=payload,
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            with urlopen(req, timeout=30) as resp:
                                body = resp.read().decode("utf-8")
                                logger.info("[EXIF Relations] Retry created entity '%s': %d", name, resp.status)
                                return json.loads(body)

                        entity_result = await asyncio.to_thread(_retry_create)
                        results["entities_created"].append(entity_result)
                    except Exception as retry_exc:
                        logger.warning("[EXIF Relations] Retry creation of '%s' also failed: %s", target_entity, retry_exc)
                        results["entities_created"].append({"status": "exists", "entity_name": entity_name})
                else:
                    logger.info("[EXIF Relations] Entity '%s' already exists in graph, proceeding", target_entity)
                    results["entities_created"].append({"status": "exists", "entity_name": entity_name})
            else:
                logger.warning("[EXIF Relations] Failed to create entity '%s': %s", entity_name, exc)
                results["entities_created"].append({"status": "error", "entity_name": entity_name, "error": str(exc)})
        except Exception as exc:
            logger.warning("[EXIF Relations] Unexpected error creating entity '%s': %s", entity_name, exc)
            results["entities_created"].append({"status": "error", "entity_name": entity_name, "error": str(exc)})

        # Link photo -> EXIF entity
        relation_payload = json.dumps({
            "source_entity": photo_name,
            "target_entity": entity_name,
            "relation_data": {
                "description": dim["edge_description"],
                "keywords": dim["edge_keyword"],
                "weight": 1.0,
            },
        }).encode("utf-8")

        try:
            def _create_relation(payload: bytes = relation_payload, src: str = photo_name, tgt: str = entity_name) -> dict[str, Any]:
                req = Request(
                    f"{base_url}/graph/relation/create",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8")
                    logger.info("[EXIF Relations] Created relation '%s' -> '%s': %d", src, tgt, resp.status)
                    return json.loads(body)

            relation_result = await asyncio.to_thread(_create_relation)
            results["relations_created"].append(relation_result)
        except URLError as exc:
            if "409" in str(exc) or "Conflict" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                results["relations_created"].append({"status": "exists", "source": photo_name, "target": entity_name})
            else:
                logger.warning("[EXIF Relations] Failed to create relation '%s' -> '%s': %s", photo_name, entity_name, exc)
                results["relations_created"].append({"status": "error", "source": photo_name, "target": entity_name, "error": str(exc)})
        except Exception as exc:
            logger.warning("[EXIF Relations] Unexpected error creating relation '%s' -> '%s': %s", photo_name, entity_name, exc)
            results["relations_created"].append({"status": "error", "source": photo_name, "target": entity_name, "error": str(exc)})

        # Link EXIF entities to each other
        for other_dim in exif_dimensions:
            if other_dim["name"] == entity_name:
                continue
            cross_payload = json.dumps({
                "source_entity": entity_name,
                "target_entity": other_dim["name"],
                "relation_data": {
                    "description": f"{dim['edge_description']}, also {other_dim['edge_keyword']} {other_dim['name']}",
                    "keywords": dim["edge_keyword"],
                    "weight": 1.0,
                },
            }).encode("utf-8")

            try:
                def _create_cross(payload: bytes = cross_payload, src: str = entity_name, tgt: str = other_dim["name"]) -> dict[str, Any]:
                    req = Request(
                        f"{base_url}/graph/relation/create",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(req, timeout=30) as resp:
                        body = resp.read().decode("utf-8")
                        return json.loads(body)

                results["relations_created"].append(await asyncio.to_thread(_create_cross))
            except URLError as exc:
                if "409" in str(exc) or "Conflict" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    results["relations_created"].append({"status": "exists", "source": entity_name, "target": other_dim["name"]})
                else:
                    results["relations_created"].append({"status": "error", "source": entity_name, "target": other_dim["name"], "error": str(exc)})
            except Exception as exc:
                results["relations_created"].append({"status": "error", "source": entity_name, "target": other_dim["name"], "error": str(exc)})

    logger.info("[EXIF Relations] Injection complete for %s: %d entities, %d relations",
                file_source, len(results["entities_created"]), len(results["relations_created"]))
    return results


async def link_exif_to_visual_entities(
    lightrag_url: str,
    file_source: str,
    photo_name: str,
) -> dict[str, Any]:
    """Link LLM-extracted visual entities to the photo node by finding graph
    entities that share the document's file_path.

    After LightRAG processes a document, it creates visual entities (Backyard,
    Person, etc.) with the document's file_path. This function finds those entities
    and creates edges from them to the photo node, making the photo the central hub.
    """
    base_url = lightrag_url.rstrip("/")
    results: dict[str, Any] = {"visual_links_created": []}

    try:
        def _get_labels() -> list[str]:
            req = Request(f"{base_url}/graph/label/list", headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        all_labels = await asyncio.to_thread(_get_labels)
    except Exception as exc:
        logger.warning("[EXIF Links] Failed to get graph labels: %s", exc)
        return results

    exif_suffixes = (" (Date)", " (Camera)", " (Location)", " (Photo)")

    for label in all_labels:
        if label.endswith(exif_suffixes):
            continue

        try:
            def _get_neighbors(l: str = label) -> dict:
                from urllib.parse import quote
                req = Request(
                    f"{base_url}/graphs?label={quote(l, safe='')}",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            graph_data = await asyncio.to_thread(_get_neighbors)
        except Exception as exc:
            logger.warning("[EXIF Links] Failed to get neighbors for '%s': %s", label, exc)
            continue

        for node in graph_data.get("nodes", []):
            if node.get("id") != label:
                continue
            props = node.get("properties", {})
            if props.get("file_path") != file_source:
                continue

            relation_payload = json.dumps({
                "source_entity": label,
                "target_entity": photo_name,
                "relation_data": {
                    "description": f"{label} appears in {file_source}",
                    "keywords": "appears_in",
                    "weight": 1.0,
                },
            }).encode("utf-8")

            try:
                def _create_link(payload: bytes = relation_payload, src: str = label) -> dict[str, Any]:
                    req = Request(
                        f"{base_url}/graph/relation/create",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(req, timeout=30) as resp:
                        body = resp.read().decode("utf-8")
                        logger.info("[EXIF Links] Linked '%s' -> '%s'", src, photo_name)
                        return json.loads(body)

                link_result = await asyncio.to_thread(_create_link)
                results["visual_links_created"].append({"source": label, "target": photo_name, "status": "success"})
            except URLError as exc:
                if "409" in str(exc) or "Conflict" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    results["visual_links_created"].append({"source": label, "target": photo_name, "status": "exists"})
                else:
                    logger.warning("[EXIF Links] Failed to link '%s' -> '%s': %s", label, photo_name, exc)
                    results["visual_links_created"].append({"source": label, "target": photo_name, "status": "error", "error": str(exc)})
            except Exception as exc:
                logger.warning("[EXIF Links] Unexpected error linking '%s' -> '%s': %s", label, photo_name, exc)
                results["visual_links_created"].append({"source": label, "target": photo_name, "status": "error", "error": str(exc)})
            break

    logger.info("[EXIF Links] Linking complete for %s: %d visual links created",
                file_source, len(results["visual_links_created"]))
    return results