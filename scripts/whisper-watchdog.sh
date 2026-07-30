#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Whisper Server Watchdog
# ═══════════════════════════════════════════════════════════════════════════════
# Keeps the whisper-server healthy by:
#   1. Sending a tiny inference request every KEEPALIVE_INTERVAL seconds
#      — forces Metal GPU to touch compute buffers, preventing residency-set
#        eviction after 180s idle (ggml-org/whisper.cpp#1991).
#      — A bare /health ping is NOT enough: macOS evicts Metal residency sets
#        when no GPU compute commands have been submitted for 180s. The HTTP
#        handler in whisper-server is CPU-only and never touches the GPU, so
#        /health returns 200 while the GPU silently loses its buffers. The next
#        real inference then hangs or returns empty/garbage text.
#   2. Auto-restarting whisper-server if inference fails consecutively
#      — recovers from hang, crash, or GPU eviction that inference alone
#        couldn't recover from.
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

# ─── Generate a minimal WAV for keepalive inference ───────────────────────────
# 1 second of silence at 16kHz mono 16-bit. Small enough that inference is fast
# (~0.5s on medium model), but forces the GPU to run the full encode+decode
# pipeline, keeping Metal residency sets warm.
KEEPALIVE_WAV="/tmp/whisper-watchdog-keepalive.wav"
generate_keepalive_wav() {
    # 44 bytes of WAV header + 32000 bytes of silence (1s @ 16kHz, 16-bit, mono)
    python3 -c "
import struct, sys
sr, ch, bw = 16000, 1, 2
n = sr * ch * bw  # 32000 bytes of data
sys.stdout.buffer.write(b'RIFF')
sys.stdout.buffer.write(struct.pack('<I', 36 + n))
sys.stdout.buffer.write(b'WAVEfmt ')
sys.stdout.buffer.write(struct.pack('<IHHIIHH', 16, 1, ch, sr, sr*ch*bw, ch*bw, bw*8))
sys.stdout.buffer.write(b'data')
sys.stdout.buffer.write(struct.pack('<I', n))
sys.stdout.buffer.write(b'\x00' * n)
" > "$KEEPALIVE_WAV"
}

generate_keepalive_wav

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
log "Keeping warm with inference on http://localhost:${WHISPER_PORT}/inference"

consecutive_failures=0

while true; do
    # Send a real inference request to keep Metal residency sets warm.
    # /health only touches the CPU — the GPU eviction timer keeps ticking.
    # A 1s silence WAV runs the full encode+decode on the GPU in ~0.5s.
    inference_response="$(curl -sf --max-time 15 \
        -F "file=@${KEEPALIVE_WAV}" \
        -F "temperature=0.0" \
        -F "response_format=json" \
        "http://localhost:${WHISPER_PORT}/inference" 2>/dev/null || echo "__CURL_FAILED__")"

    if [[ "$inference_response" != "__CURL_FAILED__" ]]; then
        if [[ $consecutive_failures -gt 0 ]]; then
            log "Inference recovered (was failing for ${consecutive_failures} cycle(s))"
        fi
        consecutive_failures=0
    else
        consecutive_failures=$((consecutive_failures + 1))
        log "Inference FAILED ($consecutive_failures/$MAX_RETRIES)"

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