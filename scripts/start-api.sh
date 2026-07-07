#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

API_PORT="${API_PORT:-8000}"
API_HOST="${API_HOST:-0.0.0.0}"

echo "Starting Knowledge Graph API backend on port ${API_PORT}..."
echo ""

cd "$PROJECT_DIR"

PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Virtual environment not found at $PYTHON"
    echo "Create it with: /opt/homebrew/bin/python3.11 -m venv .venv && .venv/bin/pip install -r api/requirements.txt"
    exit 1
fi

PYTHONPATH="$PROJECT_DIR" "$PYTHON" -m uvicorn api.main:app \
    --host "$API_HOST" \
    --port "$API_PORT" \
    --no-access-log