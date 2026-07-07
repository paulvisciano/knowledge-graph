#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from processors.exif_extractor import extract_exif_metadata
from processors.face_recognizer import analyze_attributes, identify_person

logger = logging.getLogger(__name__)


def process_exif(image_path: str) -> dict[str, Any] | None:
    try:
        result = extract_exif_metadata(image_path)
        logger.info("EXIF extraction succeeded for %s", image_path)
        return result
    except Exception:
        logger.exception("EXIF extraction failed for %s", image_path)
        return None


def process_faces(
    image_path: str, known_faces_path: str
) -> dict[str, Any] | None:
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


def build_captions(
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


def build_content_list(image_path: str, captions: list[str]) -> list[dict[str, Any]]:
    return [{
        "type": "image",
        "image_caption": captions,
        "image_path": str(Path(image_path).resolve()),
    }]


def check_lightrag_health(lightrag_url: str) -> bool:
    url = f"{lightrag_url.rstrip('/')}/health"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        logger.exception("LightRAG health check failed at %s", url)
        return False


def insert_into_lightrag(
    lightrag_url: str,
    content_list: list[dict[str, Any]],
    image_path: str,
) -> dict[str, Any] | None:
    text_parts: list[str] = []
    for item in content_list:
        if item.get("type") == "image" and item.get("image_caption"):
            text_parts.extend(item["image_caption"])

    if not text_parts:
        logger.warning("No caption text to insert for %s", image_path)
        return None

    text = "\n\n".join(text_parts)
    payload = json.dumps({
        "text": text,
        "file_source": str(Path(image_path).resolve()),
    }).encode("utf-8")

    url = f"{lightrag_url.rstrip('/')}/documents/text"
    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            logger.info("LightRAG response (%d): %s", resp.status, body[:500])
            return json.loads(body)
    except Exception:
        logger.exception("LightRAG insertion failed for %s", image_path)
        return None


def process_image(
    image_path: str,
    *,
    known_faces_path: str,
    skip_exif: bool = False,
    skip_faces: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "image_path": str(Path(image_path).resolve()),
        "exif": None,
        "faces": None,
    }

    if not skip_exif:
        result["exif"] = process_exif(image_path)

    if not skip_faces:
        result["faces"] = process_faces(image_path, known_faces_path)

    captions = build_captions(result["exif"], result["faces"], image_path)
    result["captions"] = captions
    result["content_list"] = build_content_list(image_path, captions)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process images through EXIF extraction and face recognition, "
                    "then output JSON or insert into LightRAG.",
    )
    parser.add_argument("images", nargs="+", metavar="IMAGE", help="Image file paths to process")
    parser.add_argument("--lightrag-url", default="http://localhost:9621", help="LightRAG API base URL")
    parser.add_argument("--known-faces", default=None, help="Path to known_faces directory (default: ../known_faces)")
    parser.add_argument("--json", dest="output_json", action="store_true", help="Output JSON instead of inserting into LightRAG")
    parser.add_argument("--no-exif", action="store_true", help="Skip EXIF extraction")
    parser.add_argument("--no-faces", action="store_true", help="Skip face recognition")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    known_faces_path = (
        str(Path(args.known_faces).resolve())
        if args.known_faces
        else str((_SCRIPT_DIR.parent / "known_faces").resolve())
    )

    for img in args.images:
        if not os.path.isfile(img):
            logger.error("Image file not found: %s", img)
            return 1

    if not args.no_faces and not os.path.isdir(known_faces_path):
        logger.error("Known faces directory not found: %s", known_faces_path)
        return 1

    if not args.output_json:
        if not check_lightrag_health(args.lightrag_url):
            logger.error("LightRAG not reachable at %s — aborting", args.lightrag_url)
            return 1

    results: list[dict[str, Any]] = []
    exit_code = 0

    for image_path in args.images:
        logger.info("Processing %s ...", image_path)
        try:
            result = process_image(
                image_path,
                known_faces_path=known_faces_path,
                skip_exif=args.no_exif,
                skip_faces=args.no_faces,
            )
            results.append(result)

            if not args.output_json:
                response = insert_into_lightrag(args.lightrag_url, result["content_list"], image_path)
                if response is None:
                    logger.error("Failed to insert %s into LightRAG", image_path)
                    exit_code = 1
                else:
                    logger.info("Successfully inserted %s into LightRAG", image_path)
        except Exception:
            logger.exception("Unexpected error processing %s", image_path)
            exit_code = 1

    if args.output_json:
        json.dump(results, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())