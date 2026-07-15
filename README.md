# Knowledge Graph

Personal knowledge graph stack: LightRAG + local LLMs + custom UI.

## Prerequisites

- **macOS with Apple Silicon** (Metal GPU for llama-server)
- **Homebrew** — install: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- **Docker Desktop** — install: `brew install --cask docker`, then open it and allocate at least 8 GB RAM in Settings → Resources
- **llama.cpp** — install: `brew install llama.cpp`
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
cp lightrag/env.example lightrag/.env

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

Search for each model on [HuggingFace](https://huggingface.co/models) and download the `.gguf` file. Directory layout:

```
~/models/
├── gemma4-12b-obliterated/
│   └── Gemma-4-12B-OBLITERATED-Q4_K_M.gguf
├── gemma4-12b-qat/
│   └── mmproj-BF16.gguf
├── nomic-embed-v2/
│   └── nomic-embed-text-v2-moe.Q6_K.gguf
└── bge-reranker-v2-m3/
    └── bge-reranker-v2-m3-Q4_K_M.gguf
```

**Optional:** The MTP draft model (`mtp-gemma-4-12B-it.gguf`) in `~/models/gemma4-12b-qat/` speeds up inference but is not required.

## Verify

```sh
curl -sf http://localhost:9621/health   # LightRAG
curl -sf http://localhost:8000/health   # API backend
```

## Stop Everything

```sh
./scripts/stop-all.sh
```