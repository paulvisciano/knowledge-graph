#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph — Ensure Docker Desktop VM memory matches the project config
# ═══════════════════════════════════════════════════════════════════════════════
# Docker Desktop's VM memory limit is a HOST-LEVEL client setting — it cannot be
# expressed in docker-compose.yml. This script syncs it to DOCKER_VM_MEMORY_MIB
# from .env so every developer gets the right allocation automatically.
#
# Container-level mem_limit caps ARE in docker-compose.yml (shareable natively);
# this script only handles the Docker Desktop VM envelope that contains them.
#
# Run before `docker compose up` or via the README quick start. Safe to re-run.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env (prefer project .env, fall back to .env.example)
ENV_FILE="$PROJECT_DIR/.env"
[[ -f "$ENV_FILE" ]] || ENV_FILE="$PROJECT_DIR/.env.example"
if [[ -f "$ENV_FILE" ]]; then
    set -a; source "$ENV_FILE"; set +a
fi

# Default: 1.5 GiB — sized to cover the 1.75 GiB of container mem_limit caps in
# docker-compose.yml with Docker Desktop's own overhead. Override via .env.
TARGET_MEM="${DOCKER_VM_MEMORY_MIB:-1536}"

# ─── Locate Docker Desktop settings (macOS only) ──────────────────────────────
# Docker Desktop on macOS stores settings in Group Containers. Two file names
# have been used across versions; check both.
SETTINGS_CANDIDATES=(
    "$HOME/Library/Group Containers/group.com.docker/settings-store.json"
    "$HOME/Library/Group Containers/group.com.docker/settings.json"
)
SETTINGS_FILE=""
for f in "${SETTINGS_CANDIDATES[@]}"; do
    if [[ -f "$f" ]]; then
        SETTINGS_FILE="$f"
        break
    fi
done

if [[ -z "$SETTINGS_FILE" ]]; then
    echo "WARNING: Docker Desktop settings file not found."
    echo "         Is Docker Desktop installed? On macOS it should be at:"
    echo "           ~/Library/Group Containers/group.com.docker/settings-store.json"
    echo "         Skipping Docker VM memory configuration."
    exit 0
fi

# ─── Read current value ────────────────────────────────────────────────────────
CURRENT_MEM=$(python3 -c "
import json, sys
try:
    with open('$SETTINGS_FILE') as f:
        print(json.load(f).get('MemoryMiB', 'unset'))
except Exception as e:
    print('error')
" 2>/dev/null || echo "error")

if [[ "$CURRENT_MEM" == "error" ]]; then
    echo "WARNING: Could not parse Docker Desktop settings at $SETTINGS_FILE — skipping."
    exit 0
fi

# ─── Apply if needed ──────────────────────────────────────────────────────────
if [[ "$CURRENT_MEM" == "$TARGET_MEM" ]]; then
    echo "✓ Docker Desktop VM memory already set to ${TARGET_MEM} MiB — no change."
    exit 0
fi

echo "Docker Desktop VM memory: ${CURRENT_MEM} MiB → ${TARGET_MEM} MiB"
python3 - << PYEOF
import json
p = "$SETTINGS_FILE"
with open(p) as f:
    d = json.load(f)
d["MemoryMiB"] = $TARGET_MEM
with open(p, "w") as f:
    json.dump(d, f, indent=2)
print(f"  ✓ Written to {p}")
PYEOF

echo "  ⚠ Docker Desktop must be restarted for the change to take effect."
echo "    Restart Docker Desktop, then run: docker compose up -d"