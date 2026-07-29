#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

MODEL_DIR="${MODEL_DIR:-$PROJECT_DIR/models}"
LLM_PORT="${LLM_PORT:-8080}"
EMBED_PORT="${EMBED_MODEL_PORT:-8081}"
WHISPER_PORT="${WHISPER_PORT:-8090}"

LLM_MODEL_PATH="${LLM_MODEL_PATH:-$MODEL_DIR/bonsai-27b/Bonsai-27B-Q1_0.gguf}"
LLM_MODEL_ALIAS="${LLM_MODEL_ALIAS:-Bonsai-27B-Q1_0}"
EMBED_MODEL_PATH="${EMBED_MODEL_PATH:-$MODEL_DIR/bge-m3/bge-m3-Q4_K_M.gguf}"
MMPROJ_PATH="${MMPROJ_PATH:-$MODEL_DIR/bonsai-27b/Bonsai-27B-mmproj-Q8_0.gguf}"
WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH:-$MODEL_DIR/whisper/ggml-medium.bin}"

# LLM sampler settings — anti-repetition (fixes verbatim phrase-loop degeneration at Q1 quant).
# DRY sampler targets phrase-level repetition; repeat_penalty is a token-level backstop.
# XTC disrupts repetitive token selection on heavily-quantized models (stopgap for Q1).
LLM_REPEAT_PENALTY="${LLM_REPEAT_PENALTY:-1.1}"
LLM_REPEAT_LAST_N="${LLM_REPEAT_LAST_N:-128}"
LLM_DRY_MULTIPLIER="${LLM_DRY_MULTIPLIER:-0.5}"
LLM_DRY_BASE="${LLM_DRY_BASE:-1.75}"
LLM_DRY_ALLOWED_LENGTH="${LLM_DRY_ALLOWED_LENGTH:-2}"
LLM_XTC_PROBABILITY="${LLM_XTC_PROBABILITY:-0.1}"
LLM_XTC_THRESHOLD="${LLM_XTC_THRESHOLD:-0.1}"
# Single slot gets the full context window (set LLM_SLOTS=2 to split for concurrency).
LLM_SLOTS="${LLM_SLOTS:-1}"
for model_path in "$LLM_MODEL_PATH" "$EMBED_MODEL_PATH"; do
    if [[ ! -f "$model_path" ]]; then
        echo "ERROR: Model file not found: $model_path"
        exit 1
    fi
done
if [[ ! -f "$WHISPER_MODEL_PATH" ]]; then
    echo "WARNING: Whisper model not found: $WHISPER_MODEL_PATH (transcription will be unavailable)"
fi

# Prefer a Metal-enabled build from vendor/llama.cpp (built via build-llama-cpp.sh).
# The Homebrew bottle of llama.cpp ships WITHOUT Metal support, so -ngl 99
# is silently ignored and the model runs on CPU at 4-7 tok/s instead of 20-40.
# If the project build exists, use it; otherwise fall back to Homebrew.
PROJECT_LLAMA_SERVER="$PROJECT_DIR/vendor/llama.cpp/src/build/bin/llama-server"
HOMEBREW_LLAMA_SERVER="$(which llama-server 2>/dev/null || echo /opt/homebrew/bin/llama-server)"

if [[ -x "$PROJECT_LLAMA_SERVER" ]]; then
    LLAMA_SERVER="$PROJECT_LLAMA_SERVER"
    export DYLD_LIBRARY_PATH="$PROJECT_DIR/vendor/llama.cpp/src/build/bin:${DYLD_LIBRARY_PATH:-}"
    echo "Using project-built llama-server (Metal): $LLAMA_SERVER"
elif [[ -x "$HOMEBREW_LLAMA_SERVER" ]]; then
    LLAMA_SERVER="$HOMEBREW_LLAMA_SERVER"
    echo "WARNING: Using Homebrew llama-server — Metal may not be available."
    echo "         Run ./scripts/build-llama-cpp.sh for GPU acceleration."
else
    echo "ERROR: llama-server not found. Install via: ./scripts/build-llama-cpp.sh"
    echo "       Or fallback: brew install llama.cpp (CPU-only, not recommended)"
    exit 1
fi

MMPROJ_FLAG=""
[[ -f "$MMPROJ_PATH" ]] && MMPROJ_FLAG="--mmproj $MMPROJ_PATH"

health_check() {
    local port=$1
    for i in $(seq 1 "${2:-60}"); do
        if curl -sf "http://localhost:${port}/health" &>/dev/null; then
            echo "  ✓ Port ${port} healthy"
            return 0
        fi
        sleep 1
    done
    echo "  ✗ Port ${port} failed to start"
    return 1
}

PIDS=()
cleanup() {
    echo "Stopping llama-servers..."
    kill "${PIDS[@]}" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "═══ Knowledge Graph — Starting llama-servers (Metal GPU) ═══"
echo ""

echo "Starting LLM on port ${LLM_PORT}..."
"$LLAMA_SERVER" \
    -m "$LLM_MODEL_PATH" \
    --alias "$LLM_MODEL_ALIAS" \
    $MMPROJ_FLAG \
    --image-max-tokens 280 --image-min-tokens 40 \
    -c 32768 -b 2048 -ub 2048 \
    -ctk q4_0 -ctv q4_0 \
    -np "$LLM_SLOTS" -fa on -cram 0 -ngl 99 \
    --repeat-penalty "$LLM_REPEAT_PENALTY" --repeat-last-n "$LLM_REPEAT_LAST_N" \
    --dry-multiplier "$LLM_DRY_MULTIPLIER" --dry-base "$LLM_DRY_BASE" --dry-allowed-length "$LLM_DRY_ALLOWED_LENGTH" \
    --xtc-probability "$LLM_XTC_PROBABILITY" --xtc-threshold "$LLM_XTC_THRESHOLD" \
    --reasoning on --reasoning-budget 1024 --ui-mcp-proxy \
    --host 0.0.0.0 --port "$LLM_PORT" \
    &>/tmp/llama-server-llm.log &
PIDS+=($!)

echo "Starting embedding server on port ${EMBED_PORT}..."
"$LLAMA_SERVER" \
    -m "$EMBED_MODEL_PATH" \
    --embedding --pooling cls \
    -c 8192 -b 8192 -ub 2048 \
    -cram 0 -ngl 99 \
    --host 0.0.0.0 --port "$EMBED_PORT" \
    &>/tmp/llama-server-embed.log &
PIDS+=($!)

if [[ -f "$WHISPER_MODEL_PATH" ]]; then
    WHISPER_SERVER="$(which whisper-server 2>/dev/null || echo /opt/homebrew/bin/whisper-server)"
    if [[ -x "$WHISPER_SERVER" ]]; then
        # Re-running this script must not stack a second whisper-server on the same port.
        if existing_pid="$(lsof -ti tcp:"$WHISPER_PORT" -sTCP:LISTEN 2>/dev/null)" && [[ -n "$existing_pid" ]]; then
            echo "Found existing whisper-server (PID $existing_pid) on port ${WHISPER_PORT}; killing it before relaunch."
            kill "$existing_pid" 2>/dev/null || true
            for _ in $(seq 1 20); do
                lsof -ti tcp:"$WHISPER_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
                sleep 0.25
            done
            if lsof -ti tcp:"$WHISPER_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
                echo "whisper-server (PID $existing_pid) did not exit; sending SIGKILL."
                kill -9 "$existing_pid" 2>/dev/null || true
                sleep 0.5
            fi
        fi
        echo "Starting whisper transcription server on port ${WHISPER_PORT}..."
        # Pin language to English. With -l auto the turbo model misclassifies
        # short English clips as Afrikaans (p = nan), yielding empty transcripts.
        # The app is English-only; lift this to a per-request param if multilingual
        # support is ever needed.
        "$WHISPER_SERVER" \
            -m "$WHISPER_MODEL_PATH" \
            -l en --convert -t 4 \
            --host 0.0.0.0 --port "$WHISPER_PORT" \
            &>/tmp/whisper-server.log &
        PIDS+=($!)
    else
        echo "WARNING: whisper-server not found, skipping transcription server"
    fi
fi

echo ""
echo "Waiting for all endpoints..."
FAIL=0
health_check "$LLM_PORT" 90 || FAIL=$((FAIL+1))
health_check "$EMBED_PORT" 45 || FAIL=$((FAIL+1))
if [[ -f "$WHISPER_MODEL_PATH" ]] && [[ -x "${WHISPER_SERVER:-/opt/homebrew/bin/whisper-server}" ]]; then
    health_check "$WHISPER_PORT" 30 || FAIL=$((FAIL+1))
fi

if [[ $FAIL -gt 0 ]]; then
    echo "WARNING: $FAIL endpoint(s) failed. Check /tmp/llama-server-*.log and /tmp/whisper-server.log"
fi

echo ""
echo "═══ llama-servers running (PIDs: ${PIDS[*]}) ═══"
echo "LLM:        http://localhost:${LLM_PORT}"
echo "Embeddings: http://localhost:${EMBED_PORT}"
echo "Whisper:    http://localhost:${WHISPER_PORT}"
echo ""
echo "Press Ctrl+C to stop all servers."

wait