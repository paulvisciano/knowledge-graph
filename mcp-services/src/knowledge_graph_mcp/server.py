"""Knowledge Graph MCP facade — 2 curated KG tools calling LightRAG REST directly."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

import zoneinfo

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware

_LIGHTRAG_API_URL = os.getenv("LIGHTRAG_API_URL", "http://localhost:9621")
_LIGHTRAG_API_KEY = os.getenv("LIGHTRAG_API_KEY", "")
_MCP_PORT = int(os.getenv("MEMORY_SEARCH_MCP_PORT", "9653"))

logger = logging.getLogger("knowledge_graph_mcp")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if _LIGHTRAG_API_KEY:
        h["X-API-Key"] = _LIGHTRAG_API_KEY
    return h


asyncio_sleep = asyncio.sleep
asyncio_monotonic = time.monotonic


mcp = FastMCP(
    "KnowledgeGraph",
    stateless_http=True,
    json_response=True,
    instructions=(
        "This server exposes two tools for a personal knowledge graph (LightRAG-backed): "
        "save_to_knowledge_graph and query_knowledge_graph.\n"
        "\n"
        "## save_to_knowledge_graph(text, file_source)\n"
        "Insert text into the graph. Entities and relations are extracted automatically server-side. "
        "Use it whenever the user shares something to be remembered: an activity, a preference, a fact, "
        "a note, or a correction.\n"
        "- file_source labels: 'diary-entry', 'chat-note', 'preference-update', 'correction'. "
        "Append the current date when known (e.g. diary-entry-2026-07-27).\n"
        "- A save only happens when this tool is actually called and returns. Never claim a save "
        "succeeded without calling it.\n"
        "\n"
        "## query_knowledge_graph(query, mode, top_k)\n"
        "Retrieve stored personal information. Call it when the user asks about their own past, "
        "people, places, activities, preferences, or photos. Do NOT call it for general world knowledge.\n"
        "- mode='mix' (default): combined graph + vector retrieval. Use for most queries. Pass top_k=5.\n"
        "- mode='local': focused entity lookups.\n"
        "- mode='global': broad overviews of how entities relate across the graph.\n"
        "\n"
        "Conversational behavior, phrasing, and persona are owned by the host client's system prompt, "
        "not by these instructions."
    ),
)


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg", ".raw-01", ".ts-000-01"}


# ---------------------------------------------------------------------------
# NLP keyword extraction — replaces LightRAG's keyword extraction LLM call.
#
# LightRAG's get_keywords_from_query() calls a 12B model to split a query into
# high-level (concept) and low-level (specific) keywords. On a local M2 Pro
# that takes ~90-140s per query because prompt processing is slow.
#
# Pre-supplying hl_keywords/ll_keywords makes LightRAG skip that LLM call
# entirely (operate.py:4036 returns immediately), cutting query latency from
# ~120s to ~1-3s with no loss in retrieval quality for personal-knowledge-graph
# queries, which are overwhelmingly date- and entity-driven.
#
# Strategy (modeled on RAGFlow's rag/nlp/query.py — NLP-only, no LLM):
#   1. Regex-extract date patterns (months, years, ISO dates, ordinals)
#   2. Regex-extract capitalized proper nouns (places, names, brands)
#   3. Strip stopwords, keep content words
#   4. hl_keywords = broad concepts (months, years, "photos", "notes", "beach")
#   5. ll_keywords = specific terms (dates, proper nouns, entity names)
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    # English articles / pronouns / common verbs
    "a", "an", "the", "i", "me", "my", "we", "our", "you", "your", "he", "she",
    "it", "its", "they", "them", "his", "her", "their", "this", "that", "these",
    "those", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "must", "can", "shall", "of", "to", "in", "on", "at", "by", "for",
    "with", "about", "as", "into", "through", "during", "before", "after",
    "above", "below", "from", "up", "down", "out", "off", "over", "under",
    "again", "further", "then", "once", "and", "or", "but", "if", "so", "than",
    "too", "very", "just", "also", "only", "no", "not", "nor",
    # Filler words common in voice transcriptions
    "hey", "so", "um", "uh", "like", "know", "you know", "actually", "really",
    "crazy", "kind", "sort", "stuff", "things", "thing", "way", "want", "wanted",
    "thinking", "wondering", "let", "lets", "going", "gonna", "wanna", "gotta",
    "there", "here", "where", "when", "what", "which", "who", "whom", "whose",
    "how", "why", "whether", "back", "out", "get", "got", "make", "made",
    "show", "tell", "give", "take", "see", "seen", "saw", "compare", "between",
    "any", "some", "all", "both", "each", "few", "more", "most", "other",
    "such", "own", "same",
})

_MONTHS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
})

# Regex patterns compiled once
_RE_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_RE_YEAR = re.compile(r"\b(20\d{2})\b")
_RE_MONTH_YEAR = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b", re.IGNORECASE)
_RE_MONTH_ORDINAL = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)
_RE_ORDINAL_DATE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_RE_PROPER_NOUN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
_RE_TOKEN = re.compile(r"[A-Za-z]+|\d{4}-\d{2}-\d{2}|\d{4}|\d{1,2}(?:st|nd|rd|th)?")

# Content categories that map to entity types in the knowledge graph
_CONTENT_CATEGORIES = frozenset({
    "photos", "photo", "pictures", "picture", "images", "image",
    "notes", "note", "diary", "journal", "entries", "entry",
    "activities", "activity",
    "people", "person", "friends", "family",
    "places", "place", "locations", "location",
    "preferences", "preference", "settings", "setting",
})


def _extract_keywords(query: str) -> tuple[list[str], list[str]]:
    """Extract high-level and low-level keywords from a query string using NLP only.

    Returns (hl_keywords, ll_keywords) — the same structure LightRAG's keyword
    extraction LLM would produce, but in microseconds instead of ~120 seconds.

    - hl_keywords: broad concepts (months, years, content categories like "photos"/"notes")
    - ll_keywords: specific terms (dates, proper nouns, entity names)
    """
    hl: list[str] = []
    ll: list[str] = []
    seen_hl: set[str] = set()
    seen_ll: set[str] = set()

    def _add_hl(kw: str) -> None:
        kw = kw.strip()
        if kw and kw.lower() not in seen_hl:
            seen_hl.add(kw.lower())
            hl.append(kw)

    def _add_ll(kw: str) -> None:
        kw = kw.strip()
        if kw and kw.lower() not in seen_ll:
            seen_ll.add(kw.lower())
            ll.append(kw)

    # Track whether a specific date (month+day) was found so we can suppress
    # year-only keywords that would act as year-wide wildcards, and bare day
    # numbers that would match generic "Person N" entities.
    specific_date_found = False
    detected_month: str | None = None
    detected_day: str | None = None
    detected_year: str | None = None

    # 1. ISO dates (e.g. "2026-07-22") → low-level
    for m in _RE_ISO_DATE.findall(query):
        _add_ll(m)
        specific_date_found = True
        detected_year = m[:4]

    # 2. "Month Year" patterns (e.g. "June 2026") → both levels
    for m in _RE_MONTH_YEAR.finditer(query):
        month, year = m.group(1).capitalize(), m.group(2)
        _add_hl(month)
        _add_ll(f"{month} {year}")
        detected_year = year

    # 3. "Month ordinal" patterns (e.g. "July 22nd") → low-level
    for m in _RE_MONTH_ORDINAL.finditer(query):
        month, day = m.group(1).capitalize(), m.group(2)
        _add_hl(month)
        _add_ll(f"{month} {day}")
        specific_date_found = True
        detected_month = m.group(1).lower()
        detected_day = day

    # 4. Standalone months (e.g. "June", "July") → high-level
    for tok in _RE_TOKEN.findall(query):
        if tok.lower() in _MONTHS:
            _add_hl(tok.capitalize())
            if detected_month is None:
                detected_month = tok.lower()

    # 5. Years (e.g. "2026") → high-level, but suppress when a specific
    #    date was already found — "2026" as a standalone hl_keyword matches
    #    every Date entity in the graph via embedding similarity.
    if not specific_date_found:
        for m in _RE_YEAR.findall(query):
            _add_hl(m)
            if detected_year is None:
                detected_year = m
    else:
        for m in _RE_YEAR.findall(query):
            detected_year = m

    # 6. Ordinal dates without month (e.g. "22nd") → low-level, but skip
    #    when a specific month+day was already found — the bare "27" would
    #    match generic "Person 27" entities in the graph.
    if not specific_date_found:
        for m in _RE_ORDINAL_DATE.findall(query):
            _add_ll(m)

    # Generate canonical ISO date keyword when month+day are present so the
    # entity VDB directly hits the right "YYYY-MM-DD (Date)" node.
    if detected_month and detected_day:
        month_num = _MONTH_NAMES.get(detected_month)
        if month_num:
            year = detected_year or str(datetime.now().year)
            iso_date = f"{year}-{month_num:02d}-{int(detected_day):02d}"
            _add_ll(iso_date)

    # 7. Proper nouns (capitalized words that aren't sentence starts)
    #    e.g. "St Pete", "Beach", "Bike Ride" → low-level
    #    Skip first word of the query (likely sentence start, not a proper noun)
    words = query.split()
    first_word = words[0] if words else ""
    for m in _RE_PROPER_NOUN.findall(query):
        if m == first_word and len(words) > 1:
            # Could be sentence start — only include if it's not a common sentence opener
            if m.lower() in _STOPWORDS or m.lower() in {"show", "what", "how", "tell", "give", "hey"}:
                continue
        _add_ll(m)

    # 8. Content categories (e.g. "photos", "notes", "beach") → high-level
    #    Also pick up any non-stopword content tokens → low-level
    tokens = re.findall(r"[A-Za-z]+", query)
    for tok in tokens:
        lower = tok.lower()
        if lower in _CONTENT_CATEGORIES:
            _add_hl(lower)
        if lower not in _STOPWORDS and lower not in _MONTHS and not _RE_YEAR.fullmatch(tok):
            # Only add multi-char tokens as low-level (skip single letters)
            if len(lower) > 2:
                _add_ll(tok)

    # Deduplicate: remove hl keywords that are already in ll (prefer ll for specificity)
    ll_lower = {k.lower() for k in ll}
    hl = [k for k in hl if k.lower() not in ll_lower]

    # LightRAG requires non-empty keyword lists for local/hybrid/mix modes.
    # If we extracted nothing useful, fall back to the raw query tokens.
    if not ll:
        ll = [t for t in tokens if t.lower() not in _STOPWORDS and len(t) > 2][:10]
    if not hl:
        hl = ll[:3] if ll else [query[:50]]

    return hl, ll


def _is_image_path(file_path: str) -> bool:
    """Heuristic: a file path is treated as an image if it ends with a known
    image extension OR matches a non-standard VLM suffix (e.g. .RAW-01, .TS-000-01).
    The frontend resolves the final URL against the document index, so being
    permissive here is safe — non-image refs that fail to resolve are dropped
    by the UI's onerror handler."""
    lower = file_path.lower()
    if any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS):
        return True
    return False


_RE_GENERIC_PERSON = re.compile(r"^Person \d+$")

# Matches a date in entity/event names like "June 27, 2026", "June 27th",
# "2026-06-27", etc. Used to detect event-type entities that carry a date.
_RE_DATE_IN_NAME = re.compile(
    r"(?:\b\d{4}-\d{2}-\d{2}\b)"
    r"|(?:\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b)",
    re.IGNORECASE,
)


def _extract_date_from_name(name: str) -> str | None:
    """Try to extract a canonical YYYY-MM-DD date from an entity/event name."""
    # ISO format: 2026-06-27
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # "June 27, 2026" or "June 27th, 2026"
    month_map = _MONTH_NAMES
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
        name,
        re.IGNORECASE,
    )
    if m:
        mon = month_map.get(m.group(1).lower())
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    return None


def _filter_response_by_date(response: str, query_date: datetime) -> str:
    """Filter LightRAG's context response to drop entities not matching the queried date.

    Parses the entity and relationship JSON blocks in the response and removes:
    - Date entities whose date differs from the query date (exact match required)
    - Event entities whose name contains a date that doesn't match the query date
    - Relationships involving filtered-out Date or event entities
    - Relationships with wrong-date "taken_on" descriptions (±1 day tolerance)
    - Generic "Person N" entities (LLM-generated labels with no real identity)

    If no Date entity matches the queried date AND no photo relationships match,
    returns a minimal response indicating no data for that date.

    Returns the filtered response with the same structure.
    """
    from datetime import timedelta

    lines = response.split("\n")
    filtered: list[str] = []
    in_entity_block = False
    in_relation_block = False

    valid_dates_exact: set[str] = {query_date.strftime("%Y-%m-%d")}
    valid_dates_tolerance: set[str] = set()
    for d in range(-1, 2):
        dt = query_date + timedelta(days=d)
        valid_dates_tolerance.add(dt.strftime("%Y-%m-%d"))

    query_date_iso = query_date.strftime("%Y-%m-%d")

    # Track which entities to drop (by entity name)
    dropped_entities: set[str] = set()
    # Track which entities to keep (Date or event entities matching the date)
    kept_date_entities: set[str] = set()
    # Track any photo relationships that survived filtering
    has_matching_photos = False

    # First pass: identify entities to drop and relationships to keep
    # We need two passes: one to find which Date/event entities match, one to filter relationships
    current_section = None  # "entity" | "relation" | None
    parsed_entities: list[dict] = []
    parsed_relations: list[dict] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "Knowledge Graph Data (Entity):":
            current_section = "entity"
            continue
        if stripped == "Knowledge Graph Data (Relationship):":
            current_section = "relation"
            continue
        if stripped.startswith("Document Chunks") or stripped.startswith("Reference Document List"):
            current_section = None
            continue
        if not stripped.startswith("{"):
            continue

        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        if current_section == "entity":
            if "entity" in obj:
                parsed_entities.append(obj)
        elif current_section == "relation":
            if "entity1" in obj and "entity2" in obj:
                parsed_relations.append(obj)

    # Evaluate entities
    for obj in parsed_entities:
        entity_name = obj.get("entity", "")
        entity_type = obj.get("type", "")

        if _RE_GENERIC_PERSON.match(entity_name):
            dropped_entities.add(entity_name)
            continue

        if entity_type == "Date":
            m = re.search(r"(\d{4}-\d{2}-\d{2})", entity_name)
            if m:
                date_str = m.group(1)
                if date_str in valid_dates_exact:
                    kept_date_entities.add(entity_name)
                else:
                    dropped_entities.add(entity_name)
            # else: Date entity without parseable date — keep it
        elif _RE_DATE_IN_NAME.search(entity_name):
            # Event or other entity with a date in its name (e.g., "June 27, 2026")
            extracted = _extract_date_from_name(entity_name)
            if extracted and extracted != query_date_iso:
                dropped_entities.add(entity_name)
            elif extracted and extracted == query_date_iso:
                kept_date_entities.add(entity_name)
        # else: non-date entity without date in name — keep (will be filtered via relationships if needed)

    # Evaluate relationships
    kept_relations: list[dict] = []
    for obj in parsed_relations:
        e1 = obj.get("entity1", "")
        e2 = obj.get("entity2", "")
        desc = obj.get("description", "")

        if _RE_GENERIC_PERSON.match(e1) or _RE_GENERIC_PERSON.match(e2):
            continue

        # Drop if either endpoint is a dropped entity
        if e1 in dropped_entities or e2 in dropped_entities:
            continue

        # Drop if relationship description has a taken_on date that doesn't match (±1 day tolerance)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", desc)
        if m and "taken_on" in desc.lower():
            date_str = m.group(1)
            if date_str not in valid_dates_tolerance:
                continue

        # Drop if either endpoint is a Date entity not matching the query
        if "(Date)" in e1:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", e1)
            if m and m.group(1) not in valid_dates_exact:
                continue
        if "(Date)" in e2:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", e2)
            if m and m.group(1) not in valid_dates_exact:
                continue

        # Track if we have any photo relationships surviving
        if "(Photo)" in e1 or "(Photo)" in e2:
            has_matching_photos = True

        kept_relations.append(obj)

    # Check if any kept entity is the matching Date or a photo/note linked to it
    has_matching_date_entity = bool(kept_date_entities)

    # If the queried date has no matching Date entity and no matching photos,
    # return a minimal response instead of unrelated content
    if not has_matching_date_entity and not has_matching_photos:
        # Check if any kept relation references the queried date at all
        date_ref = query_date_iso
        any_match = False
        for rel in kept_relations:
            if date_ref in rel.get("description", "") or date_ref in rel.get("entity1", "") or date_ref in rel.get("entity2", ""):
                any_match = True
                break
        if not any_match:
            return f"\nNo knowledge graph data found for {query_date_iso}.\n"

    # Second pass: rebuild the response with filtered entities and relationships
    # Re-emit kept entities (non-dropped)
    kept_entity_objs = [e for e in parsed_entities if e.get("entity", "") not in dropped_entities]

    result_lines: list[str] = []
    result_lines.append("")
    result_lines.append("Knowledge Graph Data (Entity):")
    result_lines.append("")
    result_lines.append("```json")
    for obj in kept_entity_objs:
        result_lines.append(json.dumps(obj))
    result_lines.append("```")
    result_lines.append("")
    result_lines.append("Knowledge Graph Data (Relationship):")
    result_lines.append("")
    result_lines.append("```json")
    for obj in kept_relations:
        result_lines.append(json.dumps(obj))
    result_lines.append("```")
    result_lines.append("")

    # Preserve any remaining sections (Document Chunks, Reference Document List)
    # by appending them from the original response if present, filtered by date
    remaining = _extract_trailing_sections(response, query_date_iso, valid_dates_tolerance)
    if remaining:
        result_lines.append(remaining)

    return "\n".join(result_lines)


def _extract_trailing_sections(response: str, query_date_iso: str, valid_dates: set[str]) -> str:
    """Extract Document Chunks and Reference Document List sections, filtered by date.

    Drops document chunks whose content references a specific date that doesn't
    match the queried date (±1 day tolerance). Chunks with no explicit date are kept.
    """
    marker = "Document Chunks"
    idx = response.find(marker)
    if idx == -1:
        return ""

    query_year = int(query_date_iso[:4])

    trailing = response[idx:]
    lines = trailing.split("\n")
    filtered: list[str] = []
    in_chunk_block = False
    in_ref_block = False
    kept_ref_ids: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Document Chunks"):
            in_chunk_block = True
            in_ref_block = False
            filtered.append(line)
            continue
        if stripped.startswith("Reference Document List"):
            in_chunk_block = False
            in_ref_block = True
            filtered.append(line)
            continue
        if stripped.startswith("```"):
            filtered.append(line)
            continue

        if in_chunk_block and stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
                content = obj.get("content", "")
                ref_id = obj.get("reference_id", "")
                dates_in_content = set(re.findall(r"\d{4}-\d{2}-\d{2}", content))
                for m in re.finditer(
                    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
                    content, re.IGNORECASE,
                ):
                    mon = _MONTH_NAMES.get(m.group(1).lower())
                    day = int(m.group(2))
                    yr = m.group(3)
                    if yr and mon:
                        dates_in_content.add(f"{yr}-{mon:02d}-{day:02d}")
                    elif mon:
                        dates_in_content.add(f"{query_year}-{mon:02d}-{day:02d}")
                if dates_in_content and not dates_in_content.intersection(valid_dates):
                    continue
                kept_ref_ids.add(ref_id)
                filtered.append(line)
            except json.JSONDecodeError:
                filtered.append(line)
        elif in_ref_block:
            # Keep reference list entries for kept chunks
            ref_match = re.match(r"\[(\d+)\]", stripped)
            if ref_match:
                if ref_match.group(1) in kept_ref_ids:
                    filtered.append(line)
            else:
                filtered.append(line)
        else:
            filtered.append(line)

    return "\n".join(filtered)


@mcp.tool()
async def query_knowledge_graph(
    query: str, mode: str = "mix", only_need_context: bool = True, top_k: int = 15
) -> str:
    """Search the knowledge graph for information relevant to a query. Use mode='mix' (default) for most queries — combines knowledge graph and vector retrieval, returns both photos and notes. Use mode='local' for focused entity lookups when you need specific entities. Use mode='global' only for broad overviews."""
    hl_keywords, ll_keywords = _extract_keywords(query)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_LIGHTRAG_API_URL}/query",
                headers=_headers(),
                json={
                    "query": query,
                    "mode": mode,
                    "only_need_context": only_need_context,
                    "top_k": top_k,
                    "include_references": True,
                    "hl_keywords": hl_keywords,
                    "ll_keywords": ll_keywords,
                },
            )
            r.raise_for_status()
            result = r.json()
        response = result.get("response", "")

        query_date = _scan_text_for_date(query)
        if query_date and response:
            response = _filter_response_by_date(response, query_date)

        if query_date and response and "No knowledge graph data found" not in response:
            photo_names = _extract_photo_names(response)
            if photo_names:
                image_descs = await _fetch_image_descriptions(photo_names)
                if image_descs:
                    response = response + "\n\nImage Descriptions:\n\n" + image_descs

        image_refs: list[str] = []
        for ref in result.get("references") or []:
            fp = ref.get("file_path", "")
            if fp and fp != "unknown_source" and _is_image_path(fp):
                image_refs.append(fp)

        if image_refs:
            response = (response or "No results found.") + "\n\n---IMAGE_REFS---\n" + "\n".join(image_refs)
        return response if response else "No results found."
    except Exception as e:
        return f"Error querying knowledge graph: {e}"


def _extract_photo_names(response: str) -> list[str]:
    """Extract photo filenames from Photo entity names in the filtered response."""
    names: list[str] = []
    for m in re.finditer(r"((?:PXL_|IMG_|DSC_|DCIM_)[\w.-]+\.jpg)", response):
        name = m.group(1)
        if name not in names:
            names.append(name)
    return names


async def _fetch_image_descriptions(photo_names: list[str]) -> str:
    """Fetch full image descriptions from LightRAG for the given photo filenames.

    Two-step: (1) list all documents to build a file_path→doc_id map, (2) fetch
    full_content for each doc_id whose file_path matches a photo name.
    """
    import httpx
    descriptions: list[str] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        list_r = await client.get(
            f"{_LIGHTRAG_API_URL}/documents",
            params={"limit": 500},
            headers=_headers(),
        )
        if list_r.status_code != 200:
            return ""
        data = list_r.json()
        processed = data.get("statuses", {}).get("processed", [])
        name_set = set(photo_names)
        doc_ids: list[str] = []
        for doc in processed:
            fp = doc.get("file_path", "")
            if fp and fp in name_set:
                doc_ids.append((fp, doc.get("id", "")))
        for fp, doc_id in doc_ids:
            if not doc_id:
                continue
            try:
                content_r = await client.get(
                    f"{_LIGHTRAG_API_URL}/documents/{doc_id}/full_content",
                    headers=_headers(),
                )
                if content_r.status_code != 200:
                    continue
                content = content_r.json().get("content", "")
                if content:
                    descriptions.append(content)
            except Exception:
                continue
    return "\n\n---\n\n".join(descriptions)


# LightRAG /documents/text returns HTTP 409 in three distinct situations.
# Only one is a genuine same-name conflict (permanent — the timestamp suffix
# on file_source already prevents that). The other two are TRANSIENT pipeline
# states that clear in seconds but were never retried, so every insert that
# happened to land during a scan-classification window or a clear/delete
# surfaced as a 409 error to the caller. Match the detail strings emitted by
# document_routes.py::_reserve_enqueue_slot and retry with backoff.
_TRANSIENT_409_MARKERS = (
    "Document scan is classifying files",
    "Pipeline is clearing or deleting documents",
    "Wait for the running job",
)
_TRANSIENT_409_MAX_ATTEMPTS = 6
_TRANSIENT_409_BASE_SLEEP = 1.0  # seconds; doubled each attempt, capped at 10s


def _is_transient_409(resp: httpx.Response) -> bool:
    if resp.status_code != 409:
        return False
    try:
        body = resp.json()
        detail = str(body.get("detail", ""))
    except Exception:
        detail = resp.text or ""
    return any(m.lower() in detail.lower() for m in _TRANSIENT_409_MARKERS)


# --- Post-processing: link extracted entities to a Note hub + Date node ---------
# Mirrors api/services/processor.py::link_note_to_date but implemented inline
# using httpx (this container can't import the API service).  Runs as a
# fire-and-forget asyncio task AFTER save_to_knowledge_graph returns, so the
# MCP caller gets its track ID promptly.  Best-effort: any failure is logged
# but never surfaced to the caller (the text is already saved in LightRAG).

_PIPELINE_BUSY_MARKERS = (
    "Pipeline is busy",
    "pipeline is busy",
    "Wait for the running job",
)
_PIPELINE_BUSY_TOTAL_TIMEOUT = float(os.getenv("PIPELINE_BUSY_TIMEOUT", "300"))
_PIPELINE_BUSY_MAX_SLEEP = 30.0
_EXIF_SUFFIXES = (" (Date)", " (Camera)", " (Location)", " (Photo)", " (Note)")
_LINK_POLL_INTERVAL = 3.0
_LINK_POLL_TIMEOUT = 300.0


def _classify_create_error(resp: httpx.Response) -> str:
    """Classify a /graph/{entity,relation}/create response like processor.py."""
    if resp.status_code == 409:
        try:
            body = resp.json()
            detail = str(body.get("detail", ""))
        except Exception:
            detail = resp.text or ""
        combined = f"{resp.status_code} {detail}"
        if any(m in combined for m in _PIPELINE_BUSY_MARKERS):
            return "busy"
        if any(m in combined for m in ("not found", "not exist", "does not exist")):
            return "error"
        return "exists"
    if resp.status_code == 400:
        try:
            body = resp.json()
            detail = str(body.get("detail", ""))
        except Exception:
            detail = resp.text or ""
        if any(m in detail for m in ("not found", "not exist", "does not exist")):
            return "error"
        if any(m in detail for m in ("already exists", "Already exists", "already exist")):
            return "exists"
        return "error"
    if 200 <= resp.status_code < 300:
        return "ok"
    return "error"


async def _create_entity_verified(
    client: httpx.AsyncClient,
    entity_name: str,
    entity_data: dict,
    *,
    max_attempts: int = 5,
) -> dict:
    """POST /graph/entity/create with busy-retry + exists-verify, like processor.py."""
    busy_deadline = asyncio_monotonic() + _PIPELINE_BUSY_TOTAL_TIMEOUT
    busy_sleep = 2.0
    exists_retries_left = max_attempts

    while True:
        r = await client.post(
            "/graph/entity/create",
            headers=_headers(),
            json={"entity_name": entity_name, "entity_data": entity_data},
        )
        kind = _classify_create_error(r)
        if kind == "ok":
            logger.info("[MCP Link] Created entity '%s'", entity_name)
            try:
                return {**r.json(), "status": r.json().get("status", "success")}
            except Exception:
                return {"status": "success", "entity_name": entity_name}
        if kind == "error":
            logger.warning("[MCP Link] Entity '%s' create failed: %s %s", entity_name, r.status_code, r.text[:200])
            return {"status": "error", "entity_name": entity_name, "error": f"{r.status_code}: {r.text[:200]}"}
        if kind == "busy":
            if asyncio_monotonic() >= busy_deadline:
                logger.error("[MCP Link] Entity '%s' pipeline busy %ds, giving up", entity_name, _PIPELINE_BUSY_TOTAL_TIMEOUT)
                return {"status": "error", "entity_name": entity_name, "error": f"Pipeline busy after {_PIPELINE_BUSY_TOTAL_TIMEOUT}s"}
            logger.info("[MCP Link] Entity '%s' pipeline busy, retry in %.1fs", entity_name, busy_sleep)
            await asyncio_sleep(busy_sleep)
            busy_sleep = min(busy_sleep * 1.7, _PIPELINE_BUSY_MAX_SLEEP)
            continue
        # kind == "exists": verify via label list
        await asyncio_sleep(3.0 * (max_attempts - exists_retries_left + 1))
        try:
            lr = await client.get("/graph/label/list", headers=_headers())
            labels = lr.json() if lr.status_code == 200 else []
        except Exception:
            labels = []
        if entity_name in labels:
            logger.info("[MCP Link] Entity '%s' confirmed existing", entity_name)
            return {"status": "exists", "entity_name": entity_name}
        exists_retries_left -= 1
        if exists_retries_left <= 0:
            logger.error("[MCP Link] Entity '%s' reported exists but missing after %d verifies", entity_name, max_attempts)
            return {"status": "error", "entity_name": entity_name, "error": f"Conflict but missing after {max_attempts} verifies"}
        logger.warning("[MCP Link] Entity '%s' conflict but not found, retry (%d/%d)", entity_name, max_attempts - exists_retries_left + 1, max_attempts)


async def _create_relation_verified(
    client: httpx.AsyncClient,
    source_entity: str,
    target_entity: str,
    relation_data: dict,
    *,
    max_attempts: int = 5,
) -> dict:
    """POST /graph/relation/create with preflight + busy-retry, like processor.py."""
    # Preflight: skip POST if edge already exists (silences 400 "already exists" log noise).
    try:
        pr = await client.get(
            "/graphs",
            params={"label": source_entity, "max_depth": 1, "max_nodes": 500},
            headers=_headers(),
        )
        if pr.status_code == 200:
            data = pr.json()
            for edge in data.get("edges", []):
                src, tgt = edge.get("source", ""), edge.get("target", "")
                if (src == source_entity and tgt == target_entity) or (src == target_entity and tgt == source_entity):
                    logger.info("[MCP Link] Relation '%s'->'%s' preflight found edge, skipping", source_entity, target_entity)
                    return {"status": "exists", "source": source_entity, "target": target_entity}
    except Exception as exc:
        logger.debug("[MCP Link] preflight failed for '%s'->'%s': %s", source_entity, target_entity, exc)

    busy_deadline = asyncio_monotonic() + _PIPELINE_BUSY_TOTAL_TIMEOUT
    busy_sleep = 2.0
    exists_retries_left = max_attempts

    while True:
        r = await client.post(
            "/graph/relation/create",
            headers=_headers(),
            json={"source_entity": source_entity, "target_entity": target_entity, "relation_data": relation_data},
        )
        kind = _classify_create_error(r)
        if kind == "ok":
            logger.info("[MCP Link] Created relation '%s'->'%s'", source_entity, target_entity)
            try:
                return {**r.json(), "status": r.json().get("status", "success")}
            except Exception:
                return {"status": "success", "source": source_entity, "target": target_entity}
        if kind == "error":
            logger.warning("[MCP Link] Relation '%s'->'%s' failed: %s %s", source_entity, target_entity, r.status_code, r.text[:200])
            return {"status": "error", "source": source_entity, "target": target_entity, "error": f"{r.status_code}: {r.text[:200]}"}
        if kind == "busy":
            if asyncio_monotonic() >= busy_deadline:
                logger.error("[MCP Link] Relation '%s'->'%s' pipeline busy %ds, giving up", source_entity, target_entity, _PIPELINE_BUSY_TOTAL_TIMEOUT)
                return {"status": "error", "source": source_entity, "target": target_entity, "error": f"Pipeline busy after {_PIPELINE_BUSY_TOTAL_TIMEOUT}s"}
            logger.info("[MCP Link] Relation '%s'->'%s' pipeline busy, retry in %.1fs", source_entity, target_entity, busy_sleep)
            await asyncio_sleep(busy_sleep)
            busy_sleep = min(busy_sleep * 1.7, _PIPELINE_BUSY_MAX_SLEEP)
            continue
        # kind == "exists": verify via source subgraph edges.
        await asyncio_sleep(3.0 * (max_attempts - exists_retries_left + 1))
        try:
            vr = await client.get(
                "/graphs",
                params={"label": source_entity, "max_depth": 1, "max_nodes": 500},
                headers=_headers(),
            )
            data = vr.json() if vr.status_code == 200 else {}
        except Exception:
            data = {}
        for edge in data.get("edges", []):
            src, tgt = edge.get("source", ""), edge.get("target", "")
            if (src == source_entity and tgt == target_entity) or (src == target_entity and tgt == source_entity):
                logger.info("[MCP Link] Relation '%s'->'%s' confirmed existing", source_entity, target_entity)
                return {"status": "exists", "source": source_entity, "target": target_entity}
        exists_retries_left -= 1
        if exists_retries_left <= 0:
            logger.error("[MCP Link] Relation '%s'->'%s' reported exists but missing after %d verifies", source_entity, target_entity, max_attempts)
            return {"status": "error", "source": source_entity, "target": target_entity, "error": f"Conflict but missing after {max_attempts} verifies"}
        logger.warning("[MCP Link] Relation '%s'->'%s' conflict but not found, retry (%d/%d)", source_entity, target_entity, max_attempts - exists_retries_left + 1, max_attempts)


def _parse_file_source_date(file_source: str):
    """Extract a local-calendar datetime from a save file_source.

    Recognises the trailing MCP timestamp suffix ``YYYYMMDD-HHMMSS-microseconds``
    appended by save_to_knowledge_graph, and the legacy ``note_<epoch>`` shape.
    Returns a tz-aware datetime or None.
    """
    try:
        tz = zoneinfo.ZoneInfo(os.environ.get("TZ", "America/New_York"))
    except Exception:
        tz = zoneinfo.ZoneInfo("America/New_York")

    m = re.search(r"(\d{8})-(\d{6})-(\d{6})$", file_source)
    if m:
        ymd, hms, _ = m.groups()
        try:
            naive = datetime.strptime(f"{ymd}{hms}", "%Y%m%d%H%M%S")
            return naive.replace(tzinfo=tz)
        except ValueError:
            return None

    m = re.match(r"^note_(\d+)$", file_source)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)), tz=tz)
        except (ValueError, OSError, OverflowError):
            return None
    return None


# Month name → number lookup for _scan_text_for_date.
_MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _scan_text_for_date(text: str) -> datetime | None:
    """Scan note text for the first explicit date mention.

    Supports journaling about past events — e.g. "On July 22 I went biking"
    resolves to July 22 of the current year, even if the note was saved on
    a different day.  Recognised patterns (checked in order, first match wins):

    1. ISO dates: ``2024-07-22`` or ``2024/07/22``
    2. US numeric: ``07/22/2024``, ``7/22/24``, ``07-22-2024``
    3. Natural language: ``July 22``, ``July 22, 2024``, ``Jul 22``,
       ``22 July 2024``, ``on July 22``, ``It's July 27``

    If a year is absent, the current year is assumed.  Returns a tz-aware
    datetime at midnight (date-level precision), or ``None`` if no date found.
    """
    try:
        tz = zoneinfo.ZoneInfo(os.environ.get("TZ", "America/New_York"))
    except Exception:
        tz = zoneinfo.ZoneInfo("America/New_York")
    current_year = datetime.now(tz=tz).year

    # 1) ISO: 2024-07-22 or 2024/07/22
    m = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=tz)
        except ValueError:
            pass

    # 2) US numeric: 07/22/2024, 7/22/24, 07-22-2024
    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b", text)
    if m:
        year = int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, int(m.group(1)), int(m.group(2)), tzinfo=tz)
        except ValueError:
            pass

    # 3) Natural language: "July 22", "July 22, 2024", "Jul 22", "22 July 2024",
    #    "June 27th", "July 22nd"
    month_alt = "|".join(_MONTH_NAMES.keys())
    # Pattern 3a: "July 22" or "July 22, 2024" or "June 27th"
    m = re.search(rf"\b({month_alt})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", text, re.IGNORECASE)
    if m:
        month = _MONTH_NAMES[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else current_year
        try:
            return datetime(year, month, day, tzinfo=tz)
        except ValueError:
            pass
    # Pattern 3b: "22 July 2024" or "22 July" or "27th June"
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_alt})(?:\s+(\d{{4}}))?\b", text, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        month = _MONTH_NAMES[m.group(2).lower()]
        year = int(m.group(3)) if m.group(3) else current_year
        try:
            return datetime(year, month, day, tzinfo=tz)
        except ValueError:
            pass

    return None


async def _wait_for_lightrag_processing(client: httpx.AsyncClient, file_source: str) -> str:
    """Poll /documents until our file_source reaches processed/failed (like processor.py)."""
    start = time.monotonic()
    terminal = {"processed", "failed"}
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= _LINK_POLL_TIMEOUT:
            raise TimeoutError(f"Timed out waiting for LightRAG to process '{file_source}' after {_LINK_POLL_TIMEOUT}s")
        try:
            r = await client.get("/documents", headers=_headers())
            if r.status_code == 200:
                docs = r.json()
                if isinstance(docs, list):
                    doc_list = docs
                elif isinstance(docs, dict) and isinstance(docs.get("statuses"), dict):
                    doc_list = [d for v in docs["statuses"].values() for d in v]
                else:
                    doc_list = docs.get("documents", docs.get("data", [])) if isinstance(docs, dict) else []
                for doc in doc_list:
                    if not isinstance(doc, dict):
                        continue
                    doc_name = doc.get("file_path") or doc.get("filename") or doc.get("name") or ""
                    if doc_name == file_source or doc_name.endswith(file_source):
                        status = str(doc.get("status", "")).lower()
                        logger.info("[MCP Link] Document '%s' status: %s (elapsed=%.1fs)", file_source, status, elapsed)
                        if status in terminal:
                            return status
                        break
                else:
                    logger.warning("[MCP Link] Document '%s' not yet in documents list (elapsed=%.1fs)", file_source, elapsed)
        except Exception as exc:
            logger.warning("[MCP Link] Document list fetch failed: %s", exc)
        await asyncio_sleep(_LINK_POLL_INTERVAL)


async def _link_saved_text_to_graph(file_source: str, text: str = "") -> None:
    """Fire-and-forget: create Note + Date nodes and link extracted entities.

    Best-effort: any failure is logged and swallowed — the text is already
    saved in LightRAG by the time this runs.
    """
    base = _LIGHTRAG_API_URL.rstrip("/")
    timeout = httpx.Timeout(60.0)
    async with httpx.AsyncClient(base_url=base, timeout=timeout) as client:
        try:
            final_status = await _wait_for_lightrag_processing(client, file_source)
        except Exception as exc:
            logger.warning("[MCP Link] '%s' processing wait failed: %s — skipping linking", file_source, exc)
            return
        if final_status == "failed":
            logger.warning("[MCP Link] '%s' processing failed — skipping linking", file_source)
            return

        note_name = f"{file_source} (Note)"
        date_label = ""
        date_taken_friendly = ""
        date_dt = _scan_text_for_date(text) if text else None
        if date_dt is None:
            date_dt = _parse_file_source_date(file_source)
        if date_dt is not None:
            date_label = date_dt.strftime("%Y-%m-%d") + " (Date)"
            date_taken_friendly = date_dt.strftime("%Y-%m-%d at %H:%M")

        note_data = {
            "description": f"Note: {file_source}",
            "entity_type": "Note",
            "source_id": file_source,
        }
        if date_taken_friendly:
            note_data["date_taken_friendly"] = date_taken_friendly

        note_result = await _create_entity_verified(client, note_name, note_data)
        if note_result.get("status") == "error":
            logger.warning("[MCP Link] Note hub '%s' failed — aborting linking: %s", note_name, note_result.get("error"))
            return

        if date_label:
            await _create_entity_verified(
                client,
                date_label,
                {
                    "description": f"Calendar date {date_label[:-len(' (Date)')]}",
                    "entity_type": "Date",
                    "source_id": date_label,
                },
            )
            await _create_relation_verified(
                client,
                note_name,
                date_label,
                {
                    "description": f"Note {file_source} written on {date_label}",
                    "keywords": "written_on",
                    "weight": 1.0,
                },
            )

            # Adjacent-day chaining: only to dates that already exist.
            date_base = date_label[:-len(" (Date)")]
            try:
                this_dt = datetime.strptime(date_base, "%Y-%m-%d")
            except ValueError:
                this_dt = None
            if this_dt is not None:
                for delta in (-1, +1):
                    nb_label = (this_dt + timedelta(days=delta)).strftime("%Y-%m-%d") + " (Date)"
                    try:
                        er = await client.get(
                            "/graph/entity/exists",
                            params={"name": nb_label},
                            headers=_headers(),
                        )
                        exists = bool(er.json().get("exists")) if er.status_code == 200 else False
                    except Exception:
                        exists = False
                    if not exists:
                        continue
                    source, target = (date_label, nb_label) if date_label < nb_label else (nb_label, date_label)
                    await _create_relation_verified(
                        client,
                        source,
                        target,
                        {
                            "description": f"{source[:-len(' (Date)')]} is adjacent to {target[:-len(' (Date)')]}",
                            "keywords": "adjacent_day",
                            "weight": 1.0,
                        },
                    )

        # Link every LLM-extracted entity whose file_path includes this
        # file_source to the Note hub via appears_in.  file_path is joined
        # with <SEP> across sources, so membership test (not equality).
        try:
            lr = await client.get("/graph/label/list", headers=_headers())
            all_labels = lr.json() if lr.status_code == 200 else []
        except Exception as exc:
            logger.warning("[MCP Link] Failed to get graph labels: %s", exc)
            return

        normalized_source = file_source.replace("\u202f", " ").replace("\u00a0", " ")
        linked = 0
        for label in all_labels:
            if label.endswith(_EXIF_SUFFIXES):
                continue
            try:
                gr = await client.get(
                    "/graphs",
                    params={"label": label, "max_depth": 1, "max_nodes": 500},
                    headers=_headers(),
                )
                graph_data = gr.json() if gr.status_code == 200 else {}
            except Exception as exc:
                logger.warning("[MCP Link] Failed to get neighbors for '%s': %s", label, exc)
                continue
            for node in graph_data.get("nodes", []):
                if node.get("id") != label:
                    continue
                props = node.get("properties", {})
                file_path = props.get("file_path", "")
                normalized_path = file_path.replace("\u202f", " ").replace("\u00a0", " ")
                path_parts = [p.strip() for p in normalized_path.split("<SEP>") if p.strip()]
                if normalized_source not in path_parts:
                    continue
                await _create_relation_verified(
                    client,
                    label,
                    note_name,
                    {
                        "description": f"{label} appears in {file_source}",
                        "keywords": "appears_in",
                        "weight": 1.0,
                    },
                )
                linked += 1
                break
        logger.info("[MCP Link] Linking complete for '%s': %d entities linked, date=%s", file_source, linked, date_label)


@mcp.tool()
async def save_to_knowledge_graph(text: str, file_source: str = "") -> str:
    """Save text content into the knowledge graph for indexing. The text will be chunked, entities/relations extracted, and added to the graph. Returns a track ID that can be used to check processing status. file_source is a label identifying the source (e.g. 'chat-note', 'preference-update'). A unique timestamp suffix is appended to avoid 409 conflicts on repeated saves. If omitted, a unique ID is generated. Transient pipeline-busy 409s are retried automatically with backoff."""
    ts = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if not file_source:
        file_source = f"mcp-{ts}"
    else:
        file_source = f"{file_source}-{ts}"

    url = f"{_LIGHTRAG_API_URL}/documents/text"
    payload = {"text": text, "file_source": file_source}
    last_detail = ""
    for attempt in range(_TRANSIENT_409_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(url, headers=_headers(), json=payload)
                if r.status_code == 409 and _is_transient_409(r):
                    last_detail = r.text[:300]
                    sleep = min(_TRANSIENT_409_BASE_SLEEP * (2 ** attempt), 10.0)
                    logger.info(
                        "save_to_knowledge_graph: transient 409 (attempt %d/%d), retrying in %.1fs — %s",
                        attempt + 1, _TRANSIENT_409_MAX_ATTEMPTS, sleep, last_detail,
                    )
                    await asyncio.sleep(sleep)
                    continue
                r.raise_for_status()
                result = r.json()
            # Fire-and-forget: link extracted entities to a Note hub + Date node
            # once LightRAG finishes processing. Never blocks the caller.
            asyncio.create_task(_link_saved_text_to_graph(file_source, text))
            return json.dumps(result, indent=2, default=str)
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", e.response.text)
            except Exception:
                detail = str(e)
            return f"Error saving to knowledge graph (file_source={file_source}): {detail}"
        except Exception as e:
            return f"Error saving to knowledge graph (file_source={file_source}): {e}"
    return (
        f"Error saving to knowledge graph (file_source={file_source}): "
        f"pipeline remained busy after {_TRANSIENT_409_MAX_ATTEMPTS} retries. "
        f"Last detail: {last_detail}"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = _MCP_PORT
    mcp.settings.transport_security.enable_dns_rebinding_protection = False
    logger.info("Starting Knowledge Graph MCP facade on port %d (streamable-http)", _MCP_PORT)
    logger.info("Proxying KG calls to %s", _LIGHTRAG_API_URL)

    app = mcp.streamable_http_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=_MCP_PORT)