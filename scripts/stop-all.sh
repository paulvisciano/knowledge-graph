#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Stopping API backend..."
pkill -f "uvicorn api.main:app" 2>/dev/null || echo "No API backend running"

echo "Stopping llama-servers..."
pkill -f "llama-server.*--port" 2>/dev/null || echo "No llama-server processes found"

echo "Stopping Docker services..."
docker compose -f "$PROJECT_DIR/docker-compose.yml" down 2>/dev/null || echo "No Docker services running"

echo "Done."