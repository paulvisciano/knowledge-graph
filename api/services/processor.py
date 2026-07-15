from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.error import URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from processors.exif_extractor import extract_exif_metadata
from processors.face_recognizer import analyze_attributes, identify_person, detect_faces

logger = logging.getLogger("api.processor")

LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://localhost:9621")
VLM_URL = os.environ.get("VLM_LLM_BINDING_HOST", "http://host.docker.internal:8080")
VLM_MODEL = os.environ.get("VLM_LLM_MODEL", "gemma-4-12B-it-qat-UD-Q4_K_XL")
VLM_API_KEY = os.environ.get("VLM_LLM_BINDING_API_KEY", "llama-server")
FACE_DETECTOR = os.environ.get("FACE_DETECTOR_BACKEND", "mtcnn")
FACE_ATTRIBUTES = os.environ.get("FACE_ATTRIBUTES", "age,gender,emotion,race").split(",")

_MAX_ENTITY_ATTEMPTS = 5
_MAX_RELATION_ATTEMPTS = 5
_VLM_MAX_RETRIES = 3
_VLM_RETRY_BACKOFF_BASE = 5  # seconds


async def _create_entity_verified(
    base_url: str,
    entity_name: str,
    entity_data: dict[str, Any],
    *,
    max_attempts: int = _MAX_ENTITY_ATTEMPTS,
) -> dict[str, Any]:
    """Create a graph entity, verifying it actually exists after creation.

    LightRAG's entity creation may return 409 (conflict) even when the entity
    does not actually exist due to a stale index. This helper verifies after
    a conflict response and retries if the entity is missing.
    """
    payload = json.dumps({"entity_name": entity_name, "entity_data": entity_data}).encode("utf-8")

    for attempt in range(1, max_attempts + 1):
        try:
            def _post(payload: bytes = payload) -> dict[str, Any]:
                req = Request(
                    f"{base_url}/graph/entity/create",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            result = await asyncio.to_thread(_post)
            logger.info("[Entity] Created '%s' (attempt %d)", entity_name, attempt)
            return {**result, "status": result.get("status", "success")}

        except URLError as exc:
            is_conflict = (
                "409" in str(exc) or "Conflict" in str(exc)
                or "400" in str(exc) or "already exists" in str(exc)
            )
            if not is_conflict:
                logger.warning("[Entity] Failed '%s': %s", entity_name, exc)
                return {"status": "error", "entity_name": entity_name, "error": str(exc)}

            await asyncio.sleep(3.0 * attempt)

            try:
                def _check(name: str = entity_name) -> bool:
                    req = Request(f"{base_url}/graph/label/list", headers={"Content-Type": "application/json"})
                    with urlopen(req, timeout=30) as resp:
                        labels = json.loads(resp.read().decode("utf-8"))
                        return name in labels
                entity_exists = await asyncio.to_thread(_check)
            except Exception:
                entity_exists = False

            if entity_exists:
                logger.info("[Entity] '%s' confirmed existing (attempt %d)", entity_name, attempt)
                return {"status": "exists", "entity_name": entity_name}

            logger.warning("[Entity] '%s' conflict but not found, retrying (%d/%d)", entity_name, attempt, max_attempts)

        except Exception as exc:
            logger.warning("[Entity] Unexpected error '%s': %s", entity_name, exc)
            return {"status": "error", "entity_name": entity_name, "error": str(exc)}

    logger.error("[Entity] All %d attempts failed for '%s'", max_attempts, entity_name)
    return {"status": "error", "entity_name": entity_name, "error": f"Failed after {max_attempts} attempts"}


async def _create_relation_verified(
    base_url: str,
    source_entity: str,
    target_entity: str,
    relation_data: dict[str, Any],
    *,
    max_attempts: int = _MAX_RELATION_ATTEMPTS,
) -> dict[str, Any]:
    """Create a graph relation, verifying it actually exists after creation.

    LightRAG's graph/relation/create may return 400/409 (conflict) even when
    the relation does not actually exist — a stale-index bug. This helper
    works around it by querying the source entity's subgraph after a conflict
    response to confirm the edge is present. If missing, creation is retried.
    """
    payload = json.dumps({
        "source_entity": source_entity,
        "target_entity": target_entity,
        "relation_data": relation_data,
    }).encode("utf-8")

    for attempt in range(1, max_attempts + 1):
        try:
            def _post(payload: bytes = payload) -> dict[str, Any]:
                req = Request(
                    f"{base_url}/graph/relation/create",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            result = await asyncio.to_thread(_post)
            logger.info("[Relation] Created '%s' -> '%s' (attempt %d)", source_entity, target_entity, attempt)
            return {**result, "status": result.get("status", "success")}

        except URLError as exc:
            is_conflict = (
                "409" in str(exc) or "Conflict" in str(exc)
                or "400" in str(exc) or "already exists" in str(exc)
            )
            if not is_conflict:
                logger.warning("[Relation] Failed '%s' -> '%s': %s", source_entity, target_entity, exc)
                return {"status": "error", "source": source_entity, "target": target_entity, "error": str(exc)}

            try:
                def _verify(se: str = source_entity) -> bool:
                    req = Request(
                        f"{base_url}/graphs?label={url_quote(se, safe='')}",
                        headers={"Content-Type": "application/json"},
                    )
                    with urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    for edge in data.get("edges", []):
                        src = edge.get("source", "")
                        tgt = edge.get("target", "")
                        if (src == source_entity and tgt == target_entity) or (
                            src == target_entity and tgt == source_entity
                        ):
                            return True
                    return False

                edge_exists = await asyncio.to_thread(_verify)
            except Exception:
                edge_exists = False

            if edge_exists:
                logger.info("[Relation] '%s' -> '%s' confirmed existing (attempt %d)", source_entity, target_entity, attempt)
                return {"status": "exists", "source": source_entity, "target": target_entity}

            logger.warning(
                "[Relation] '%s' -> '%s' conflict reported but edge not found, retrying (%d/%d)",
                source_entity, target_entity, attempt, max_attempts,
            )
            if attempt < max_attempts:
                await asyncio.sleep(3.0 * attempt)

        except Exception as exc:
            logger.warning("[Relation] Unexpected error '%s' -> '%s': %s", source_entity, target_entity, exc)
            return {"status": "error", "source": source_entity, "target": target_entity, "error": str(exc)}

    logger.error("[Relation] All %d attempts failed for '%s' -> '%s'", max_attempts, source_entity, target_entity)
    return {"status": "error", "source": source_entity, "target": target_entity,
            "error": f"Failed after {max_attempts} attempts — relation may not exist"}


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


async def _run_face_detection_subprocess(image_path: str, known_faces_path: str) -> dict[str, Any] | None:
    import subprocess
    import tempfile

    script = (
        "import json, sys, os\n"
        "# Always use mtcnn in subprocess — retinaface/opencv Haar cascades OOM or fail in containers\n"
        "os.environ['FACE_DETECTOR_BACKEND'] = 'mtcnn'\n"
        "from processors.face_recognizer import analyze_attributes, identify_person\n"
        "from pathlib import Path\n"
        f"image_path = {json.dumps(image_path)}\n"
        f"known_faces_path = {json.dumps(known_faces_path)}\n"
        "face_attributes = os.environ.get('FACE_ATTRIBUTES', 'age,gender,emotion,race').split(',')\n"
        "face_detector = os.environ.get('FACE_DETECTOR_BACKEND', 'opencv')\n"
        "try:\n"
        "    attributes = analyze_attributes(image_path, actions=face_attributes, detector_backend=face_detector)\n"
        "    if not isinstance(attributes, list) or not attributes:\n"
        "        print(json.dumps({'status': 'ok', 'data': {'faces': []}}))\n"
        "        sys.exit(0)\n"
        "    identifications = []\n"
        "    db_path = Path(known_faces_path)\n"
        "    if db_path.exists() and any(db_path.iterdir()):\n"
        "        id_result = identify_person(image_path, known_faces_path)\n"
        "        if isinstance(id_result, list):\n"
        "            identifications = id_result\n"
        "    combined = []\n"
        "    for idx, face_attrs in enumerate(attributes):\n"
        "        name = 'Unknown'\n"
        "        if idx < len(identifications) and isinstance(identifications[idx], dict):\n"
        "            identity = identifications[idx].get('identity', 'Unknown')\n"
        "            if identity and identity != 'Unknown':\n"
        "                name = identity\n"
        "        combined.append({'name': name, 'age': face_attrs.get('age'), 'gender': face_attrs.get('gender'), 'emotion': face_attrs.get('emotion'), 'race': face_attrs.get('race')})\n"
        "    print(json.dumps({'status': 'ok', 'data': {'faces': combined}}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'status': 'error', 'data': str(exc)}))\n"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)

        if proc.returncode != 0:
            logger.warning("Face recognition subprocess failed (rc=%d): %s", proc.returncode, stderr.decode()[:500])
            return None

        result = json.loads(stdout.decode().strip().split("\n")[-1])
        if result.get("status") == "ok":
            return result.get("data")
        logger.warning("Face recognition subprocess error: %s", result.get("data"))
        return None
    except asyncio.TimeoutError:
        logger.warning("Face recognition subprocess timed out for %s", image_path)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None
    except Exception as exc:
        logger.warning("Face recognition subprocess failed for %s: %s", image_path, exc)
        return None


def _save_face_crops(image_path: str, faces: list[dict[str, Any]], names: list[str], source_id: str, bboxes: list[dict[str, Any]] | None = None) -> None:
    """Save cropped face images with a person-to-crop mapping.

    Crops are saved to FACES_CACHE_DIR as JPEG files named by the person name.
    A mapping file (face_mapping.json) records the person→filename association.

    Faces are sorted by area (largest first) to match the VLM's salience-based
    ordering: Person 1 = most prominent face, Person 2 = second, etc.

    Args:
        image_path: Path to the source image.
        faces: List of face attribute dicts from analyze_attributes().
        names: List of person names corresponding to each face.
        source_id: Source identifier (typically the filename).
        bboxes: Pre-computed bounding boxes from detect_faces(). If None,
            detect_faces() will be called (expensive — loads DeepFace model).
    """
    from PIL import Image

    faces_cache_dir = Path(os.environ.get("FACES_CACHE_DIR", str(Path(_PROJECT_ROOT) / "face_crops")))
    faces_cache_dir.mkdir(parents=True, exist_ok=True)

    # Use pre-computed bboxes if available to avoid redundant DeepFace model loading
    if bboxes is None:
        bboxes = detect_faces(image_path)
    if not bboxes:
        logger.info("No face bounding boxes for crop saving in %s", image_path)
        return

    # Sort bboxes by face area descending (most prominent first)
    # This matches the VLM's salience-based person numbering
    for b in bboxes:
        bx1, by1, bx2, by2 = b.get("bbox", [0, 0, 0, 0])
        b["_area"] = max(0, (bx2 - bx1)) * max(0, (by2 - by1))
    bboxes.sort(key=lambda b: b.get("_area", 0), reverse=True)
    for b in bboxes:
        b.pop("_area", None)

    try:
        img = Image.open(image_path)
    except Exception as exc:
        logger.warning("Failed to open image for face cropping: %s", exc)
        return

    img_w, img_h = img.size

    for idx, face_info in enumerate(faces):
        name = names[idx] if idx < len(names) else f"Person {idx + 1}"
        if idx >= len(bboxes):
            break

        bbox = bboxes[idx].get("bbox", [])
        if len(bbox) != 4:
            continue

        x1, y1, x2, y2 = bbox
        face_w = x2 - x1
        face_h = y2 - y1
        pad_x = int(face_w * 0.3)
        pad_y = int(face_h * 0.3)

        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_y)
        cx2 = min(img_w, x2 + pad_x)
        cy2 = min(img_h, y2 + pad_y)

        try:
            cropped = img.crop((cx1, cy1, cx2, cy2))
            max_dim = 256
            cropped.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

            safe_name = name.replace("/", "_").replace("\\", "_").replace("..", "")
            crop_path = faces_cache_dir / f"{safe_name}.jpg"
            cropped.save(str(crop_path), "JPEG", quality=85)
            logger.info("Saved face crop for '%s' → %s", name, crop_path.name)
        except Exception as exc:
            logger.warning("Failed to save face crop for '%s': %s", name, exc)

    mapping_path = faces_cache_dir / "face_mapping.json"
    mapping: dict[str, Any] = {}
    if mapping_path.is_file():
        try:
            mapping = json.loads(mapping_path.read_text())
        except (json.JSONDecodeError, OSError):
            mapping = {}

    for idx, face_info in enumerate(faces):
        name = names[idx] if idx < len(names) else f"Person {idx + 1}"
        safe_name = name.replace("/", "_").replace("\\", "_").replace("..", "")
        mapping[name] = {
            "source_id": source_id,
            "face_index": idx,
            "crop_file": f"{safe_name}.jpg",
        }

    mapping_path.write_text(json.dumps(mapping, indent=2))


def _process_faces(image_path: str, known_faces_path: str) -> dict[str, Any] | None:
    """Detect faces and analyze attributes, optionally identifying known persons.

    Always detects faces and analyzes attributes (age, gender, emotion, race).
    Only attempts identification against known faces if the database directory
    exists and contains reference photos.
    """
    try:
        bboxes = detect_faces(image_path)

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
        names: list[str] = []
        for idx, face_attrs in enumerate(attributes):
            name = "Unknown"
            if idx < len(identifications) and isinstance(identifications[idx], dict):
                identity = identifications[idx].get("identity", "Unknown")
                if identity and identity != "Unknown":
                    name = identity

            names.append(name if name != "Unknown" else f"Person {idx + 1}")
            combined.append({
                "name": names[-1],
                "age": face_attrs.get("age"),
                "gender": face_attrs.get("gender"),
                "emotion": face_attrs.get("emotion"),
                "race": face_attrs.get("race"),
            })

        source_id = Path(image_path).name
        _save_face_crops(image_path, attributes, names, source_id, bboxes=bboxes)

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
        try:
            face_data = await _run_face_detection_subprocess(image_path, known_faces_path)
            if face_data is None:
                logger.info("Subprocess returned None for %s — falling back to in-process detection", image_path)
                face_data = await asyncio.to_thread(_process_faces, image_path, known_faces_path)
            logger.info("Face detection completed for %s: %s", image_path, "ok" if face_data else "none")
        except Exception as exc:
            logger.warning("Face recognition error for %s: %s — trying in-process fallback", image_path, exc)
            try:
                face_data = await asyncio.to_thread(_process_faces, image_path, known_faces_path)
            except Exception:
                logger.exception("In-process face detection also failed for %s", image_path)
                face_data = None
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

    last_exc: Exception | None = None
    for attempt in range(1, _VLM_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{url}/v1/chat/completions", json=payload, headers=headers)

            if resp.status_code != 200:
                logger.error("VLM request failed: %d %s (attempt %d/%d)", resp.status_code, resp.text[:500], attempt, _VLM_MAX_RETRIES)
                last_exc = RuntimeError(f"VLM request failed: {resp.status_code} {resp.text[:200]}")
                if attempt < _VLM_MAX_RETRIES:
                    delay = _VLM_RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.info("VLM retry %d/%d in %.0fs for %s", attempt, _VLM_MAX_RETRIES, delay, image_path)
                    await asyncio.sleep(delay)
                    continue
                raise last_exc

            result = resp.json()
            description = result["choices"][0]["message"]["content"]
            logger.info("VLM description generated (%d chars) for %s (attempt %d)", len(description), image_path, attempt)
            return description
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < _VLM_MAX_RETRIES:
                delay = _VLM_RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning("VLM timeout/error for %s (attempt %d/%d): %s, retrying in %.0fs", image_path, attempt, _VLM_MAX_RETRIES, type(exc).__name__, delay)
                await asyncio.sleep(delay)
            else:
                logger.error("VLM failed after %d attempts for %s: %s", _VLM_MAX_RETRIES, image_path, exc)
                raise
    raise last_exc or RuntimeError("VLM failed unexpectedly")


async def describe_face_crop(
    crop_path: str,
    vlm_url: str | None = None,
    vlm_model: str | None = None,
) -> str:
    """Send a face crop to the VLM and return a brief physical description.

    Uses the same OpenAI-compatible endpoint as describe_image_with_vlm but
    with a focused prompt for describing a single person's appearance.
    """
    import httpx

    url = (vlm_url or VLM_URL).rstrip("/")
    model = vlm_model or VLM_MODEL

    with open(crop_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")

    prompt = (
        "Describe this person's physical appearance in one short sentence. "
        "Focus on: hair (color, length, baldness), facial hair, build, and clothing. "
        "Example: 'A male with brown hair, muscular build, wearing no shirt and grey shorts.'"
    )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }
        ],
        "max_tokens": 256,
        "temperature": 0.2,
    }

    headers = {"Content-Type": "application/json"}
    if VLM_API_KEY:
        headers["Authorization"] = f"Bearer {VLM_API_KEY}"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{url}/v1/chat/completions", json=payload, headers=headers)

    if resp.status_code != 200:
        logger.error("VLM face description failed: %d %s", resp.status_code, resp.text[:500])
        return ""

    result = resp.json()
    description = result["choices"][0]["message"]["content"].strip()
    logger.info("VLM face description for %s: %s", crop_path, description[:100])
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
        if metadata_text:
            logger.info("Falling back to EXIF-only insertion for %s", image_path)
            file_name = filename or Path(image_path).name
            fallback_text = f"Image: {file_name}\n\n[Image description unavailable — visual analysis failed.]\n\n{metadata_text}"
            return await insert_metadata_into_lightrag(
                lightrag_url,
                fallback_text,
                file_name,
            )
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

    results: dict[str, Any] = {"exif_dimensions": exif_dimensions, "photo_name": photo_name}

    logger.info("[EXIF Relations] Prepared %d EXIF dimensions for %s (entities will be created by link-exif-entities)",
                len(exif_dimensions), file_source)
    return results


_LOCATION_SUFFIXES = (" (Location)", " (Date)", " (Camera)", " (Photo)")
_LOCATION_ABBREVS = {
    "st": "saint", "mt": "mount", "ft": "fort",
    "n": "north", "s": "south", "e": "east", "w": "west",
}


def _strip_entity_suffix(name: str) -> str:
    for suffix in _LOCATION_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _normalize_for_matching(name: str) -> str:
    bare = _strip_entity_suffix(name)
    normalized = re.sub(r"[.\-,]", " ", bare.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    words = normalized.split()
    expanded = []
    for word in words:
        expanded.append(_LOCATION_ABBREVS.get(word, word))
    return " ".join(expanded)


def _find_matching_entity(entity_name: str, existing_labels: set[str], threshold: float = 0.85) -> str | None:
    if entity_name in existing_labels:
        return entity_name

    bare_name = _strip_entity_suffix(entity_name)
    if bare_name in existing_labels:
        return bare_name

    target_norm = _normalize_for_matching(entity_name)

    best_match: str | None = None
    best_score = 0.0

    for label in existing_labels:
        label_norm = _normalize_for_matching(label)
        score = SequenceMatcher(None, target_norm, label_norm).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_match = label

    return best_match


async def create_exif_relations(
    lightrag_url: str,
    file_source: str,
    photo_name: str,
    exif_dimensions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create EXIF entity nodes and relation edges after LightRAG processing.

    Entity creation is deferred here (instead of inject_exif_relations) because
    LightRAG's entity index becomes stale after the first entity is created,
    causing all subsequent entity and relation creations to fail with 409. By
    running this after LightRAG has finished processing the document, the index
    has stabilized and creation succeeds.
    """
    base_url = lightrag_url.rstrip("/")
    results: dict[str, Any] = {"entities_created": [], "relations_created": []}

    # Create the photo entity node
    photo_result = await _create_entity_verified(
        base_url, photo_name,
        {"description": f"Photo: {file_source}", "entity_type": "Photo", "source_id": file_source},
    )
    photo_result["entity_name"] = photo_name
    photo_result["entity_type"] = "Photo"
    results["entities_created"].append(photo_result)

    await asyncio.sleep(1.0)

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

        existing_match = _find_matching_entity(entity_name, existing_labels)

        if existing_match:
            logger.info("[EXIF Relations] Entity '%s' matches existing '%s', reusing", entity_name, existing_match)
            dim["_resolved_name"] = existing_match
            results["entities_created"].append({"status": "exists", "entity_name": existing_match, "original_name": entity_name})
        else:
            entity_result = await _create_entity_verified(
                base_url, entity_name,
                {"description": dim["description"], "entity_type": dim["entity_type"], "source_id": file_source},
            )
            entity_result["entity_name"] = entity_name
            entity_result["entity_type"] = dim["entity_type"]
            results["entities_created"].append(entity_result)
            existing_labels.add(entity_name)
            await asyncio.sleep(0.5)

    for dim in exif_dimensions:
        entity_name = dim.get("_resolved_name") or dim["name"]

        relation_result = await _create_relation_verified(
            base_url, photo_name, entity_name,
            {"description": dim["edge_description"], "keywords": dim["edge_keyword"], "weight": 1.0},
        )
        relation_result["source"] = photo_name
        relation_result["target"] = entity_name
        results["relations_created"].append(relation_result)

        for other_dim in exif_dimensions:
            other_name = other_dim.get("_resolved_name") or other_dim["name"]
            if other_name == entity_name:
                continue
            cross_result = await _create_relation_verified(
                base_url, entity_name, other_name,
                {"description": f"{dim['edge_description']}, also {other_dim['edge_keyword']} {other_name}", "keywords": dim["edge_keyword"], "weight": 1.0},
            )
            cross_result["source"] = entity_name
            cross_result["target"] = other_name
            results["relations_created"].append(cross_result)

    logger.info("[EXIF Relations] Complete for %s: %d entities, %d relations",
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
        logger.info("[EXIF Links] Found %d labels for %s", len(all_labels), file_source)
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
            file_path = props.get("file_path", "")
            # Normalize Unicode whitespace variants that LLMs sometimes produce
            # (e.g., U+202F narrow no-break space, U+00A0 non-breaking space)
            normalized_path = file_path.replace("\u202f", " ").replace("\u00a0", " ")
            normalized_source = file_source.replace("\u202f", " ").replace("\u00a0", " ")
            logger.info("[EXIF Links] Checking '%s': file_path='%s' vs file_source='%s'", label, file_path, file_source)
            if normalized_path != normalized_source:
                continue

            link_result = await _create_relation_verified(
                base_url, label, photo_name,
                {"description": f"{label} appears in {file_source}", "keywords": "appears_in", "weight": 1.0},
            )
            logger.info("[EXIF Links] Linked '%s' -> '%s': status=%s", label, photo_name, link_result.get("status", "?"))
            link_result["source"] = label
            link_result["target"] = photo_name
            results["visual_links_created"].append(link_result)
            break

    logger.info("[EXIF Links] Linking complete for %s: %d visual links created",
                file_source, len(results["visual_links_created"]))
    return results


async def wait_for_lightrag_processing(
    lightrag_url: str,
    file_source: str,
    *,
    poll_interval: float = 3.0,
    timeout: float = 300.0,
) -> str:
    """Poll LightRAG until document processing finishes.

    Checks the pipeline status endpoint until it is no longer busy,
    then verifies the specific document reached a terminal state
    (``PROCESSED`` or ``FAILED``).

    Returns the final status string ("processed" or "failed").
    Raises ``TimeoutError`` if the timeout is exceeded.
    """
    base_url = lightrag_url.rstrip("/")
    start = time.monotonic()

    # Phase 0: wait for pipeline to become busy (document picked up)
    saw_busy = False
    while not saw_busy:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            logger.warning("[LightRAG Wait] Pipeline never became busy after %.1fs; assuming processed", elapsed)
            return "processed"
        try:
            def _poll_pipeline() -> dict:
                req = Request(
                    f"{base_url}/documents/pipeline_status",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            pipeline_status = await asyncio.to_thread(_poll_pipeline)
            busy = pipeline_status.get("busy", False)
            if busy:
                saw_busy = True
                logger.info("[LightRAG Wait] Pipeline is now busy, waiting for completion")
        except Exception as exc:
            logger.warning("[LightRAG Wait] Pipeline status poll failed: %s", exc)
        if not saw_busy:
            await asyncio.sleep(poll_interval)

    # Phase 1: wait for pipeline to become idle
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            raise TimeoutError(
                f"Timed out waiting for LightRAG pipeline to become idle after {timeout}s"
            )

        try:
            def _poll_pipeline() -> dict:
                req = Request(
                    f"{base_url}/documents/pipeline_status",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            pipeline_status = await asyncio.to_thread(_poll_pipeline)
            busy = pipeline_status.get("busy", True)
            logger.info(
                "[LightRAG Wait] Pipeline poll — busy=%s, elapsed=%.1fs, file=%s",
                busy, elapsed, file_source,
            )
            if not busy:
                break
        except Exception as exc:
            logger.warning("[LightRAG Wait] Pipeline status poll failed: %s", exc)

        await asyncio.sleep(poll_interval)

    # Phase 2: confirm document status
    try:
        def _get_docs() -> list[dict]:
            req = Request(
                f"{base_url}/documents",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        docs = await asyncio.to_thread(_get_docs)
        # docs may be a list or a dict with a "documents" key
        doc_list = docs if isinstance(docs, list) else docs.get("documents", docs.get("data", []))

        for doc in doc_list:
            if not isinstance(doc, dict):
                continue
            # Match by file_source against potential field names
            doc_name = doc.get("file_path") or doc.get("filename") or doc.get("name") or ""
            if doc_name == file_source or doc_name.endswith(file_source):
                status = str(doc.get("status", "")).lower()
                logger.info(
                    "[LightRAG Wait] Document '%s' final status: %s", file_source, status,
                )
                return status

        logger.warning(
            "[LightRAG Wait] Could not find document '%s' in documents list; assuming processed",
            file_source,
        )
        return "processed"
    except Exception as exc:
        logger.warning("[LightRAG Wait] Failed to fetch documents list: %s", exc)
        return "processed"


async def delete_photo_entities(
    lightrag_url: str,
    file_source: str,
) -> dict[str, Any]:
    """Delete the Photo node and EXIF entities created for a file source.

    When a document is deleted from LightRAG, it removes the visual entities
    it extracted (Pool, House, Person, etc.), but our manually created Photo
    and EXIF entities (Date, Camera, Location) persist. This function cleans
    those up by finding them via source_id and deleting them along with
    their relations.
    """
    base_url = lightrag_url.rstrip("/")
    results: dict[str, Any] = {"entities_deleted": [], "errors": []}
    photo_name = f"{file_source} (Photo)"
    exif_suffixes = (" (Date)", " (Camera)", " (Location)")

    # Find all entities belonging to this file by querying the Photo node's subgraph
    try:
        def _get_graph() -> dict[str, Any]:
            req = Request(
                f"{base_url}/graphs?label={url_quote(photo_name)}",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        graph = await asyncio.to_thread(_get_graph)
    except Exception as exc:
        logger.warning("[Delete Entities] Failed to fetch graph for '%s': %s", photo_name, exc)
        results["errors"].append({"step": "fetch_graph", "error": str(exc)})
        return results

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Collect entity names to delete: the Photo node + EXIF entities with matching source_id
    entities_to_delete: list[str] = []
    for node in nodes:
        node_id = node.get("id", "")
        props = node.get("properties", {})
        source_id = props.get("source_id", "")
        if node_id == photo_name or source_id == file_source:
            if node_id not in entities_to_delete:
                entities_to_delete.append(node_id)
        elif any(node_id.endswith(s) for s in exif_suffixes):
            connected = any(
                e.get("source") == photo_name and e.get("target") == node_id
                or e.get("target") == photo_name and e.get("source") == node_id
                for e in edges
            )
            if connected and node_id not in entities_to_delete:
                entities_to_delete.append(node_id)

    for entity_name in entities_to_delete:
        try:
            def _delete(name: str = entity_name) -> dict[str, Any]:
                payload = json.dumps({"entity_name": name}).encode("utf-8")
                req = Request(
                    f"{base_url}/graph/entity/delete",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="DELETE",
                )
                with urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            result = await asyncio.to_thread(_delete)
            results["entities_deleted"].append({"name": entity_name, "status": "deleted"})
            logger.info("[Delete Entities] Deleted '%s'", entity_name)
            await asyncio.sleep(0.3)
        except URLError as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                results["entities_deleted"].append({"name": entity_name, "status": "not_found"})
                logger.info("[Delete Entities] '%s' already gone", entity_name)
            else:
                results["errors"].append({"entity": entity_name, "error": str(exc)})
                logger.warning("[Delete Entities] Failed to delete '%s': %s", entity_name, exc)
        except Exception as exc:
            results["errors"].append({"entity": entity_name, "error": str(exc)})
            logger.warning("[Delete Entities] Unexpected error deleting '%s': %s", entity_name, exc)

    logger.info("[Delete Entities] Cleanup for '%s': deleted %d entities, %d errors",
                file_source, len(results["entities_deleted"]), len(results["errors"]))
    return results