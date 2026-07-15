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
RERANK_PORT="${RERANKER_PORT:-8082}"

LLM_MODEL_PATH="${LLM_MODEL_PATH:-$MODEL_DIR/gemma4-12b-obliterated/Gemma-4-12B-OBLITERATED-Q4_K_M.gguf}"
LLM_MODEL_ALIAS="${LLM_MODEL_ALIAS:-Gemma-4-12B-OBLITERATED-Q4_K_M}"
EMBED_MODEL_PATH="${EMBED_MODEL_PATH:-$MODEL_DIR/nomic-embed-v2/nomic-embed-text-v2-moe.Q6_K.gguf}"
RERANK_MODEL_PATH="${RERANK_MODEL_PATH:-$MODEL_DIR/bge-reranker-v2-m3/bge-reranker-v2-m3-Q4_K_M.gguf}"
MMPROJ_PATH="${MMPROJ_PATH:-$MODEL_DIR/gemma4-12b-obliterated/mmproj-BF16.gguf}"
for model_path in "$LLM_MODEL_PATH" "$EMBED_MODEL_PATH" "$RERANK_MODEL_PATH"; do
    if [[ ! -f "$model_path" ]]; then
        echo "ERROR: Model file not found: $model_path"
        exit 1
    fi
done

LLAMA_SERVER="$(which llama-server 2>/dev/null || echo /opt/homebrew/bin/llama-server)"
if [[ ! -x "$LLAMA_SERVER" ]]; then
    echo "ERROR: llama-server not found. Install llama.cpp: brew install llama.cpp"
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
    -np 1 -fa on -cram 0 -ngl 99 \
    --reasoning off --ui-mcp-proxy \
    --host 0.0.0.0 --port "$LLM_PORT" \
    &>/tmp/llama-server-llm.log &
PIDS+=($!)

echo "Starting embedding server on port ${EMBED_PORT}..."
"$LLAMA_SERVER" \
    -m "$EMBED_MODEL_PATH" \
    --embedding --pooling cls \
    -c 8192 -b 8192 -ub 2048 \
    --override-kv nomic-bert-moe.context_length=int:8192 \
    -cram 0 -ngl 99 \
    --host 0.0.0.0 --port "$EMBED_PORT" \
    &>/tmp/llama-server-embed.log &
PIDS+=($!)

echo "Starting reranker on port ${RERANK_PORT}..."
"$LLAMA_SERVER" \
    -m "$RERANK_MODEL_PATH" \
    --reranking --embedding --pooling rank \
    -c 512 -b 512 -ub 512 \
    -cram 0 -ngl 99 \
    --host 0.0.0.0 --port "$RERANK_PORT" \
    &>/tmp/llama-server-reranker.log &
PIDS+=($!)

echo ""
echo "Waiting for all endpoints..."
FAIL=0
health_check "$LLM_PORT" 90 || FAIL=$((FAIL+1))
health_check "$EMBED_PORT" 45 || FAIL=$((FAIL+1))
health_check "$RERANK_PORT" 45 || FAIL=$((FAIL+1))

if [[ $FAIL -gt 0 ]]; then
    echo "WARNING: $FAIL endpoint(s) failed. Check /tmp/llama-server-*.log"
fi

echo ""
echo "═══ llama-servers running (PIDs: ${PIDS[*]}) ═══"
echo "LLM:        http://localhost:${LLM_PORT}"
echo "Embeddings: http://localhost:${EMBED_PORT}"
echo "Reranker:   http://localhost:${RERANK_PORT}"
echo ""
echo "Press Ctrl+C to stop all servers."

wait