"""Knowledge Graph MCP — facade server exposing 4 curated KG tools from LightRAG REST.

Transport: streamable-http (compatible with llama.cpp WebUI and SvelteKit proxy).
"""
from knowledge_graph_mcp.server import main

__all__ = ["main"]