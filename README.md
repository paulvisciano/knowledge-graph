# Knowledge Graph

Personal knowledge graph stack: LightRAG + local LLMs + custom UI.

## Prerequisites

- **macOS with Apple Silicon** (Metal GPU for llama-server)
- **Homebrew** — install: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- **Docker Desktop** — install: `brew install --cask docker`, then open it and allocate at least 8 GB RAM in Settings → Resources
- **llama.cpp** — install: `brew install llama.cpp`. **Must be version 9750+** (commit `bf533823c` or later, from June 2026+) for `gemma4uv` projector support. Older versions will crash when loading the mmproj with `unknown projector type: gemma4uv`. Check with `llama-server --version` and upgrade if needed: `brew upgrade llama.cpp`.
- **~10 GB disk space** for model files

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

# 3. Download model files (see "Model Files" below)

# 4. Start llama-servers on host (Metal GPU)
./scripts/start-llama-servers.sh

# 5. Start Docker services
docker compose up -d --build

# 6. Open Nexus UI
open http://localhost:3000
```

## Model Files

You need four GGUF files in `~/models/` (or set `MODEL_DIR` in `.env`):

| Model | File | Size |
|-------|------|------|
| LLM (Gemma 4 12B) | `Gemma-4-12B-OBLITERATED-Q4_K_M.gguf` | ~6.9 GB |
| Vision projection (mmproj) | `mmproj-BF16.gguf` | ~2.3 GB |
| Embedding (nomic-embed-v2) | `nomic-embed-text-v2-moe.Q6_K.gguf` | ~379 MB |
| Reranker (bge-reranker-v2-m3) | `bge-reranker-v2-m3-Q4_K_M.gguf` | ~418 MB |

> **Why mmproj?** Gemma 4 has a vision architecture, but in llama.cpp the vision encoder weights are a separate file. The base GGUF contains only the language model — `mmproj-BF16.gguf` is the vision projection that maps image pixels into the model's embedding space. Without it, `llama-server` cannot process images. The start script passes it via `--mmproj`.
>
> **mmproj compatibility:** The Gemma 4 12B mmproj uses the `gemma4uv` projector type, which requires llama.cpp **v9750+** (June 2026 or later). If your llama.cpp is older, you'll see `unknown projector type: gemma4uv` and the server will fail to start. Fix: `brew upgrade llama.cpp`. If you can't upgrade, move the mmproj aside (`mv mmproj-BF16.gguf mmproj-BF16.gguf.bak`) — the start script will skip `--mmproj` and the API will fall back to EXIF-only image processing (no visual descriptions, but EXIF metadata like location, date, and camera still works).

Search for each model on [HuggingFace](https://huggingface.co/models) and download the `.gguf` file. Directory layout:

```
~/models/
├── gemma4-12b-obliterated/
│   ├── Gemma-4-12B-OBLITERATED-Q4_K_M.gguf
│   └── mmproj-BF16.gguf
├── nomic-embed-v2/
│   └── nomic-embed-text-v2-moe.Q6_K.gguf
└── bge-reranker-v2-m3/
    └── bge-reranker-v2-m3-Q4_K_M.gguf
```



## Verify

```sh
curl -sf http://localhost:9621/health   # LightRAG
curl -sf http://localhost:8000/health   # API backend
```

## Stop Everything

```sh
./scripts/stop-all.sh
```