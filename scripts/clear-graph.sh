#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Clear all graph data so images can be reprocessed from scratch.
#
# Wipes:
#   - LightRAG graph: entities, relations, chunks, vector stores, doc status
#     (via DELETE /documents on the lightrag service)
#   - API job history: jobs + job_events tables in PostgreSQL
#   - Face recognition state: face_mapping.json + per-face crops in face_crops vol
#
# Preserves:
#   - known_faces/ reference embeddings (so you don't re-label people)
#   - ./inputs/ source images on the host (so you can reprocess the same set)
#   - conversations / messages (Nexus chat history)
#
# Prerequisites:
#   - Stack running: `docker compose up -d`
#   - llama-servers running on the host (ports 8080/8081/8082)
#
# Usage:
#   bash scripts/clear-graph.sh           # clear and keep stack running
#   bash scripts/clear-graph.sh --down   # also stop the stack afterwards
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

LIGHTRAG_PORT="${LIGHTRAG_PORT:-9621}"
LIGHTRAG_URL="http://localhost:${LIGHTRAG_PORT}"
POSTGRES_USER="${POSTGRES_USER:-lightrag}"
POSTGRES_DB="${POSTGRES_DATABASE:-lightrag}"
STOP_AFTER=false
if [[ "${1:-}" == "--down" ]]; then
  STOP_AFTER=true
fi

# ─── Preflight ────────────────────────────────────────────────────────────────
if ! docker compose ps --services --filter "status=running" 2>/dev/null | grep -q lightrag; then
  echo "ERROR: lightrag service is not running. Start the stack first:"
  echo "  docker compose up -d"
  exit 1
fi

echo "This will PERMANENTLY delete all graph data, job history, and face crops."
echo "known_faces/ embeddings and ./inputs/ images will be preserved."
read -r -p "Proceed? [y/N] " response
if [[ ! "$response" =~ ^[yY]$ ]]; then
  echo "Aborted."
  exit 0
fi

# ─── 1. LightRAG graph (entities, relations, chunks, VDB, doc_status) ────────
echo ""
echo "→ Clearing LightRAG graph (DELETE /documents)..."
RESPONSE=$(curl -s -o /tmp/lightrag_clear_body -w "%{http_code}" \
  -X DELETE "${LIGHTRAG_URL}/documents" \
  --max-time 120) || true
BODY=$(cat /tmp/lightrag_clear_body 2>/dev/null || echo "")
if [[ "$RESPONSE" == "200" ]]; then
  echo "  ✓ LightRAG cleared: $BODY"
else
  echo "  ✗ LightRAG clear failed (HTTP $RESPONSE): $BODY"
  echo "    If the pipeline is busy, wait for it to finish and re-run this script."
  exit 1
fi

# ─── 2. API job history (jobs + job_events) ──────────────────────────────────
echo ""
echo "→ Clearing API job history (jobs, job_events)..."
docker compose exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "TRUNCATE jobs, job_events RESTART IDENTITY CASCADE;" >/dev/null
echo "  ✓ Job history cleared"

# ─── 3. Face recognition state (face_mapping.json + crops) ───────────────────
echo ""
echo "→ Clearing face crops and face_mapping.json..."
# The face_crops named volume is mounted at /app/face_crops in the api container.
docker compose exec -T api sh -c \
  'rm -rf /app/face_crops/* /app/face_crops/.[!.]* 2>/dev/null || true'
echo "  ✓ Face crops cleared"

# ─── Done ────────────────────────────────────────────────────────────────────
echo ""
echo "All graph data cleared. To reprocess:"
echo "  bash scripts/reprocess_faces.sh   # faces only (skip EXIF)"
echo "  # or re-upload images via the Nexus UI to run the full pipeline"
echo "  # (EXIF + faces + VLM + LightRAG insert)"

if $STOP_AFTER; then
  echo ""
  echo "→ Stopping stack (--down)..."
  bash "$SCRIPT_DIR/stop-all.sh" || true
fi