#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph — post-start smoke test
# Loads http://localhost:3000 and confirms the nexus UI is serving.
# Retries for up to 30s — docker compose up -d returns before containers are
# healthy, so the first probe may hit a still-starting nexus.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

NEXUS_URL="${NEXUS_URL:-http://localhost:3000}"

for _ in $(seq 1 15); do
    code="$(curl -s -o /dev/null -w "%{http_code}" "$NEXUS_URL/" 2>/dev/null || echo "000")"
    if [[ "$code" == "200" ]]; then
        ctype="$(curl -s -D - -o /dev/null "$NEXUS_URL/" | grep -i '^content-type:' | tr -d '\r' || true)"
        if echo "$ctype" | grep -qi "text/html"; then
            echo "  ✓ GET $NEXUS_URL/ → 200 text/html"
            exit 0
        fi
    fi
    sleep 2
done

echo "  ✗ GET $NEXUS_URL/ → $code (expected 200 text/html) after 30s" >&2
exit 1