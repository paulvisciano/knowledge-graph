# Knowledge Graph

Personal knowledge graph stack: LightRAG + local LLMs + custom UI.

## Prerequisites

- **macOS with Apple Silicon** (Metal GPU for llama-server)
- **Homebrew** — install: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- **Docker Desktop** — install: `brew install --cask docker`, then open it and allocate at least 8 GB RAM in Settings → Resources
- **llama.cpp** — install: `brew install llama.cpp`
- **~8 GB disk space** for model files

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

You need three GGUF model files in `~/models/` (or set `MODEL_DIR` in `.env`):

| Model | File | Size |
|-------|------|------|
| LLM (Gemma 4 12B) | `Gemma-4-12B-OBLITERATED-Q4_K_M.gguf` | ~6.9 GB |
| Embedding (nomic-embed-v2) | `nomic-embed-text-v2-moe.Q6_K.gguf` | ~379 MB |
| Reranker (bge-reranker-v2-m3) | `bge-reranker-v2-m3-Q4_K_M.gguf` | ~418 MB |

Search for each model on [HuggingFace](https://huggingface.co/models) and download the `.gguf` file. Directory layout:

```
~/models/
├── gemma4-12b-obliterated/
│   └── Gemma-4-12B-OBLITERATED-Q4_K_M.gguf
├── nomic-embed-v2/
│   └── nomic-embed-text-v2-moe.Q6_K.gguf
└── bge-reranker-v2-m3/
    └── bge-reranker-v2-m3-Q4_K_M.gguf
```

**Optional:** For VLM (vision) support, also download `mmproj-BF16.gguf` to `~/models/gemma4-12b-qat/`.

## Verify

```sh
curl -sf http://localhost:9621/health   # LightRAG
curl -sf http://localhost:8000/health   # API backend
```

## Stop Everything

```sh
./scripts/stop-all.sh
```