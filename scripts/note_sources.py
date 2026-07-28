#!/usr/bin/env python3.11
"""Shared helpers for recognising note ``file_source`` strings.

A ``file_source`` identifies a source document in the LightRAG knowledge
graph.  Notes — as opposed to photos — are produced by two code paths:

1. The legacy ``POST /notes`` endpoint (``api/routes/images.py``), which
   emits ``note_<epoch>`` (e.g. ``note_1784696596``).
2. The MCP ``save_to_knowledge_graph`` tool
   (``mcp-services/src/knowledge_graph_mcp/server.py``), which takes a
   label such as ``diary-entry`` or ``chat-note`` and appends a timestamp
   suffix ``YYYYMMDD-HHMMSS-microseconds`` (e.g.
   ``diary-entry-2026-07-27-20260727-144715-618412``).  When no label is
   given it falls back to ``mcp-<timestamp>``.  A few older saves in the
   live graph used the bare labels ``plan_entry``, ``daily_update`` and
   ``personal_context_update`` with no suffix.

The hub node for a note is always ``{file_source} (Note)`` with
``entity_type: "Note"`` (created by
``api/services/processor.py:link_note_to_date``).  The orphan-repair
script (``scripts/fix_orphan_nodes.py``) historically only recognised the
``note_`` prefix and treated every other file_source as a photo, creating
spurious ``{file_source} (Photo)`` hubs that the UI then 404s on.  This
module centralises the note/photo predicate so both the repair script and
the one-time cleanup script (``scripts/fix_note_photo_hubs.py``) agree on
what is a note.

Standard library only — no third-party imports.
"""

from __future__ import annotations

import re

# Labels explicitly documented for the MCP ``save_to_knowledge_graph`` tool
# (server.py:50) plus the older bare labels observed in the live graph.
NOTE_LABEL_PREFIXES: tuple[str, ...] = (
    "diary-entry",
    "diary_entry",
    "chat-note",
    "preference-update",
    "correction",
    "plan_entry",
    "daily_update",
    "personal_context_update",
    "mcp-",
)

# Legacy /notes endpoint shape: ``note_<digits>`` (Unix epoch in seconds).
_RE_LEGACY_NOTE = re.compile(r"^note_\d+$")

# MCP timestamp suffix: trailing ``YYYYMMDD-HHMMSS-microseconds`` appended by
# ``save_to_knowledge_graph`` (server.py:1173).  Searched at the end of the
# string; mirrors ``processor.py:_parse_file_source_date``.
_RE_MCP_TIMESTAMP_SUFFIX = re.compile(r"\d{8}-\d{6}-\d{6}$")

# Photo filename heuristics.  Photos live on disk under ``inputs/`` as
# ``PXL_*.jpg`` (and a handful of other extensions).  Even if a photo
# file_source ever happened to end with the MCP timestamp suffix, these
# markers take precedence and force it to be treated as a photo.
_PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".raw", ".gif", ".webp", ".bmp", ".tif", ".tiff")


def _looks_like_photo(file_source: str) -> bool:
    """Return True for filename-shaped photo sources."""
    lowered = file_source.lower()
    if lowered.startswith("pxl_"):
        return True
    return any(lowered.endswith(ext) for ext in _PHOTO_EXTENSIONS)


def is_note_file_source(file_source: str) -> bool:
    """Return True if ``file_source`` identifies a note (not a photo).

    Recognised shapes (any one wins):

    * ``note_<digits>`` — legacy ``/notes`` endpoint.
    * Starts with any label in :data:`NOTE_LABEL_PREFIXES`
      (``diary-entry``, ``diary_entry``, ``chat-note``, ``preference-update``,
      ``correction``, ``plan_entry``, ``daily_update``,
      ``personal_context_update``, ``mcp-``).
    * Ends with the MCP timestamp suffix ``\\d{8}-\\d{6}-\\d{6}$`` *and* is
      not photo-filename-shaped (``PXL_*`` / has a photo extension).

    Photo sources (``PXL_*.jpg`` etc.) always return False, even in the
    pathological case where a photo file_source happened to end with the
    MCP suffix pattern.

    LightRAG concatenates an entity's source documents with ``<SEP>`` (e.g.
    ``personal_context_update<SEP>PXL_20260504....jpg<SEP>daily_update``).
    Those compound strings belong to extracted entities (person/location/),
    NOT to a Note hub, so they return False — otherwise entities get
    misclassified as notes.
    """
    if not file_source:
        return False
    if "<SEP>" in file_source:
        return False
    if _looks_like_photo(file_source):
        return False
    if _RE_LEGACY_NOTE.match(file_source):
        return True
    if file_source.startswith(NOTE_LABEL_PREFIXES):
        return True
    if _RE_MCP_TIMESTAMP_SUFFIX.search(file_source):
        return True
    return False


_RE_DOC_CHUNK = re.compile(r"^doc-[a-f0-9]{32}-chunk-\d{3}$")


def is_doc_chunk_source(file_source: str) -> bool:
    """Return True if ``file_source`` is a LightRAG document-chunk ID.

    Chunk IDs have the shape ``doc-{32 hex chars}-chunk-{NNN}`` (e.g.
    ``doc-a1e468b48b942cc5d675221767a6a061-chunk-000``), produced by
    ``lightrag/utils_pipeline.py:build_chunks_dict_from_chunking_result``.
    They identify a text chunk, not an image file, so a hub whose
    ``source_id`` is a chunk ID must NOT be treated as a Photo — the UI
    would request ``/api/kg/images/photo/{chunk_id}`` and 404.
    """
    if not file_source:
        return False
    if "<SEP>" in file_source:
        return False
    return bool(_RE_DOC_CHUNK.match(file_source))


__all__ = ("NOTE_LABEL_PREFIXES", "is_note_file_source", "is_doc_chunk_source")