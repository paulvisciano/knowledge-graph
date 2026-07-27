#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

MODEL_DIR="${MODEL_DIR:-$PROJECT_DIR/models}"
mkdir -p "$MODEL_DIR"

download() {
    local url="$1"
    local dest="$2"
    local label="$3"
    if [[ -f "$dest" ]]; then
        echo "  ✓ $label (already exists)"
        return 0
    fi
    echo "  ↓ $label"
    mkdir -p "$(dirname "$dest")"
    curl -L --fail --progress-bar -o "$dest" "$url"
    echo "  ✓ $label ($(du -h "$dest" | cut -f1))"
}

echo "═══ Downloading models to $MODEL_DIR ═══"
echo ""

# ─── LLM: Bonsai-27B (1-bit Q1_0, Prism ML) ───────────────────────────────────
# Requires the PrismML-Eng llama.cpp fork — run ./scripts/build-llama-cpp.sh.
BONSAI_DIR="$MODEL_DIR/bonsai-27b"
download \
    "https://huggingface.co/prism-ml/Bonsai-27B-gguf/resolve/main/Bonsai-27B-Q1_0.gguf" \
    "$BONSAI_DIR/Bonsai-27B-Q1_0.gguf" \
    "Bonsai-27B Q1_0 (1-bit, 3.8 GB)"
download \
    "https://huggingface.co/prism-ml/Bonsai-27B-gguf/resolve/main/Bonsai-27B-mmproj-Q8_0.gguf" \
    "$BONSAI_DIR/Bonsai-27B-mmproj-Q8_0.gguf" \
    "Bonsai-27B mmproj Q8_0 (vision)"

# ─── Embedding: BGE-M3 ─────────────────────────────────────────────────────────
download \
    "https://huggingface.co/gaianet/bge-m3-GGUF/resolve/main/bge-m3-Q4_K_M.gguf" \
    "$MODEL_DIR/bge-m3/bge-m3-Q4_K_M.gguf" \
    "BGE-M3 Q4_K_M (embeddings)"

# ─── Reranker: bge-reranker-v2-m3 ───────────────────────────────────────────────
download \
    "https://huggingface.co/gaianet/bge-reranker-v2-m3-GGUF/resolve/main/bge-reranker-v2-m3-Q4_K_M.gguf" \
    "$MODEL_DIR/bge-reranker-v2-m3/bge-reranker-v2-m3-Q4_K_M.gguf" \
    "BGE-Reranker-v2-m3 Q4_K_M"

# ─── Whisper: ggml-large-v3-turbo (multilingual transcription) ──────────────────
download \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin" \
    "$MODEL_DIR/whisper/ggml-large-v3-turbo.bin" \
    "Whisper ggml-large-v3-turbo (multilingual transcription)"

echo ""
echo "═══ All models downloaded to $MODEL_DIR ═══"