#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Whisper Server Watchdog
# ═══════════════════════════════════════════════════════════════════════════════
# Keeps the whisper-server healthy by:
#   1. Pinging /health every KEEPALIVE_INTERVAL seconds (default: 90s)
#      — prevents Metal GPU residency sets from being evicted after 180s idle
#   2. Auto-restarting whisper-server if /health fails consecutively
#      — recovers from the known idle-hang bug (ggml-org/whisper.cpp#1991)
#
# Usage:
#   ./scripts/whisper-watchdog.sh          # foreground (Ctrl+C to stop)
#   ./scripts/whisper-watchdog.sh &        # background
#
# Environment variables (override in .env or export before running):
#   WHISPER_PORT           — port whisper-server listens on (default: 8090)
#   WHISPER_MODEL_PATH     — path to ggml model file
#   WHISPER_WATCHDOG_INTERVAL  — seconds between keepalive pings (default: 90)
#   WHISPER_WATCHDOG_RETRIES   — consecutive failures before restart (default: 3)
#   WHISPER_WATCHDOG_LOG       — log file path (default: /tmp/whisper-watchdog.log)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

WHISPER_PORT="${WHISPER_PORT:-8090}"
WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH:-$PROJECT_DIR/models/whisper/ggml-medium.bin}"
INTERVAL="${WHISPER_WATCHDOG_INTERVAL:-90}"          # ping every 90s (Metal eviction is 180s)
MAX_RETRIES="${WHISPER_WATCHDOG_RETRIES:-3}"          # 3 consecutive failures = restart
LOG="${WHISPER_WATCHDOG_LOG:-/tmp/whisper-watchdog.log}"

WHISPER_SERVER="$(which whisper-server 2>/dev/null || echo /opt/homebrew/bin/whisper-server)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] $*" | tee -a "$LOG"; }

restart_whisper() {
    log "Restarting whisper-server..."

    # Kill existing whisper-server (graceful, then forced)
    local pid
    pid="$(lsof -ti tcp:"$WHISPER_PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$pid" ]]; then
        log "Killing existing whisper-server (PID $pid)"
        kill "$pid" 2>/dev/null || true
        # Wait up to 10s for graceful shutdown
        for _ in $(seq 1 40); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.25
        done
        if kill -0 "$pid" 2>/dev/null; then
            log "whisper-server did not exit; sending SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
            sleep 0.5
        fi
    fi

    if [[ ! -x "$WHISPER_SERVER" ]]; then
        log "ERROR: whisper-server not found at $WHISPER_SERVER"
        return 1
    fi

    if [[ ! -f "$WHISPER_MODEL_PATH" ]]; then
        log "ERROR: Whisper model not found at $WHISPER_MODEL_PATH"
        return 1
    fi

    "$WHISPER_SERVER" \
        -m "$WHISPER_MODEL_PATH" \
        -l en --convert -t 4 \
        --host 0.0.0.0 --port "$WHISPER_PORT" \
        &>/tmp/whisper-server.log &

    # Wait up to 30s for the new server to become healthy
    for _ in $(seq 1 60); do
        if curl -sf "http://localhost:${WHISPER_PORT}/health" &>/dev/null; then
            log "whisper-server is healthy (PID $(lsof -ti tcp:"$WHISPER_PORT" -sTCP:LISTEN 2>/dev/null || echo '?'))"
            return 0
        fi
        sleep 0.5
    done

    log "ERROR: whisper-server failed to start within 30s"
    return 1
}

# ─── Main loop ────────────────────────────────────────────────────────────────
log "Whisper watchdog started (interval=${INTERVAL}s, max_retries=${MAX_RETRIES})"
log "Monitoring http://localhost:${WHISPER_PORT}/health"

consecutive_failures=0

while true; do
    if curl -sf --max-time 5 "http://localhost:${WHISPER_PORT}/health" &>/dev/null; then
        if [[ $consecutive_failures -gt 0 ]]; then
            log "Health check recovered (was failing for ${consecutive_failures} cycle(s))"
        fi
        consecutive_failures=0
    else
        consecutive_failures=$((consecutive_failures + 1))
        log "Health check FAILED ($consecutive_failures/$MAX_RETRIES)"

        if [[ $consecutive_failures -ge $MAX_RETRIES ]]; then
            log "Max retries reached — restarting whisper-server"
            if restart_whisper; then
                consecutive_failures=0
            else
                log "Restart failed — will retry next cycle"
            fi
        fi
    fi

    sleep "$INTERVAL"
done