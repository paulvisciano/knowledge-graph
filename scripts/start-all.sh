#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph — Start the full stack (Docker containers + host llama-servers)
# ═══════════════════════════════════════════════════════════════════════════════
# Brings up the entire system in the correct order:
#   1. Ensure Docker Desktop VM memory matches .env (scripts/setup-docker.sh)
#   2. Start Docker containers (lightrag, postgres, api, nexus, mcp)
#   3. Smoke-test nexus UI (scripts/health-check.sh) — aborts if :3000 is broken
#   4. Start host llama-servers (LLM, embeddings, reranker, whisper)
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

# 3. Wait for nexus to serve the UI, then hand off to llama-servers.
echo "── Smoke test (scripts/health-check.sh) ──"
if bash "$SCRIPT_DIR/health-check.sh"; then
    echo ""
else
    rc=$?
    echo "ERROR: nexus UI not responding — stack is up but :3000 is broken." >&2
    echo "       Run ./scripts/health-check.sh for details, or ./scripts/stop-all.sh to tear down." >&2
    exit $rc
fi

# 4. Wait for LightRAG and the API to be healthy before starting llama-servers
#    (containers depend on host llama-servers, but we start containers first so
#    their healthchecks can proceed as soon as llama-servers come up in step 4)
#    Interactive TTY: exec into start-llama-servers.sh so Ctrl+C stops servers.
#    Non-interactive: run it inside a named tmux session (kg-llama) so it
#    survives the parent shell AND stays attachable for live logs:
#      tmux attach -t kg-llama    # Ctrl+B then D to detach again
#    stop-all.sh tears the servers down regardless of how they were launched.
LLAMA_TMUX_SESSION="${LLAMA_TMUX_SESSION:-kg-llama}"
echo "── Handing off to llama-servers ──"
echo "    Containers are up; llama-servers will serve LLM/embed/rerank/whisper."
if [[ -t 0 ]]; then
    echo "    Press Ctrl+C to stop llama-servers."
    echo "    Run ./scripts/stop-all.sh to tear down everything."
    echo ""
    exec "$SCRIPT_DIR/start-llama-servers.sh"
else
    echo "    (non-interactive: launching llama-servers in tmux session '$LLAMA_TMUX_SESSION')"
    echo "    Live logs:   tmux attach -t $LLAMA_TMUX_SESSION   (Ctrl+B then D to detach)"
    echo "    Run ./scripts/stop-all.sh to tear down everything."
    echo ""
    # Kill any stale session with the same name, then start a fresh detached one.
    tmux kill-session -t "$LLAMA_TMUX_SESSION" 2>/dev/null || true
    tmux new-session -d -s "$LLAMA_TMUX_SESSION" -x "$(tput cols 2>/dev/null || echo 200)" -y 50 \
        "bash $SCRIPT_DIR/start-llama-servers.sh; echo '(start-llama-servers exited with status $?)'; sleep 86400"
    # Wait for all llama-server endpoints to be healthy, then exit. We poll the
    # ports directly (not tmux pane scraping) because tmux's default pane has no
    # scrollback, so "llama-servers running" scrolls off and becomes invisible.
    LLAMA_PORT="${LLAMA_PORT:-8080}"
    EMBED_PORT="${EMBED_MODEL_PORT:-8081}"
    RERANK_PORT="${RERANKER_PORT:-8082}"
    WHISPER_PORT="${WHISPER_PORT:-8090}"
    all_ready() {
        for p in "$LLAMA_PORT" "$EMBED_PORT" "$RERANK_PORT" "$WHISPER_PORT"; do
            curl -sf "http://localhost:${p}/health" >/dev/null 2>&1 || return 1
        done
        return 0
    }
    for _ in $(seq 1 120); do
        if all_ready; then
            echo "    ✓ LLM :${LLAMA_PORT}  embed :${EMBED_PORT}  rerank :${RERANK_PORT}  whisper :${WHISPER_PORT}"
            exit 0
        fi
        # Surface whatever the script has printed so far for visibility.
        tmux capture-pane -p -t "$LLAMA_TMUX_SESSION" -S - 2>/dev/null | grep -E "Starting|✓ Port|✗ Port|endpoint" | tail -5
        sleep 5
    done
    echo "WARNING: llama-servers did not report ready within 240s." >&2
    tmux capture-pane -p -t "$LLAMA_TMUX_SESSION" -S - 2>/dev/null | tail -20 >&2
    exit 1
fi