#!/usr/bin/env python3.11
"""One-time cleanup: convert spurious ``{file_source} (Photo)`` hub nodes
that are actually notes into ``{file_source} (Note)`` hubs.

Background
----------
``scripts/fix_orphan_nodes.py`` historically decided hub type with
``file_source.startswith("note_")``.  Notes saved via the MCP
``save_to_knowledge_graph`` tool use labels like ``diary-entry-...``,
``chat-note-...``, ``plan_entry``, ``daily_update`` — none of which start
with ``note_`` — so the orphan-repair script created ``{file_source} (Photo)``
hubs with ``entity_type: "Photo"`` and ``source_id = file_source`` for them.
The UI then treats these as photos and requests
``/api/kg/images/photo/{file_source}``, which 404s (no such image on disk).

This script finds every spurious ``(Photo)`` hub whose stripped source passes
``note_sources.is_note_file_source`` and fixes the graph:

* If a matching ``{file_source} (Note)`` hub already exists, every
  ``appears_in`` edge from the ``(Photo)`` hub to an extracted entity is
  re-linked to the ``(Note)`` hub (created if missing), then the ``(Photo)``
  hub is deleted.
* If no ``(Note)`` hub exists, a new ``{file_source} (Note)`` hub is created
  (``entity_type: "Note"``, ``source_id: file_source``, with
  ``date_taken_friendly`` parsed from the source), all ``appears_in`` edges
  are re-linked to it, the ``Note -> written_on -> Date`` bridge is added
  when a date can be parsed, and the ``(Photo)`` hub is deleted.

Idempotent: running twice is a no-op (the second run finds no spurious
``(Photo)`` hubs).  Dry-run by default; pass ``--apply`` to commit.

Standard library only — mirrors the urllib/json/logging style of
``scripts/fix_orphan_nodes.py``.

Usage:
  python scripts/fix_note_photo_hubs.py            # dry run
  python scripts/fix_note_photo_hubs.py --apply    # commit changes
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from note_sources import is_note_file_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_GRAPH_FIELD_SEP = "<SEP>"
_APPEARS_IN = "appears_in"
_WRITTEN_ON = "written_on"
_RE_MCP_TIMESTAMP_SUFFIX = re.compile(r"(\d{8})-(\d{6})-(\d{6})$")
_RE_LEGACY_NOTE = re.compile(r"^note_(\d+)$")


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


def get_all_labels(base_url: str) -> list[str]:
    return _api_get(base_url, "/graph/label/list")


def get_node_subgraph(base_url: str, label: str) -> dict[str, Any]:
    encoded = url_quote(label, safe="")
    return _api_get(base_url, f"/graphs?label={encoded}&max_depth=1&max_nodes=500")


def get_node_properties(subgraph: dict[str, Any], label: str) -> dict[str, Any]:
    for node in subgraph.get("nodes", []):
        if node.get("id") == label:
            return node.get("properties", {})
    return {}


def create_relation(base_url: str, source: str, target: str, description: str, keywords: str = _APPEARS_IN) -> dict[str, Any]:
    return _api_post(base_url, "/graph/relation/create", {
        "source_entity": source,
        "target_entity": target,
        "relation_data": {
            "description": description,
            "keywords": keywords,
            "weight": 1.0,
        },
    })


def delete_entity(base_url: str, name: str) -> dict[str, Any]:
    return _api_delete(base_url, "/graph/entity/delete", {"entity_name": name})


def _edge_partners(subgraph: dict[str, Any], label: str, keyword: str | None = None) -> list[str]:
    """Return the labels on the other end of every edge touching ``label``."""
    partners: list[str] = []
    for edge in subgraph.get("edges", []):
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src == label:
            other = tgt
        elif tgt == label:
            other = src
        else:
            continue
        if keyword is not None:
            props = edge.get("properties", {}) or {}
            if keyword not in (props.get("keywords", "") or ""):
                continue
        partners.append(other)
    return partners


def _note_date_strings(file_source: str) -> tuple[str, str] | None:
    """Parse a note's date — mirrors processor.py:_parse_file_source_date."""
    import os
    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo(os.environ.get("TZ", "America/New_York"))
    except Exception:
        tz = zoneinfo.ZoneInfo("America/New_York")

    dt: datetime | None = None
    m = _RE_LEGACY_NOTE.match(file_source)
    if m:
        try:
            dt = datetime.fromtimestamp(int(m.group(1)), tz=tz)
        except (ValueError, OSError, OverflowError):
            dt = None
    else:
        m = _RE_MCP_TIMESTAMP_SUFFIX.search(file_source)
        if m:
            ymd, hms, _ = m.groups()
            try:
                dt = datetime.strptime(f"{ymd}{hms}", "%Y%m%d%H%M%S").replace(tzinfo=tz)
            except ValueError:
                dt = None
    if dt is None:
        return None
    return (dt.strftime("%Y-%m-%d") + " (Date)", dt.strftime("%Y-%m-%d at %H:%M"))


def _shift_date_label(label: str, days: int) -> str | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2}) \(Date\)$", label)
    if not m:
        return None
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))) + timedelta(days=days)
    except ValueError:
        return None
    return d.strftime("%Y-%m-%d") + " (Date)"


def _ensure_note_hub(
    base_url: str,
    note_hub: str,
    file_source: str,
    dry_run: bool,
    existing_labels: set[str],
) -> bool:
    """Create the ``(Note)`` hub if missing.  Returns True if it exists after."""
    if note_hub in existing_labels:
        return True
    ds = _note_date_strings(file_source)
    entity_data: dict[str, Any] = {
        "description": f"Note: {file_source}",
        "entity_type": "Note",
        "source_id": file_source,
    }
    if ds:
        entity_data["date_taken_friendly"] = ds[1]
    if dry_run:
        logger.info("    [DRY RUN] Would create note hub '%s'", note_hub)
        existing_labels.add(note_hub)
        return True
    try:
        _api_post(base_url, "/graph/entity/create", {
            "entity_name": note_hub,
            "entity_data": entity_data,
        })
        existing_labels.add(note_hub)
        logger.info("    Created note hub '%s'", note_hub)
        return True
    except URLError as exc:
        if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
            existing_labels.add(note_hub)
            return True
        logger.warning("    Failed to create note hub '%s': %s", note_hub, exc)
        return False


def _ensure_appears_in_edge(base_url: str, note_hub: str, target: str, dry_run: bool, existing_targets: set[str]) -> bool:
    if target in existing_targets:
        return True
    description = f"{target} appears in {note_hub}"
    if dry_run:
        logger.info("    [DRY RUN] Would re-link edge '%s' -> '%s'", note_hub, target)
        existing_targets.add(target)
        return True
    try:
        create_relation(base_url, note_hub, target, description)
        existing_targets.add(target)
        logger.info("    Re-linked edge '%s' -> '%s'", note_hub, target)
        return True
    except URLError as exc:
        if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
            existing_targets.add(target)
            return True
        logger.warning("    Failed to re-link '%s' -> '%s': %s", note_hub, target, exc)
        return False
    except Exception as exc:
        logger.warning("    Failed to re-link '%s' -> '%s': %s", note_hub, target, exc)
        return False


def _ensure_written_on(
    base_url: str,
    note_hub: str,
    file_source: str,
    dry_run: bool,
    existing_labels: set[str],
) -> bool:
    ds = _note_date_strings(file_source)
    if not ds:
        return False
    date_label, _ = ds
    subgraph = get_node_subgraph(base_url, note_hub)
    if date_label in _edge_partners(subgraph, note_hub, keyword=_WRITTEN_ON):
        return True
    if date_label not in existing_labels:
        if dry_run:
            logger.info("    [DRY RUN] Would create Date node '%s'", date_label)
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
                existing_labels.add(date_label)
                logger.info("    Created Date node '%s'", date_label)
            except URLError as exc:
                if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                    existing_labels.add(date_label)
                else:
                    logger.warning("    Failed to create Date node '%s': %s", date_label, exc)
                    return False
            except Exception as exc:
                logger.warning("    Failed to create Date node '%s': %s", date_label, exc)
                return False
    if dry_run:
        logger.info("    [DRY RUN] Would create edge '%s' -> '%s' (written_on)", note_hub, date_label)
        return True
    try:
        create_relation(
            base_url, note_hub, date_label,
            f"Note {file_source} written on {date_label}",
            keywords=_WRITTEN_ON,
        )
        logger.info("    Created edge '%s' -> '%s' (written_on)", note_hub, date_label)
        return True
    except URLError as exc:
        if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
            return True
        logger.warning("    Failed to create written_on edge: %s", exc)
        return False
    except Exception as exc:
        logger.warning("    Failed to create written_on edge: %s", exc)
        return False


def _chain_date_to_neighbours(
    base_url: str,
    date_label: str,
    dry_run: bool,
    existing_labels: set[str],
) -> int:
    created = 0
    for delta in (-1, +1):
        nb = _shift_date_label(date_label, delta)
        if not nb or nb not in existing_labels:
            continue
        a, b = (date_label, nb) if date_label < nb else (nb, date_label)
        sub = get_node_subgraph(base_url, a)
        if b in _edge_partners(sub, a, keyword="adjacent_day"):
            continue
        desc = f"{a[:-len(' (Date)')]} is adjacent to {b[:-len(' (Date)')]}"
        if dry_run:
            logger.info("    [DRY RUN] Would create edge '%s' -> '%s' (adjacent_day)", a, b)
            created += 1
            continue
        try:
            create_relation(base_url, a, b, desc, keywords="adjacent_day")
            logger.info("    Created edge '%s' -> '%s' (adjacent_day)", a, b)
            created += 1
        except URLError as exc:
            if "409" in str(exc) or "400" in str(exc) or "already exists" in str(exc):
                created += 1
            else:
                logger.warning("    Failed to chain '%s' -> '%s': %s", a, b, exc)
        except Exception as exc:
            logger.warning("    Failed to chain '%s' -> '%s': %s", a, b, exc)
        time.sleep(0.3)
    return created


def fix_spurious_photo_hubs(
    base_url: str,
    labels: list[str],
    dry_run: bool,
    limit: int | None,
) -> dict[str, int]:
    label_set = set(labels)
    photo_hubs = [l for l in labels if l.endswith(" (Photo)")]
    spurious: list[tuple[str, str]] = []
    for hub in photo_hubs:
        source = hub[: -len(" (Photo)")]
        if is_note_file_source(source):
            spurious.append((hub, source))

    stats = {
        "spurious_found": len(spurious),
        "hubs_converted": 0,
        "hubs_deleted": 0,
        "edges_relinked": 0,
        "date_links_created": 0,
        "failed": 0,
    }

    logger.info("")
    logger.info("Found %d spurious (Photo) hub(s) that are actually notes", len(spurious))
    if limit is not None:
        spurious = spurious[:limit]
        logger.info("  (limited to %d)", len(spurious))

    for i, (photo_hub, source) in enumerate(spurious, 1):
        note_hub = f"{source} (Note)"
        logger.info("[%d/%d] '%s' -> should be '%s'", i, len(spurious), photo_hub, note_hub)
        try:
            photo_sub = get_node_subgraph(base_url, photo_hub)
        except Exception as exc:
            logger.warning("  Failed to fetch subgraph for '%s': %s", photo_hub, exc)
            stats["failed"] += 1
            continue

        targets = _edge_partners(photo_sub, photo_hub)
        relink_targets = [t for t in targets if not t.endswith((" (Photo)", " (Note)", " (Date)", " (Camera)", " (Location)"))]

        if not _ensure_note_hub(base_url, note_hub, source, dry_run, label_set):
            stats["failed"] += 1
            continue

        if note_hub != photo_hub:
            try:
                note_sub = get_node_subgraph(base_url, note_hub)
                existing_note_targets = set(_edge_partners(note_sub, note_hub))
            except Exception:
                existing_note_targets = set()
        else:
            existing_note_targets = set()

        relinked = 0
        for target in relink_targets:
            if _ensure_appears_in_edge(base_url, note_hub, target, dry_run, existing_note_targets):
                relinked += 1
        stats["edges_relinked"] += relinked

        date_ok = _ensure_written_on(base_url, note_hub, source, dry_run, label_set)
        if date_ok:
            stats["date_links_created"] += 1
            ds = _note_date_strings(source)
            if ds:
                _chain_date_to_neighbours(base_url, ds[0], dry_run, label_set)

        if dry_run:
            logger.info("    [DRY RUN] Would delete spurious hub '%s'", photo_hub)
            stats["hubs_deleted"] += 1
            if note_hub not in [p for p, _ in spurious]:
                stats["hubs_converted"] += 1
            continue

        try:
            delete_entity(base_url, photo_hub)
            logger.info("    Deleted spurious hub '%s'", photo_hub)
            stats["hubs_deleted"] += 1
            label_set.discard(photo_hub)
        except URLError as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                stats["hubs_deleted"] += 1
                label_set.discard(photo_hub)
            else:
                logger.warning("    Failed to delete '%s': %s", photo_hub, exc)
                stats["failed"] += 1
        except Exception as exc:
            logger.warning("    Failed to delete '%s': %s", photo_hub, exc)
            stats["failed"] += 1

        was_converted = note_hub not in labels
        if was_converted:
            stats["hubs_converted"] += 1

        time.sleep(0.4)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert spurious (Photo) hubs that are actually notes into (Note) hubs."
    )
    parser.add_argument("--base-url", default="http://localhost:9621", help="LightRAG API base URL")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed without making changes (default)")
    parser.add_argument("--apply", action="store_true", help="Commit the changes (default is dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N spurious hubs (optional)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    dry_run = not args.apply
    if args.apply and args.dry_run:
        logger.warning("--apply and --dry-run both set; using --apply (committing changes)")
        dry_run = False

    base_url = args.base_url.rstrip("/")

    logger.info("Fetching all graph labels from %s ...", base_url)
    try:
        labels = get_all_labels(base_url)
    except Exception as exc:
        logger.error("Failed to fetch labels: %s", exc)
        return 1

    logger.info("Found %d labels total", len(labels))

    if dry_run:
        logger.info("DRY RUN — no changes will be made.  Pass --apply to commit.")

    stats = fix_spurious_photo_hubs(base_url, labels, dry_run, args.limit)

    logger.info("")
    logger.info("Summary:")
    logger.info("  spurious (Photo) hubs found: %d", stats["spurious_found"])
    logger.info("  hubs converted (new Note created): %d", stats["hubs_converted"])
    logger.info("  hubs deleted: %d", stats["hubs_deleted"])
    logger.info("  edges re-linked: %d", stats["edges_relinked"])
    logger.info("  Note->Date links created: %d", stats["date_links_created"])
    logger.info("  failed: %d", stats["failed"])

    return 1 if stats["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())