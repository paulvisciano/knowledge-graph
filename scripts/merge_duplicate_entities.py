#!/usr/bin/env python3
"""Find and merge duplicate entity nodes in the LightRAG knowledge graph.

Groups entity labels by similarity (ignoring type suffixes like " (Location)"),
then calls LightRAG's /graph/entities/merge endpoint to consolidate each group
into one canonical entity.

Usage:
  # Dry run — show what would be merged
  python scripts/merge_duplicate_entities.py --dry-run

  # List duplicate groups only
  python scripts/merge_duplicate_entities.py --list-only

  # Merge all Location duplicates with default threshold
  python scripts/merge_duplicate_entities.py --entity-type Location

  # Merge all types at a stricter threshold
  python scripts/merge_duplicate_entities.py --threshold 0.9
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_ENTITY_SUFFIXES = (" (Location)", " (Date)", " (Camera)", " (Photo)")
_ABBREVS = {
    "st": "saint",
    "mt": "mount",
    "ft": "fort",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
}


def _strip_suffix(name: str) -> str:
    for suffix in _ENTITY_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _normalize(name: str) -> str:
    bare = _strip_suffix(name)
    normalized = re.sub(r"[.\-,]", " ", bare.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    words = normalized.split()
    expanded = [_ABBREVS.get(w, w) for w in words]
    return " ".join(expanded)


def _entity_type(name: str) -> str:
    for suffix in _ENTITY_SUFFIXES:
        if name.endswith(suffix):
            return suffix.strip(" ()")
    return ""


def get_labels(base_url: str) -> list[str]:
    req = Request(f"{base_url}/graph/label/list", headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def merge_entities(base_url: str, sources: list[str], target: str) -> dict[str, Any]:
    payload = json.dumps({
        "entities_to_change": sources,
        "entity_to_change_into": target,
    }).encode("utf-8")
    req = Request(
        f"{base_url}/graph/entities/merge",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_obvious_false_positive(group: list[str]) -> bool:
    etypes = {_entity_type(n) for n in group}

    if etypes == {"Date"}:
        return True

    if etypes == {"Photo"}:
        return True

    bare_names = [_strip_suffix(n) for n in group]
    tokens = [n.lower().split() for n in bare_names]

    if any(tok[0] == "person" for tok in tokens if tok):
        return True

    number_bodies = set()
    for toks in tokens:
        body = tuple(w for w in toks if not w.isdigit())
        number_bodies.add(body)
    if len(number_bodies) == 1 and len(tokens) > 1:
        differing = sum(1 for toks in tokens if any(w.isdigit() for w in toks))
        if differing >= len(tokens) - 1 and len(tokens) > 2:
            return True

    return False


def find_duplicate_groups(labels: list[str], threshold: float, filter_type: str | None) -> list[list[str]]:
    candidates = []
    for label in labels:
        etype = _entity_type(label)
        if filter_type and etype.lower() != filter_type.lower():
            if etype:
                continue
        candidates.append(label)

    label_index = {i: i for i in range(len(candidates))}
    normalized = [_normalize(l) for l in candidates]

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            score = SequenceMatcher(None, normalized[i], normalized[j]).ratio()

            same_type = _entity_type(candidates[i]) == _entity_type(candidates[j])
            bare_matches = (
                normalized[i] == normalized[j]
                or normalized[i].startswith(normalized[j] + " ")
                or normalized[j].startswith(normalized[i] + " ")
            )

            if same_type and score >= threshold:
                pass
            elif bare_matches and score >= 0.75:
                pass
            else:
                continue

            i_group = label_index[i]
            j_group = label_index[j]
            if i_group != j_group:
                target = min(i_group, j_group)
                source = max(i_group, j_group)
                for k, v in list(label_index.items()):
                    if v == source:
                        label_index[k] = target

    cluster_map: dict[int, list[int]] = {}
    for k, v in label_index.items():
        cluster_map.setdefault(v, []).append(k)

    groups: list[list[str]] = []
    for indices in cluster_map.values():
        if len(indices) > 1:
            group = [candidates[i] for i in indices]
            if not _is_obvious_false_positive(group):
                groups.append(group)

    return groups


def pick_canonical(group: list[str]) -> str:
    """Pick the canonical entity from a group.

    Prefer the longest suffixed name (most specific/complete EXIF entity),
    breaking ties alphabetically for determinism.
    """
    suffixed = [n for n in group if any(n.endswith(s) for s in _ENTITY_SUFFIXES)]
    if suffixed:
        return max(suffixed, key=lambda n: (len(n), n))
    return max(group, key=lambda n: (len(n), n))


def main() -> None:
    parser = argparse.ArgumentParser(description="Find and merge duplicate entity nodes in the knowledge graph")
    parser.add_argument("--url", default="http://localhost:9621", help="LightRAG API URL")
    parser.add_argument("--threshold", type=float, default=0.9, help="Similarity threshold 0-1 (default 0.9)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be merged without making changes")
    parser.add_argument("--entity-type", default=None, help="Only merge entities of this type (e.g. Location)")
    parser.add_argument("--list-only", action="store_true", help="Just list duplicate groups without merging")
    args = parser.parse_args()

    logger.info("Fetching entity labels from %s ...", args.url)
    labels = get_labels(args.url)
    logger.info("Found %d entity labels", len(labels))

    groups = find_duplicate_groups(labels, args.threshold, args.entity_type)

    if not groups:
        logger.info("No duplicate groups found.")
        return

    logger.info("Found %d duplicate group(s):", len(groups))
    for group in groups:
        canonical = pick_canonical(group)
        duplicates = [e for e in group if e != canonical]
        logger.info("  canonical='%s'  duplicates=%s", canonical, duplicates)

    if args.list_only:
        return

    for group in groups:
        canonical = pick_canonical(group)
        duplicates = [e for e in group if e != canonical]

        if args.dry_run:
            logger.info("[DRY-RUN] Would merge %s → '%s'", duplicates, canonical)
            continue

        logger.info("Merging %s → '%s' ...", duplicates, canonical)
        try:
            result = merge_entities(args.url, duplicates, canonical)
            logger.info("Merged: %s", result.get("message", result))
        except URLError as exc:
            logger.error("Merge failed for group %s: %s", group, exc)
            continue

        time.sleep(1.0)

    if not args.dry_run:
        logger.info("All merges complete.")


if __name__ == "__main__":
    main()