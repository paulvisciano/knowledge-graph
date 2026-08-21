#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph — post-start smoke test
# Loads http://localhost:3000 and confirms the Knowledge Graph UI is serving.
# Retries for up to 30s — docker compose up -d returns before containers are
# healthy, so the first probe may hit a still-starting Knowledge Graph.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

KG_URL="${KG_URL:-https://localhost:3443}"

for _ in $(seq 1 15); do
    code="$(curl -sk -o /dev/null -w "%{http_code}" "$KG_URL/" 2>/dev/null || echo "000")"
    if [[ "$code" == "200" ]]; then
        ctype="$(curl -sk -D - -o /dev/null "$KG_URL/" | grep -i '^content-type:' | tr -d '\r' || true)"
        if echo "$ctype" | grep -qi "text/html"; then
            echo "  ✓ GET $KG_URL/ → 200 text/html"
            exit 0
        fi
    fi
    sleep 2
done

echo "  ✗ GET $KG_URL/ → $code (expected 200 text/html) after 30s" >&2
exit 1