# Knowledge Graph

Personal knowledge graph stack: LightRAG + local LLMs + custom UI.

## Quick Start

```sh
# 1. Clone with submodules
git clone --recurse-submodules https://github.com/paulvisciano/knowledge-graph.git
cd knowledge-graph

# 2. Configure
cp .env.example .env
cp lightrag/env.example lightrag/.env

# 3. Start llama-servers on host (Metal GPU)
./scripts/start-llama-servers.sh

# 4. Start Docker services
docker compose up -d --build

# 5. Open Nexus UI
open http://localhost:3000
```

## Stop Everything

```sh
./scripts/stop-all.sh
```