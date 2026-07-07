# Knowledge Graph

Personal knowledge graph stack: LightRAG + local LLMs + custom UI.

## Structure

```
knowledge-graph/
├── lightrag/          # LightRAG source (git submodule)
├── models/            # symlink → ~/models (GGUF model files)
├── scripts/           # llama-server start/stop scripts
├── ui/                # Custom frontend (TODO)
├── secrets/           # SSL certs, API keys (not committed)
├── docker-compose.yml # Orchestrates all Docker services
├── .env.example       # Environment template
└── .env               # Local config (not committed)
```

## Quick Start

1. Copy and customize environment:
   ```sh
   cp .env.example .env
   ```

2. Start llama-servers on host:
   ```sh
   ./scripts/start-llama-servers.sh
   ```

3. Start LightRAG:
   ```sh
   docker compose up -d
   ```

4. Open http://localhost:9621

## Stop Everything

```sh
./scripts/stop-all.sh
```