#!/usr/bin/env python3
"""One-time graph repair: re-link photos to their correct Date entities.

Two repair passes:

1. **Mis-linked photos** — When ``_find_matching_entity`` fuzzy-matched Date
   entity names with a 0.85 SequenceMatcher threshold, adjacent calendar dates
   (e.g. ``2026-06-27`` vs ``2026-06-28``) scored 0.90 and were wrongly treated
   as the same entity.  Photos taken on June 28 were silently linked to the
   ``2026-06-27 (Date)`` node instead of creating a ``2026-06-28 (Date)`` node.
   This pass scans every Date node, parses the capture date from the edge
   description, and re-links photos whose capture date doesn't match the Date
   node they're attached to.

2. **Unlinked photos** — Many Photo entities exist in the graph but were never
   linked to a Date entity (the ``create_exif_relations`` step failed or was
   skipped).  This pass reads ``date_taken_friendly`` from the ``photo_metadata``
   table via the Postgres container and creates the missing ``taken_on`` edge
   from each Photo to its correct ``YYYY-MM-DD (Date)`` node.

Actions per mis-linked photo:
  1. Delete the wrong ``Date -> Photo`` edge.
  2. Create the correct ``YYYY-MM-DD (Date)`` entity if it does not exist.
  3. Create a new ``Date -> Photo`` edge with the correct description.
  4. Re-create the cross edges (Date↔Location, Date↔Camera) that the original
     ``inject_exif_relations`` / ``create_exif_relations`` pipeline would have
     created, so the new Date node is not orphaned.

Actions per unlinked photo:
  1. Create the ``YYYY-MM-DD (Date)`` entity if it does not exist.
  2. Create a ``Date -> Photo`` edge with the correct description.
  3. Chain the new Date node to adjacent dates (±1 day) if they exist.

Usage:
    python scripts/fix_mislinked_photo_dates.py            # apply both passes
    python scripts/fix_mislinked_photo_dates.py --dry-run  # report only
    python scripts/fix_mislinked_photo_dates.py --backfill-only
    python scripts/fix_mislinked_photo_dates.py --relink-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import urllib.parse
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("fix_mislinked_photo_dates")

_LIGHTRAG_URL = os.getenv("LIGHTRAG_API_URL", "http://localhost:9621").rstrip("/")

_RE_DATE_ENTITY = re.compile(r"^(\d{4}-\d{2}-\d{2}) \(Date\)$")
_RE_TAKEN_ON = re.compile(r"Photo taken on (\d{4}-\d{2}-\d{2})(?: at (\d{2}:\d{2}))?")
_RE_TAKEN_AT = re.compile(r"also taken_at (.+?)(?:$|, also)")
_RE_TAKEN_WITH = re.compile(r"also taken_with (.+?)(?:$|, also)")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    key = os.getenv("LIGHTRAG_API_KEY", "")
    if key:
        h["X-API-Key"] = key
    return h


async def _entity_exists(client: httpx.AsyncClient, name: str) -> bool:
    quoted = urllib.parse.quote(name, safe="")
    r = await client.get(f"{_LIGHTRAG_URL}/graph/entity/exists?name={quoted}")
    r.raise_for_status()
    return bool(r.json().get("exists"))


async def _create_entity(
    client: httpx.AsyncClient, name: str, entity_type: str, description: str, source_id: str
) -> dict:
    r = await client.post(
        f"{_LIGHTRAG_URL}/graph/entity/create",
        headers=_headers(),
        json={
            "entity_name": name,
            "entity_data": {
                "description": description,
                "entity_type": entity_type,
                "source_id": source_id,
            },
        },
    )
    if r.status_code == 409:
        return {"status": "exists"}
    r.raise_for_status()
    return r.json()


async def _create_relation(
    client: httpx.AsyncClient, source: str, target: str, data: dict
) -> dict:
    r = await client.post(
        f"{_LIGHTRAG_URL}/graph/relation/create",
        headers=_headers(),
        json={"source_entity": source, "target_entity": target, "relation_data": data},
    )
    if r.status_code == 409:
        return {"status": "exists"}
    r.raise_for_status()
    return r.json()


async def _delete_relation(client: httpx.AsyncClient, source: str, target: str) -> dict:
    r = await client.request(
        "DELETE",
        f"{_LIGHTRAG_URL}/graph/relation/delete",
        headers=_headers(),
        json={"source_entity": source, "target_entity": target},
    )
    if r.status_code == 404:
        return {"status": "not_found"}
    r.raise_for_status()
    return r.json()


async def _get_subgraph(client: httpx.AsyncClient, label: str, max_nodes: int = 500) -> dict:
    quoted = urllib.parse.quote(label, safe="")
    r = await client.get(
        f"{_LIGHTRAG_URL}/graphs",
        params={"label": label, "max_depth": 1, "max_nodes": max_nodes},
    )
    r.raise_for_status()
    return r.json()


async def _get_label_list(client: httpx.AsyncClient) -> list[str]:
    r = await client.get(f"{_LIGHTRAG_URL}/graph/label/list")
    r.raise_for_status()
    return r.json()


def _parse_taken_on(description: str) -> str | None:
    m = _RE_TAKEN_ON.search(description)
    if m:
        return m.group(1)
    return None


def _extract_location_camera(description: str) -> tuple[str | None, str | None]:
    loc = None
    cam = None
    m = _RE_TAKEN_AT.search(description)
    if m:
        loc = m.group(1).strip()
    m = _RE_TAKEN_WITH.search(description)
    if m:
        cam = m.group(1).strip()
    return loc, cam


async def fix_date_node(
    client: httpx.AsyncClient, date_label: str, dry_run: bool
) -> list[dict]:
    m = _RE_DATE_ENTITY.match(date_label)
    if not m:
        return []
    date_str = m.group(1)

    subgraph = await _get_subgraph(client, date_label, max_nodes=500)
    edges = subgraph.get("edges", [])

    fixes = []
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        props = edge.get("properties", {})
        desc = props.get("description", "") or ""
        keywords = props.get("keywords", "") or ""

        if "taken_on" not in keywords:
            continue

        other = tgt if src == date_label else src
        if "(Photo)" not in other:
            continue

        actual_date = _parse_taken_on(desc)
        if actual_date is None or actual_date == date_str:
            continue

        fix = {
            "photo": other,
            "wrong_date": date_str,
            "correct_date": actual_date,
            "edge_desc": desc,
        }
        fixes.append(fix)

    if not fixes:
        return []

    correct_date_label = f"{fixes[0]['correct_date']} (Date)"

    logger.info(
        "[ %s ] found %d mis-linked photos (should be %s)",
        date_label,
        len(fixes),
        fixes[0]["correct_date"],
    )
    for f in fixes:
        logger.info("  %s  (desc: %s)", f["photo"], f["edge_desc"])

    if dry_run:
        return fixes

    correct_date_exists = await _entity_exists(client, correct_date_label)

    if not correct_date_exists:
        logger.info("Creating missing Date entity: %s", correct_date_label)
        await _create_entity(
            client,
            correct_date_label,
            "Date",
            f"Calendar date {fixes[0]['correct_date']}",
            correct_date_label,
        )
        await asyncio.sleep(0.5)
    else:
        logger.info("Date entity %s already exists", correct_date_label)

    for f in fixes:
        photo = f["photo"]
        actual_date = f["correct_date"]

        logger.info("  fixing %s", photo)

        await _delete_relation(client, date_label, photo)

        new_desc = f"Photo taken on {actual_date}"
        await _create_relation(
            client,
            photo,
            correct_date_label,
            {
                "description": f"Photo taken on {actual_date}",
                "keywords": "taken_on",
                "weight": 1.0,
            },
        )
        await asyncio.sleep(0.3)

        loc, cam = _extract_location_camera(f["edge_desc"])
        if loc:
            loc_entity = f"{loc} (Location)" if not loc.endswith(" (Location)") else loc
            await _create_relation(
                client,
                correct_date_label,
                loc_entity,
                {
                    "description": f"Photo taken on {actual_date}, also taken_at {loc}",
                    "keywords": "taken_on",
                    "weight": 1.0,
                },
            )
            await asyncio.sleep(0.2)
        if cam:
            cam_entity = f"{cam} (Camera)" if not cam.endswith(" (Camera)") else cam
            await _create_relation(
                client,
                correct_date_label,
                cam_entity,
                {
                    "description": f"Photo taken on {actual_date}, also taken_with {cam}",
                    "keywords": "taken_on",
                    "weight": 1.0,
                },
            )
            await asyncio.sleep(0.2)

    prev_label = f"{_prev_day(date_str)} (Date)"
    next_label = f"{_next_day(date_str)} (Date)"
    for adj in (prev_label, next_label):
        if await _entity_exists(client, adj):
            a, b = (correct_date_label, adj) if correct_date_label < adj else (adj, correct_date_label)
            await _create_relation(
                client,
                a,
                b,
                {
                    "description": f"{a[:-len(' (Date)')]} is adjacent to {b[:-len(' (Date)')]}",
                    "keywords": "adjacent_day",
                    "weight": 1.0,
                },
            )
            logger.info("  chained adjacency %s <-> %s", a, b)

    return fixes


def _prev_day(date_str: str) -> str:
    from datetime import datetime, timedelta

    d = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _next_day(date_str: str) -> str:
    from datetime import datetime, timedelta

    d = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _fetch_db_exif_dates() -> dict[str, str]:
    """Query photo_metadata via the docker Postgres container for date_taken_friendly.

    Returns a mapping of ``file_source`` → ``date_taken_friendly``
    (e.g. ``"PXL_20260627_210508691.RAW-01.jpg"`` → ``"2026-06-27 at 17:05"``).
    """
    import subprocess

    result = subprocess.run(
        [
            "docker", "exec", "knowledge-graph-postgres",
            "psql", "-U", "lightrag", "-d", "lightrag",
            "-t", "-A", "-F\t",
            "-c", "SELECT file_source, date_taken_friendly FROM photo_metadata "
                   "WHERE date_taken_friendly IS NOT NULL ORDER BY file_source",
        ],
        capture_output=True, text=True, check=True,
    )
    db_dates: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        fs, friendly = parts[0], parts[1]
        db_dates[fs] = friendly
    return db_dates


async def _chain_adjacency(
    client: httpx.AsyncClient, date_label: str
) -> None:
    """Link date_label to its ±1-day neighbors if they exist in the graph."""
    m = _RE_DATE_ENTITY.match(date_label)
    if not m:
        return
    date_str = m.group(1)
    for delta_fn in (_prev_day, _next_day):
        adj_label = f"{delta_fn(date_str)} (Date)"
        if not await _entity_exists(client, adj_label):
            continue
        a, b = (date_label, adj_label) if date_label < adj_label else (adj_label, date_label)
        await _create_relation(
            client, a, b,
            {
                "description": f"{a[:-len(' (Date)')]} is adjacent to {b[:-len(' (Date)')]}",
                "keywords": "adjacent_day",
                "weight": 1.0,
            },
        )
        logger.info("  chained adjacency %s <-> %s", a, b)


async def backfill_missing_date_links(
    client: httpx.AsyncClient, dry_run: bool
) -> int:
    """Create ``taken_on`` edges for Photo entities that have no Date link.

    Reads ``date_taken_friendly`` from the ``photo_metadata`` DB table and
    creates the missing Date entity + edge for every Photo entity in the graph
    that has DB EXIF data but no ``taken_on`` edge to any Date node.
    """
    all_labels = await _get_label_list(client)
    label_set = set(all_labels)
    photo_labels = {l for l in all_labels if l.endswith(" (Photo)")}

    # Build a set of photos that already have a taken_on edge
    date_labels = [l for l in all_labels if _RE_DATE_ENTITY.match(l)]
    linked_photos: set[str] = set()
    for dl in date_labels:
        subgraph = await _get_subgraph(client, dl, max_nodes=500)
        for e in subgraph.get("edges", []):
            src, tgt = e.get("source", ""), e.get("target", "")
            props = e.get("properties", {})
            if "taken_on" not in (props.get("keywords") or ""):
                continue
            other = tgt if src == dl else src
            if other in photo_labels:
                linked_photos.add(other)

    unlinked_photos = sorted(photo_labels - linked_photos)
    logger.info("Backfill: %d Photo entities in graph, %d already linked, %d unlinked",
                len(photo_labels), len(linked_photos), len(unlinked_photos))
    if not unlinked_photos:
        return 0

    db_dates = _fetch_db_exif_dates()
    logger.info("Backfill: fetched %d EXIF dates from DB", len(db_dates))

    # Group unlinked photos by their target Date entity
    by_date: dict[str, list[tuple[str, str]]] = {}
    no_db_data: list[str] = []
    for photo_label in unlinked_photos:
        file_source = photo_label[: -len(" (Photo)")]
        friendly = db_dates.get(file_source)
        if not friendly:
            no_db_data.append(photo_label)
            continue
        date_only = friendly.split(" at ")[0].split(" ")[0]
        date_label = f"{date_only} (Date)"
        by_date.setdefault(date_label, []).append((photo_label, friendly))

    if no_db_data:
        logger.info("Backfill: %d photos have no DB EXIF data (skipping):", len(no_db_data))
        for p in no_db_data:
            logger.info("  %s", p)

    total = 0
    for date_label in sorted(by_date):
        photos = by_date[date_label]
        logger.info("Backfill: %s needs %d photos linked", date_label, len(photos))
        for p, friendly in sorted(photos):
            logger.info("  %s  (friendly=%s)", p, friendly)
        total += len(photos)

    if dry_run:
        return total

    for date_label in sorted(by_date):
        photos = by_date[date_label]
        m = _RE_DATE_ENTITY.match(date_label)
        date_str = m.group(1) if m else date_label

        if not await _entity_exists(client, date_label):
            logger.info("Backfill: creating Date entity %s", date_label)
            await _create_entity(
                client, date_label, "Date",
                f"Calendar date {date_str}", date_label,
            )
            await asyncio.sleep(0.5)

        for photo_label, friendly in sorted(photos):
            await _create_relation(
                client, photo_label, date_label,
                {
                    "description": f"Photo taken on {friendly}",
                    "keywords": "taken_on",
                    "weight": 1.0,
                },
            )
            logger.info("Backfill: linked %s -> %s", photo_label, date_label)
            await asyncio.sleep(0.3)

        await _chain_adjacency(client, date_label)

    return total


async def main(dry_run: bool, backfill_only: bool, relink_only: bool) -> None:
    async with httpx.AsyncClient(timeout=120.0) as client:
        do_relink = not backfill_only
        do_backfill = not relink_only
        total_fixes = 0
        total_backfilled = 0

        if do_relink:
            labels = await _get_label_list(client)
            date_labels = [l for l in labels if _RE_DATE_ENTITY.match(l)]
            logger.info("Pass 1: found %d Date entities, checking for mis-linked photos", len(date_labels))
            for label in sorted(date_labels):
                try:
                    fixes = await fix_date_node(client, label, dry_run)
                    total_fixes += len(fixes)
                except Exception as e:
                    logger.error("Error processing %s: %s", label, e)
            action = "DRY RUN" if dry_run else "APPLIED"
            logger.info("Pass 1 %s: %d mis-linked photo edges found%s", action, total_fixes,
                         "" if dry_run else " and fixed")

        if do_backfill:
            logger.info("Pass 2: backfilling missing taken_on edges from DB EXIF")
            total_backfilled = await backfill_missing_date_links(client, dry_run)
            action = "DRY RUN" if dry_run else "APPLIED"
            logger.info("Pass 2 %s: %d missing Date links found%s", action, total_backfilled,
                         "" if dry_run else " and created")

        logger.info("Total: %d relink + %d backfill = %d edges", total_fixes, total_backfilled, total_fixes + total_backfilled)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report only, do not apply fixes")
    parser.add_argument("--backfill-only", action="store_true", help="Only backfill missing Date links, skip relink pass")
    parser.add_argument("--relink-only", action="store_true", help="Only relink mis-linked photos, skip backfill pass")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, args.backfill_only, args.relink_only))