#!/usr/bin/env bash
# Re-process all images through the face detection pipeline (skip EXIF).
# Uses the /images/reprocess endpoint with skip_exif=true to only redo face detection.
#
# Prerequisites:
#   - API container must be running (docker compose up -d api)
#   - FACE_DETECTOR_BACKEND must be set (opencv for memory-constrained containers)
#
# Usage:
#   bash scripts/reprocess_faces.sh
#   bash scripts/reprocess_faces.sh --dry-run   # just list images, don't process

set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# Find all image files relative to the inputs/ directory
INPUT_DIR="inputs"
IMAGES=()
while IFS= read -r -d '' img; do
    IMAGES+=("$(basename "$img")")
done < <(find "$INPUT_DIR" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.heic" -o -iname "*.webp" \) -print0 | sort -z)

echo "Found ${#IMAGES[@]} images to reprocess"
echo "---"

if $DRY_RUN; then
    for img in "${IMAGES[@]}"; do
        echo "  [DRY RUN] Would reprocess: $img"
    done
    echo "---"
    echo "Dry run complete. Remove --dry-run to process."
    exit 0
fi

SUCCESS=0
FAIL=0
SKIP=0

for img in "${IMAGES[@]}"; do
    echo "Processing: $img"
    # URL-encode the filename for the form field
    ENCODED_IMG=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$img'))")

    # Call the reprocess endpoint with skip_exif=true (only redo face detection)
    RESPONSE=$(curl -s -w "\n%{http_code}" \
        -X POST "${API_URL}/images/reprocess" \
        -F "file_source=${img}" \
        -F "skip_exif=true" \
        -F "skip_faces=false" \
        --max-time 600 2>&1) || true

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [[ "$HTTP_CODE" == "200" ]]; then
        # Check if the SSE stream contains face data
        if echo "$BODY" | grep -q "faces_complete"; then
            FACES=$(echo "$BODY" | grep "faces_complete" | head -1)
            echo "  ✓ OK — $FACES"
            SUCCESS=$((SUCCESS + 1))
        else
            echo "  ✓ OK (200) — no face events in stream"
            SUCCESS=$((SUCCESS + 1))
        fi
    elif [[ "$HTTP_CODE" == "000" ]]; then
        echo "  ✗ FAIL — curl error (timeout/connection): $BODY"
        FAIL=$((FAIL + 1))
    else
        echo "  ✗ FAIL — HTTP $HTTP_CODE: $(echo "$BODY" | head -3)"
        FAIL=$((FAIL + 1))
    fi
    echo ""
done

echo "========================================="
echo "Reprocessing complete: ${#IMAGES[@]} images"
echo "  Success: $SUCCESS"
echo "  Failed:  $FAIL"
echo "========================================="