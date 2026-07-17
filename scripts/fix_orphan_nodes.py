#!/usr/bin/env python3.11
"""Find and repair orphan nodes in the LightRAG knowledge graph.

An orphan is a visual entity (extracted by the LLM from image descriptions)
that has a ``file_path`` property pointing to a source document, but no
``appears_in`` edge connecting it to the corresponding ``{file_source} (Photo)``
node.  This happened because ``link_exif_to_visual_entities`` did an exact
``file_path`` equality check, which failed for entities appearing in multiple
photos (LightRAG joins their ``file_path`` with ``<SEP>``).

This script:
  1. Fetches all graph labels.
  2. Skips EXIF-managed entities (Date/Camera/Location/Photo suffixes).
  3. For each visual entity, reads its ``file_path`` property.
  4. Splits on ``<SEP>`` to get the list of source documents.
  5. For each source, checks if a ``{source} (Photo)`` node exists.
  6. Checks if an ``appears_in`` edge already connects them.
  7. If not, creates the missing edge via ``/graph/relation/create``.

Usage:
  # Dry run — show what would be fixed
  python scripts/fix_orphan_nodes.py --dry-run

  # Apply repairs
  python scripts/fix_orphan_nodes.py

  # Custom LightRAG URL
  python scripts/fix_orphan_nodes.py --lightrag-url http://localhost:9621
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen
from urllib.error import URLError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_EXIF_SUFFIXES = (" (Date)", " (Camera)", " (Location)", " (Photo)")
_GRAPH_FIELD_SEP = "<SEP>"
_RELATION_KEYWORDS = "appears_in"


def _is_exif_entity(label: str) -> bool:
    return label.endswith(_EXIF_SUFFIXES)


def _photo_name_for(file_source: str) -> str:
    return f"{file_source} (Photo)"


def _api_get(base_url: str, path: str) -> Any:
    req = Request(f"{base_url}{path}", headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_post(base_url: str, path: str, payload: dict[str, Any]) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_all_labels(base_url: str) -> list[str]:
    return _api_get(base_url, "/graph/label/list")


def get_node_subgraph(base_url: str, label: str) -> dict[str, Any]:
    encoded = url_quote(label, safe="")
    return _api_get(base_url, f"/graphs?label={encoded}&max_depth=1&max_nodes=50")


def get_node_properties(subgraph: dict[str, Any], label: str) -> dict[str, Any]:
    for node in subgraph.get("nodes", []):
        if node.get("id") == label:
            return node.get("properties", {})
    return {}


def get_existing_edge_targets(subgraph: dict[str, Any], source_label: str) -> set[str]:
    targets: set[str] = set()
    for edge in subgraph.get("edges", []):
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src == source_label:
            targets.add(tgt)
        elif tgt == source_label:
            targets.add(src)
    return targets


def file_path_to_sources(file_path: str) -> list[str]:
    if not file_path or file_path == "unknown_source":
        return []
    normalized = file_path.replace("\u202f", " ").replace("\u00a0", " ")
    return [p.strip() for p in normalized.split(_GRAPH_FIELD_SEP) if p.strip()]


def create_relation(base_url: str, source: str, target: str, description: str) -> dict[str, Any]:
    return _api_post(base_url, "/graph/relation/create", {
        "source_entity": source,
        "target_entity": target,
        "relation_data": {
            "description": description,
            "keywords": _RELATION_KEYWORDS,
            "weight": 1.0,
        },
    })


def create_photo_entity(base_url: str, file_source: str) -> dict[str, Any]:
    photo_name = _photo_name_for(file_source)
    return _api_post(base_url, "/graph/entity/create", {
        "entity_name": photo_name,
        "entity_data": {
            "description": f"Photo: {file_source}",
            "entity_type": "Photo",
            "source_id": file_source,
        },
    })


def find_orphans(base_url: str, labels: list[str]) -> list[dict[str, Any]]:
    orphans: list[dict[str, Any]] = []
    photo_labels = {l for l in labels if l.endswith(" (Photo)")}

    visual_labels = [l for l in labels if not _is_exif_entity(l)]

    logger.info("Scanning %d labels (%d photos, %d visual entities)",
                len(labels), len(photo_labels), len(visual_labels))

    for i, label in enumerate(visual_labels, 1):
        try:
            subgraph = get_node_subgraph(base_url, label)
        except Exception as exc:
            logger.warning("  [%d/%d] Failed to fetch subgraph for '%s': %s", i, len(visual_labels), label, exc)
            continue

        props = get_node_properties(subgraph, label)
        file_path = props.get("file_path", "")
        sources = file_path_to_sources(file_path)

        if not sources:
            continue

        existing_targets = get_existing_edge_targets(subgraph, label)

        missing_links: list[str] = []
        missing_photos: list[str] = []
        for src in sources:
            normalized_src = src.replace("\u202f", " ").replace("\u00a0", " ")
            photo_name = _photo_name_for(normalized_src)
            if photo_name not in photo_labels:
                missing_photos.append(photo_name)
                continue
            if photo_name in existing_targets:
                continue
            missing_links.append(photo_name)

        if missing_links or missing_photos:
            orphans.append({
                "label": label,
                "file_path": file_path,
                "sources": sources,
                "missing_links": missing_links,
                "missing_photos": missing_photos,
            })
            if missing_photos:
                logger.info("  [%d/%d] ORPHAN '%s' — %d missing photo node(s): %s, %d missing edge(s): %s",
                            i, len(visual_labels), label, len(missing_photos), missing_photos,
                            len(missing_links), missing_links)
            else:
                logger.info("  [%d/%d] ORPHAN '%s' — missing %d edge(s): %s",
                            i, len(visual_labels), label, len(missing_links), missing_links)

    return orphans


def repair_orphans(base_url: str, orphans: list[dict[str, Any]], dry_run: bool) -> tuple[int, int]:
    created = 0
    failed = 0
    photos_created: set[str] = set()

    for orphan in orphans:
        label = orphan["label"]

        for photo_name in orphan.get("missing_photos", []):
            if photo_name in photos_created:
                continue
            file_source = photo_name[:-len(" (Photo)")]
            if dry_run:
                logger.info("  [DRY RUN] Would create Photo node: '%s'", photo_name)
                photos_created.add(photo_name)
                created += 1
                continue
            try:
                create_photo_entity(base_url, file_source)
                photos_created.add(photo_name)
                logger.info("  Created Photo node '%s'", photo_name)
                created += 1
            except URLError as exc:
                if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    photos_created.add(photo_name)
                    created += 1
                else:
                    logger.warning("  Failed to create Photo node '%s': %s", photo_name, exc)
                    failed += 1
            except Exception as exc:
                logger.warning("  Failed to create Photo node '%s': %s", photo_name, exc)
                failed += 1
            time.sleep(0.5)

        for photo_name in orphan["missing_links"]:
            description = f"{label} appears in {photo_name}"
            if dry_run:
                logger.info("  [DRY RUN] Would create edge: '%s' -> '%s'", label, photo_name)
                created += 1
                continue

            try:
                result = create_relation(base_url, label, photo_name, description)
                status = result.get("status", "success")
                logger.info("  Created edge '%s' -> '%s': %s", label, photo_name, status)
                created += 1
            except URLError as exc:
                if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    logger.info("  Edge '%s' -> '%s' already exists (conflict), skipping", label, photo_name)
                    created += 1
                else:
                    logger.warning("  Failed to create edge '%s' -> '%s': %s", label, photo_name, exc)
                    failed += 1
            except Exception as exc:
                logger.warning("  Failed to create edge '%s' -> '%s': %s", label, photo_name, exc)
                failed += 1

            time.sleep(0.3)

    return created, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and repair orphan nodes (entities missing appears_in edges to Photo nodes)."
    )
    parser.add_argument("--lightrag-url", default="http://localhost:9621", help="LightRAG API base URL")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed without making changes")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    base_url = args.lightrag_url.rstrip("/")

    logger.info("Fetching all graph labels from %s ...", base_url)
    try:
        labels = get_all_labels(base_url)
    except Exception as exc:
        logger.error("Failed to fetch labels: %s", exc)
        return 1

    logger.info("Found %d labels total", len(labels))

    orphans = find_orphans(base_url, labels)

    if not orphans:
        logger.info("No orphan nodes found — graph is healthy!")
        return 0

    logger.info("")
    logger.info("Found %d orphan node(s):", len(orphans))
    for orphan in orphans:
        logger.info("  '%s' (file_path=%s)", orphan["label"], orphan["file_path"])
        for link in orphan["missing_links"]:
            logger.info("    -> missing edge to '%s'", link)

    logger.info("")
    if args.dry_run:
        logger.info("DRY RUN — no changes will be made.")

    created, failed = repair_orphans(base_url, orphans, dry_run=args.dry_run)

    logger.info("")
    logger.info("Summary: %d edge(s) %s, %d failed", created, "would be created" if args.dry_run else "created", failed)
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())