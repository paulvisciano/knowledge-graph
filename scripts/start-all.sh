#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph — Start the full stack (Docker containers + host llama-servers)
# ═══════════════════════════════════════════════════════════════════════════════
# Brings up the entire system in the correct order:
#   1. Ensure Docker Desktop VM memory matches .env (scripts/setup-docker.sh)
#   2. Start Docker containers (lightrag, postgres, api, nexus, mcp)
#   3. Start host llama-servers (LLM, embeddings, reranker, whisper) in foreground
#
# The llama-servers run in the foreground so Ctrl+C stops them cleanly.
# To stop everything (containers + servers): ./scripts/stop-all.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

echo "═══ Knowledge Graph — starting full stack ═══"
echo ""

# 1. Ensure Docker Desktop VM memory is configured
echo "── Checking Docker Desktop VM memory ──"
bash "$SCRIPT_DIR/setup-docker.sh"
echo ""

# 2. Start Docker containers (non-blocking — they run detached)
echo "── Starting Docker containers ──"
docker compose -f "$COMPOSE_FILE" up -d --build
echo ""

# 3. Wait for LightRAG and the API to be healthy before starting llama-servers
#    (containers depend on host llama-servers, but we start containers first so
#    their healthchecks can proceed as soon as llama-servers come up in step 4)
echo "── Handing off to llama-servers (foreground) ──"
echo "    Containers are up; llama-servers will serve LLM/embed/rerank/whisper."
echo "    Press Ctrl+C to stop llama-servers."
echo "    Run ./scripts/stop-all.sh to tear down everything."
echo ""
exec "$SCRIPT_DIR/start-llama-servers.sh"