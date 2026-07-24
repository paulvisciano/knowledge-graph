# Knowledge Graph

Personal knowledge graph stack: LightRAG + local LLMs + custom UI.

## Prerequisites

- **macOS with Apple Silicon** (Metal GPU for llama-server)
- **Homebrew** — install: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- **Docker Desktop** — install: `brew install --cask docker`, then open it. `scripts/start-all.sh` sets the VM memory from `DOCKER_VM_MEMORY_MIB` in `.env` (default 1.5 GiB) automatically; no manual Settings → Resources step needed.
- **llama.cpp** — install: `brew install llama.cpp`. **Must be version 9750+** (commit `bf533823c` or later, from June 2026+) for `gemma4uv` projector support. Older versions will crash when loading the mmproj with `unknown projector type: gemma4uv`. Check with `llama-server --version` and upgrade if needed: `brew upgrade llama.cpp`.
- **whisper.cpp** — install: `brew install whisper-cpp`. Provides `whisper-server`, which runs the transcription model on the Metal GPU on the host (same pattern as llama-server). Required for voice memo transcription; if missing, `start-llama-servers.sh` skips it with a warning and the chat UI disables audio transcription.
- **~11 GB disk space** for model files (includes Whisper)

## Quick Start

```sh
# 1. Clone with submodules (includes patched LightRAG fork)
git clone --recurse-submodules https://github.com/paulvisciano/knowledge-graph.git
cd knowledge-graph

# If you already cloned without --recurse-submodules:
git submodule update --init --recursive

# 2. Configure
cp .env.example .env
# All config in one file — model names, ports, storage backends. No separate LightRAG config needed.

# 3. Download model files
./scripts/download-models.sh
# Pulls all five GGUF/model files into ~/models/ (or $MODEL_DIR if set in .env).
# Re-runnable — skips files that already exist.

# 4. Start the full stack (Docker containers + host llama-servers)
./scripts/start-all.sh
# Starts everything in order:
#   • Ensures Docker Desktop VM memory matches .env (DOCKER_VM_MEMORY_MIB)
#   • Docker containers: lightrag, postgres, api, nexus, mcp
#   • Host llama-servers (LLM + mmproj vision)        :8080
#   • Host llama-servers (BGE-M3 embeddings)          :8081
#   • Host llama-servers (bge-reranker-v2-m3)         :8082
#   • Host whisper-server (ggml-large-v3-turbo)       :8090
# Ctrl+C stops llama-servers; ./scripts/stop-all.sh tears down everything.

# 5. Open Nexus UI
open http://localhost:3000
```

## Model Files

`scripts/download-models.sh` downloads everything automatically. You need five files in `~/models/` (or wherever `MODEL_DIR` in `.env` points):

| Model | File | Size |
|-------|------|------|
| LLM (Gemma 4 12B) | `Gemma-4-12B-OBLITERATED-Q4_K_M.gguf` | ~6.9 GB |
| Vision projection (mmproj) | `mmproj-BF16.gguf` | ~2.3 GB |
| Embedding (BGE-M3) | `bge-m3-Q4_K_M.gguf` | ~1.1 GB |
| Reranker (bge-reranker-v2-m3) | `bge-reranker-v2-m3-Q4_K_M.gguf` | ~418 MB |
| Transcription (Whisper) | `whisper/ggml-large-v3-turbo.bin` | ~1.5 GB |

> **Why mmproj?** Gemma 4 has a vision architecture, but in llama.cpp the vision encoder weights are a separate file. The base GGUF contains only the language model — `mmproj-BF16.gguf` is the vision projection that maps image pixels into the model's embedding space. Without it, `llama-server` cannot process images. The start script passes it via `--mmproj`.
>
> **mmproj compatibility:** The Gemma 4 12B mmproj uses the `gemma4uv` projector type, which requires llama.cpp **v9750+** (June 2026 or later). If your llama.cpp is older, you'll see `unknown projector type: gemma4uv` and the server will fail to start. Fix: `brew upgrade llama.cpp`. If you can't upgrade, move the mmproj aside (`mv mmproj-BF16.gguf mmproj-BF16.gguf.bak`) — the start script will skip `--mmproj` and the API will fall back to EXIF-only image processing (no visual descriptions, but EXIF metadata like location, date, and camera still works).

Search for each model on [HuggingFace](https://huggingface.co/models) and download the `.gguf` file. Directory layout:

```
~/models/
├── gemma4-12b-obliterated/
│   ├── Gemma-4-12B-OBLITERATED-Q4_K_M.gguf
│   └── mmproj-BF16.gguf
├── bge-m3/
│   └── bge-m3-Q4_K_M.gguf
├── bge-reranker-v2-m3/
│   └── bge-reranker-v2-m3-Q4_K_M.gguf
└── whisper/
    └── ggml-large-v3-turbo.bin
```



## Verify

Host llama-servers + whisper-server:

```sh
curl -sf http://localhost:8080/health   # LLM (Gemma 4 12B + mmproj)
curl -sf http://localhost:8081/health   # Embeddings (BGE-M3)
curl -sf http://localhost:8082/health   # Reranker (bge-reranker-v2-m3)
curl -sf http://localhost:8090/health   # Whisper transcription
```

Docker services:

```sh
curl -sf http://localhost:9621/health   # LightRAG
curl -sf http://localhost:8000/health   # Knowledge Graph API
open http://localhost:3000              # Nexus UI
```

## Stop Everything

```sh
./scripts/stop-all.sh
```