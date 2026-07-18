"""Knowledge Graph MCP facade — 2 curated KG tools calling LightRAG REST directly."""

from __future__ import annotations

import json
import logging
import os

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


mcp = FastMCP(
    "KnowledgeGraph",
    stateless_http=True,
    json_response=True,
    instructions=(
        "You are Paul's personal assistant. You have access to a local knowledge graph.\n"
        "\n"
        "## Knowledge Graph\n"
        "The knowledge graph stores Paul's personal information: preferences, people he knows, "
        "places he's been, documents, notes, playlists, and VLM-analyzed photo descriptions "
        "(people in photos, locations, activities). Text content covers preferences and facts; "
        "photo content covers events and people.\n"
        "\n"
        "### When to query\n"
        "ALWAYS query the knowledge graph when the user asks about Paul, his preferences, "
        "his relationships, his activities, his photos, or any personal information that might "
        "be stored there. Do NOT say 'I don't have that information' without querying first.\n"
        "\n"
        "Do NOT query for general knowledge questions (e.g. 'How tall is the Eiffel Tower?'). "
        "Only query for information specific to Paul's life and stored content.\n"
        "\n"
        "### Query modes\n"
        "- mode='local' (default): Use for most queries. Returns focused, relevant entities. Always pass top_k=5.\n"
        "- mode='global': Use ONLY for broad overviews of how entities relate across the entire graph.\n"
        "- AVOID mode='hybrid' and mode='mix' — they return noisy, irrelevant results.\n"
        "\n"
        "### Saving information\n"
        "When the user shares personal information (a preference, fact, or note), proactively offer "
        "to save it using insert_text. Always provide a descriptive file_source label, e.g. "
        "file_source='chat-note', file_source='preference-update', file_source='correction'.\n"
        "\n"
        "### Correcting information\n"
        "When the user corrects something in the knowledge graph ('that's wrong', 'actually, X is Y'), "
        "use insert_text with file_source='correction' to add the corrected information. The new text "
        "will be indexed and update the graph accordingly.\n"
        "\n"
        "### No results\n"
        "If query_knowledge_graph returns 'No results found', say so for the KG data only. "
        "You can still share relevant general knowledge — just make clear it's not from Paul's records. "
        "Suggest rephrasing the query or trying mode='global' for a broader search.\n"
        "\n"
        "### Integrating knowledge\n"
        "When you receive knowledge graph results, you MUST enrich them with your own knowledge. "
        "Do NOT just summarize the raw KG data — add context, explanations, and connections that only you can provide.\n"
        "\n"
        "- Enrich: if the KG says David is your brother, explain what that relationship involves. "
        "If a photo places him in Miami Beach, add that Miami Beach is known for Art Deco architecture and beachfront culture.\n"
        "- Fill gaps: if the KG says 'The Betsy Hotel' is in Miami Beach with Mediterranean architecture, "
        "add that this is in the historic Art Deco District of South Beach. If the KG mentions 'Betsy's Kitchen,' "
        "note that this is the hotel's restaurant.\n"
        "- Interpret: raw KG entities and relationships need synthesis. Don't list them — explain what they mean together.\n"
        "\n"
        "Always be transparent about your sources:\n"
        "- Facts from Paul's records: \"Based on your records…\" or \"Your notes indicate…\"\n"
        "- Your own knowledge: \"I'd note that…\" or \"Generally, …\" or \"In fact, …\"\n"
        "- Inferences combining both: \"Your records show X, which typically means Y.\"\n"
        "\n"
        "## Style\n"
        "Be direct and informative. When presenting knowledge graph results, enrich them with your own "
        "knowledge — do not just paraphrase the raw data. Always distinguish what comes from Paul's "
        "records versus your own knowledge."
    ),
)


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg", ".raw-01", ".ts-000-01"}


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


@mcp.tool()
async def query_knowledge_graph(
    query: str, mode: str = "local", only_need_context: bool = True, top_k: int = 5
) -> str:
    """Search the knowledge graph for information relevant to a query. Use mode='local' (default) for most queries — returns focused, relevant entities. Use mode='global' only for broad overviews. Avoid 'hybrid' and 'mix' — they pull in noisy results."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_LIGHTRAG_API_URL}/query",
                headers=_headers(),
                json={
                    "query": query,
                    "mode": mode,
                    "only_need_context": only_need_context,
                    "top_k": top_k,
                    "include_references": True,
                },
            )
            r.raise_for_status()
            result = r.json()
        response = result.get("response", "")

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


@mcp.tool()
async def insert_text(text: str, file_source: str = "") -> str:
    """Insert text content into the knowledge graph for indexing. The text will be chunked, entities/relations extracted, and added to the graph. Returns a track ID that can be used to check processing status. file_source is a label identifying the source (e.g. 'chat-note', 'preference-update'). If omitted, a unique ID is generated."""
    try:
        if not file_source:
            from datetime import datetime

            file_source = f"mcp-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_LIGHTRAG_API_URL}/documents/text",
                headers=_headers(),
                json={"text": text, "file_source": file_source},
            )
            r.raise_for_status()
            result = r.json()
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error inserting text: {e}"


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