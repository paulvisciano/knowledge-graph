from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from api.services import config, db as db_module

logger = logging.getLogger(__name__)

router = APIRouter(tags=["graph"])

# How a Photo node identifies its backing file. The LightRAG entity is created
# in processor.create_exif_relations with source_id = file_source, and the node
# id is "{file_source} (Photo)". The properties dict carries source_id /
# file_path. Any of these may hold the file_source we key on in photo_metadata.
_PHOTO_LABELS = ("photo", "image")


def _is_photo_node(node: dict[str, Any]) -> bool:
    labels = node.get("labels") or []
    for lab in labels:
        if str(lab).lower() in _PHOTO_LABELS:
            return True
    props = node.get("properties") or {}
    etype = props.get("entity_type")
    if isinstance(etype, str) and etype.lower() in _PHOTO_LABELS:
        return True
    nid = str(node.get("id") or "")
    return nid.endswith(" (Photo)") or nid.endswith(" (Image)")


def _file_source_from_node(node: dict[str, Any]) -> str | None:
    props = node.get("properties") or {}
    for key in ("source_id", "file_path", "file_source"):
        val = props.get(key)
        if isinstance(val, str) and val and val != "manual_creation":
            return val
    nid = str(node.get("id") or "")
    for suffix in (" (Photo)", " (Image)"):
        if nid.endswith(suffix):
            return nid[: -len(suffix)]
    return None


@router.get("/graphs")
async def get_graph(
    label: str = Query(..., description="Label to get knowledge graph for"),
    max_depth: int = Query(3, ge=1),
    max_nodes: int = Query(1000, ge=1),
    node_id: str | None = Query(None),
):
    """Proxy LightRAG's /graphs, enriching Photo nodes with EXIF date/dimension
    fields from photo_metadata so the UI gets them in a single response instead
    of a second bulk-dates round-trip that scanned every photo and PIL-decoded
    legacy files on each page load.
    """
    base = config.lightrag_url()
    params: dict[str, Any] = {
        "label": label,
        "max_depth": max_depth,
        "max_nodes": max_nodes,
    }
    if node_id:
        params["node_id"] = node_id

    try:
        async with httpx.AsyncClient(timeout=config.http_timeouts().long) as client:
            resp = await client.get(f"{base}/graphs", params=params)
    except httpx.RequestError as exc:
        logger.warning("LightRAG /graphs proxy failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LightRAG unreachable: {exc}")

    if resp.status_code != 200:
        return JSONResponse(status_code=resp.status_code, content=resp.json())

    payload = resp.json()
    nodes = payload.get("nodes") or []

    photo_sources: list[str] = []
    for node in nodes:
        if _is_photo_node(node):
            src = _file_source_from_node(node)
            if src:
                photo_sources.append(src)

    if photo_sources:
        dates = await db_module.get_photo_dates_for_sources(photo_sources)
        if dates:
            for node in nodes:
                if not _is_photo_node(node):
                    continue
                src = _file_source_from_node(node)
                if not src:
                    continue
                exif = dates.get(src)
                if exif:
                    props = dict(node.get("properties") or {})
                    props.update(exif)
                    node["properties"] = props

    return payload