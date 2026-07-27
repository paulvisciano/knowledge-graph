#!/usr/bin/env python3.11
"""Find and repair orphan nodes in the LightRAG knowledge graph.

An orphan is a visual entity (extracted by the LLM from image or note
descriptions) that has a ``file_path`` property pointing to a source document,
but no ``appears_in`` edge connecting it to the corresponding hub node —
``{file_source} (Photo)`` for images, ``{file_source} (Note)`` for notes.

This script:
  1. Fetches all graph labels.
  2. Skips hub/EXIF-managed entities (Date/Camera/Location/Photo/Note suffixes).
  3. For each visual entity, reads its ``file_path`` property.
  4. Splits on ``<SEP>`` to get the list of source documents.
  5. For each source, checks if the matching hub node ({source} (Photo) or
     {source} (Note)) exists.
  6. Checks if an ``appears_in`` edge already connects them.
  7. If not, creates the missing hub node and/or edge via the graph API.

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

_EXIF_SUFFIXES = (" (Date)", " (Camera)", " (Location)", " (Photo)", " (Note)")
_GRAPH_FIELD_SEP = "<SEP>"
_RELATION_KEYWORDS = "appears_in"


def _is_exif_entity(label: str) -> bool:
    return label.endswith(_EXIF_SUFFIXES)


def _hub_name_for(file_source: str) -> str:
    # Notes use a (Note) hub; images use a (Photo) hub.  An entity's file_path
    # lists its source documents, so the hub type is determined per source.
    if file_source.startswith("note_"):
        return f"{file_source} (Note)"
    return f"{file_source} (Photo)"


def _api_delete(base_url: str, path: str, payload: dict[str, Any]) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
    # max_nodes=500 matches link_exif_to_visual_entities and the verify step
    # in _create_relation_verified (api/services/processor.py:351).  The old
    # value of 50 truncated hub nodes (Beach, Sky, "Google Pixel 10 Pro XL"
    # with 17+ photo edges) so their existing appears_in edges were invisible
    # to the scanner → endless false-positive "orphans" that LightRAG reports
    # as "already exists" on every repair pass.
    return _api_get(base_url, f"/graphs?label={encoded}&max_depth=1&max_nodes=500")


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


def create_relation(base_url: str, source: str, target: str, description: str, keywords: str = _RELATION_KEYWORDS) -> dict[str, Any]:
    return _api_post(base_url, "/graph/relation/create", {
        "source_entity": source,
        "target_entity": target,
        "relation_data": {
            "description": description,
            "keywords": keywords,
            "weight": 1.0,
        },
    })


def create_hub_entity(base_url: str, file_source: str) -> dict[str, Any]:
    hub_name = _hub_name_for(file_source)
    is_note = file_source.startswith("note_")
    entity_data: dict[str, Any] = {
        "description": f"Note: {file_source}" if is_note else f"Photo: {file_source}",
        "entity_type": "Note" if is_note else "Photo",
        "source_id": file_source,
    }
    if is_note:
        ds = _note_date_strings(file_source)
        if ds:
            entity_data["date_taken_friendly"] = ds[1]
    return _api_post(base_url, "/graph/entity/create", {
        "entity_name": hub_name,
        "entity_data": entity_data,
    })


def find_orphans(base_url: str, labels: list[str]) -> list[dict[str, Any]]:
    orphans: list[dict[str, Any]] = []
    hub_labels = {l for l in labels if l.endswith(" (Photo)") or l.endswith(" (Note)")}

    visual_labels = [l for l in labels if not _is_exif_entity(l)]

    logger.info("Scanning %d labels (%d hubs, %d visual entities)",
                len(labels), len(hub_labels), len(visual_labels))

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
        missing_hubs: list[str] = []
        for src in sources:
            normalized_src = src.replace("\u202f", " ").replace("\u00a0", " ")
            hub_name = _hub_name_for(normalized_src)
            if hub_name not in hub_labels:
                missing_hubs.append(hub_name)
                continue
            if hub_name in existing_targets:
                continue
            missing_links.append(hub_name)

        if missing_links or missing_hubs:
            orphans.append({
                "label": label,
                "file_path": file_path,
                "sources": sources,
                "missing_links": missing_links,
                "missing_hubs": missing_hubs,
            })
            if missing_hubs:
                logger.info("  [%d/%d] ORPHAN '%s' — %d missing hub node(s): %s, %d missing edge(s): %s",
                            i, len(visual_labels), label, len(missing_hubs), missing_hubs,
                            len(missing_links), missing_links)
            else:
                logger.info("  [%d/%d] ORPHAN '%s' — missing %d edge(s): %s",
                            i, len(visual_labels), label, len(missing_links), missing_links)

    return orphans


def repair_orphans(base_url: str, orphans: list[dict[str, Any]], dry_run: bool) -> tuple[int, int]:
    created = 0
    failed = 0
    hubs_created: set[str] = set()

    for orphan in orphans:
        label = orphan["label"]

        for hub_name in orphan.get("missing_hubs", []):
            if hub_name in hubs_created:
                continue
            suffix = " (Note)" if hub_name.endswith(" (Note)") else " (Photo)"
            file_source = hub_name[:-len(suffix)]
            if dry_run:
                logger.info("  [DRY RUN] Would create hub node: '%s'", hub_name)
                hubs_created.add(hub_name)
                created += 1
                continue
            try:
                create_hub_entity(base_url, file_source)
                hubs_created.add(hub_name)
                logger.info("  Created hub node '%s'", hub_name)
                created += 1
            except URLError as exc:
                if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    hubs_created.add(hub_name)
                    created += 1
                else:
                    logger.warning("  Failed to create hub node '%s': %s", hub_name, exc)
                    failed += 1
            except Exception as exc:
                logger.warning("  Failed to create hub node '%s': %s", hub_name, exc)
                failed += 1
            time.sleep(0.5)

        for hub_name in orphan["missing_links"]:
            description = f"{label} appears in {hub_name}"
            if dry_run:
                logger.info("  [DRY RUN] Would create edge: '%s' -> '%s'", label, hub_name)
                created += 1
                continue

            try:
                result = create_relation(base_url, label, hub_name, description)
                status = result.get("status", "success")
                logger.info("  Created edge '%s' -> '%s': %s", label, hub_name, status)
                created += 1
            except URLError as exc:
                if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    logger.info("  Edge '%s' -> '%s' already exists (conflict), skipping", label, hub_name)
                    created += 1
                else:
                    logger.warning("  Failed to create edge '%s' -> '%s': %s", label, hub_name, exc)
                    failed += 1
            except Exception as exc:
                logger.warning("  Failed to create edge '%s' -> '%s': %s", label, hub_name, exc)
                failed += 1

            time.sleep(0.3)

    return created, failed


def _get_documents(base_url: str) -> list[dict[str, Any]]:
    docs = _api_get(base_url, "/documents")
    if isinstance(docs, dict) and "statuses" in docs:
        return [d for v in docs["statuses"].values() for d in v]
    if isinstance(docs, list):
        return docs
    return docs.get("documents", docs.get("data", []))


def cleanup_isolated_hubs(base_url: str, labels: list[str], dry_run: bool) -> tuple[int, int]:
    # A zero-edge hub with no backing document is a dead node whose source was
    # never ingested (or was deleted) — no entities will ever link to it.
    deleted = 0
    failed = 0
    hub_labels = [l for l in labels if l.endswith(" (Photo)") or l.endswith(" (Note)")]

    try:
        docs = _get_documents(base_url)
    except Exception as exc:
        logger.warning("  Could not fetch document list: %s — skipping hub cleanup", exc)
        return 0, 0
    doc_sources = {d.get("file_path") or d.get("filename") or d.get("name") or "" for d in docs if isinstance(d, dict)}

    logger.info("")
    logger.info("Checking %d hub node(s) for isolation", len(hub_labels))

    for hub in hub_labels:
        file_source = hub[: -len(" (Photo)")] if hub.endswith(" (Photo)") else hub[: -len(" (Note)")]
        try:
            subgraph = get_node_subgraph(base_url, hub)
        except Exception as exc:
            logger.warning("  Failed to fetch subgraph for '%s': %s", hub, exc)
            continue
        if subgraph.get("edges"):
            continue
        if file_source in doc_sources:
            logger.info("  Isolated hub '%s' has a backing document — keeping", hub)
            continue

        if dry_run:
            logger.info("  [DRY RUN] Would delete isolated hub: '%s'", hub)
            deleted += 1
            continue
        try:
            _api_delete(base_url, "/graph/entity/delete", {"entity_name": hub})
            logger.info("  Deleted isolated hub: '%s'", hub)
            deleted += 1
        except URLError as exc:
            logger.warning("  Failed to delete '%s': %s", hub, exc)
            failed += 1
        except Exception as exc:
            logger.warning("  Failed to delete '%s': %s", hub, exc)
            failed += 1
        time.sleep(0.3)

    return deleted, failed


def _note_date_strings(file_source: str) -> tuple[str, str] | None:
    # Returns (date_label, date_taken_friendly) or None.
    # date_label matches "YYYY-MM-DD (Date)" (image Date nodes).
    # date_taken_friendly matches the "YYYY-MM-DD at HH:MM" shape the UI's
    # parseNodeDate reads (Layout.ts:126) so notes land on the timeline.
    import re
    from datetime import datetime
    import zoneinfo
    m = re.match(r"^note_(\d+)$", file_source)
    if not m:
        return None
    try:
        tz = zoneinfo.ZoneInfo(__import__("os").environ.get("TZ", "America/New_York"))
        dt = datetime.fromtimestamp(int(m.group(1)), tz=tz)
        return (dt.strftime("%Y-%m-%d") + " (Date)", dt.strftime("%Y-%m-%d at %H:%M"))
    except Exception as exc:
        logger.warning("  Could not resolve date for %s: %s", file_source, exc)
        return None


def _date_label_for_note(file_source: str) -> str | None:
    r = _note_date_strings(file_source)
    return r[0] if r else None


def _parse_date_label(label: str) -> "datetime | None":
    import re
    from datetime import datetime
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2}) \(Date\)$", label)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _shift_date_label(label: str, days: int) -> str | None:
    from datetime import timedelta
    d = _parse_date_label(label)
    if not d:
        return None
    return (d + timedelta(days=days)).strftime("%Y-%m-%d") + " (Date)"


def chain_date_nodes(base_url: str, labels: list[str], dry_run: bool) -> tuple[int, int]:
    # Date nodes are created per-day by images (taken_on) and notes (written_on),
    # but nothing connects consecutive days.  A note whose day has no photos
    # (e.g. note_1784696596 on 2026-07-22) floats in a 3-node island
    # {Note, Date, entities} because its (Date) node has no photo edges and no
    # link to neighbouring dates that DO have photos.  Link each (Date) node to
    # the previous and next existing (Date) nodes via an "adjacent_day" edge so
    # the Date nodes form a connected timeline spine that any note or photo
    # attaches into.  Idempotent: skips edges that already exist.
    created = 0
    failed = 0
    date_labels = sorted(l for l in labels if l.endswith(" (Date)"))
    if len(date_labels) < 2:
        logger.info("")
        logger.info("Date chain: fewer than 2 Date nodes — nothing to chain")
        return 0, 0

    logger.info("")
    logger.info("Chaining %d Date node(s) into a timeline spine", len(date_labels))

    for i, dl in enumerate(date_labels):
        for nb in (_shift_date_label(dl, -1), _shift_date_label(dl, +1)):
            if not nb or nb not in date_labels:
                continue
            # Symmetric edge — only create from the earlier label to avoid
            # double-creating the same adjacency in both directions.
            source, target = (dl, nb) if dl < nb else (nb, dl)
            subgraph = get_node_subgraph(base_url, source)
            targets = get_existing_edge_targets(subgraph, source)
            if target in targets:
                continue
            if dry_run:
                logger.info("  [DRY RUN] Would create edge '%s' -> '%s' (adjacent_day)", source, target)
                created += 1
                continue
            try:
                create_relation(base_url, source, target, f"{source[:-len(' (Date)')]} is adjacent to {target[:-len(' (Date)')]}", keywords="adjacent_day")
                logger.info("  Created edge '%s' -> '%s' (adjacent_day)", source, target)
                created += 1
            except URLError as exc:
                if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    created += 1
                else:
                    logger.warning("  Failed to create edge '%s' -> '%s': %s", source, target, exc)
                    failed += 1
            except Exception as exc:
                logger.warning("  Failed to create edge '%s' -> '%s': %s", source, target, exc)
                failed += 1
            time.sleep(0.3)

    return created, failed


def backfill_note_date_links(base_url: str, labels: list[str], dry_run: bool) -> tuple[int, int]:
    # Existing notes got their (Note) hub + appears_in edges from repair_orphans
    # but the Note -> written_on -> Date bridge is only in link_note_to_date
    # (api/services/processor.py), which runs for new notes only.  Backfill
    # the same bridge for any (Note) hub missing it so old notes join the same
    # Date node images from that day use.
    created = 0
    failed = 0
    note_hubs = [l for l in labels if l.endswith(" (Note)")]
    date_labels = {l for l in labels if l.endswith(" (Date)")}

    logger.info("")
    logger.info("Backfilling Note -> Date bridges for %d note hub(s)", len(note_hubs))

    for note_hub in note_hubs:
        file_source = note_hub[: -len(" (Note)")]
        ds = _note_date_strings(file_source)
        if not ds:
            continue
        date_label, date_taken_friendly = ds

        # Set date_taken_friendly on the Note hub so the UI places it on the
        # timeline.  Existing backfilled hubs lack this (created_at is the
        # backfill time, not the note date).  Idempotent.
        props = get_node_properties(get_node_subgraph(base_url, note_hub), note_hub)
        if not props.get("date_taken_friendly"):
            if dry_run:
                logger.info("  [DRY RUN] Would set date_taken_friendly on '%s' -> '%s'", note_hub, date_taken_friendly)
            else:
                try:
                    _api_post(base_url, "/graph/entity/edit", {
                        "entity_name": note_hub,
                        "updated_data": {"date_taken_friendly": date_taken_friendly},
                    })
                    logger.info("  Set date_taken_friendly on '%s' -> '%s'", note_hub, date_taken_friendly)
                except URLError as exc:
                    logger.warning("  Failed to set date_taken_friendly on '%s': %s", note_hub, exc)
                except Exception as exc:
                    logger.warning("  Failed to set date_taken_friendly on '%s': %s", note_hub, exc)
            created += 1

        subgraph = get_node_subgraph(base_url, note_hub)
        existing_targets = get_existing_edge_targets(subgraph, note_hub)
        if date_label in existing_targets:
            continue

        if date_label not in date_labels:
            if dry_run:
                logger.info("  [DRY RUN] Would create Date node: '%s'", date_label)
            else:
                try:
                    _api_post(base_url, "/graph/entity/create", {
                        "entity_name": date_label,
                        "entity_data": {
                            "description": f"Calendar date {date_label[:-len(' (Date)')]}",
                            "entity_type": "Date",
                            "source_id": date_label,
                        },
                    })
                    date_labels.add(date_label)
                    logger.info("  Created Date node '%s'", date_label)
                except URLError as exc:
                    if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                        date_labels.add(date_label)
                    else:
                        logger.warning("  Failed to create Date node '%s': %s", date_label, exc)
                        failed += 1
                        continue
                except Exception as exc:
                    logger.warning("  Failed to create Date node '%s': %s", date_label, exc)
                    failed += 1
                    continue
            created += 1

        description = f"Note {file_source} written on {date_label}"
        if dry_run:
            logger.info("  [DRY RUN] Would create edge: '%s' -> '%s' (written_on)", note_hub, date_label)
            created += 1
            continue
        try:
            result = create_relation(base_url, note_hub, date_label, description, keywords="written_on")
            logger.info("  Created edge '%s' -> '%s' (written_on): %s", note_hub, date_label, result.get("status", "success"))
            created += 1
        except URLError as exc:
            if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                logger.info("  Edge '%s' -> '%s' already exists, skipping", note_hub, date_label)
                created += 1
            else:
                logger.warning("  Failed to create edge '%s' -> '%s': %s", note_hub, date_label, exc)
                failed += 1
        except Exception as exc:
            logger.warning("  Failed to create edge '%s' -> '%s': %s", note_hub, date_label, exc)
            failed += 1
        time.sleep(0.3)

    return created, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and repair orphan nodes (entities missing appears_in edges to Photo/Note hub nodes)."
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
    else:
        logger.info("")
        logger.info("Found %d orphan node(s):", len(orphans))
        for orphan in orphans:
            logger.info("  '%s' (file_path=%s)", orphan["label"], orphan["file_path"])
            for hub in orphan.get("missing_hubs", []):
                logger.info("    -> missing hub node '%s'", hub)
            for link in orphan["missing_links"]:
                logger.info("    -> missing edge to '%s'", link)

        logger.info("")
        if args.dry_run:
            logger.info("DRY RUN — no changes will be made.")

        created, failed = repair_orphans(base_url, orphans, dry_run=args.dry_run)
        logger.info("")
        logger.info("Orphan repair: %d edge(s) %s, %d failed", created, "would be created" if args.dry_run else "created", failed)

    date_created, date_failed = backfill_note_date_links(base_url, labels, dry_run=args.dry_run)
    logger.info("")
    logger.info("Date bridge backfill: %d %s, %d failed", date_created, "would be created" if args.dry_run else "created", date_failed)

    chain_created, chain_failed = chain_date_nodes(base_url, labels, dry_run=args.dry_run)
    logger.info("")
    logger.info("Date chain: %d %s, %d failed", chain_created, "would be created" if args.dry_run else "created", chain_failed)

    deleted, del_failed = cleanup_isolated_hubs(base_url, labels, dry_run=args.dry_run)
    logger.info("")
    logger.info("Isolated hub cleanup: %d %s, %d failed", deleted, "would be deleted" if args.dry_run else "deleted", del_failed)

    total_failed = (failed if orphans else 0) + date_failed + chain_failed + del_failed
    return 1 if total_failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())