# Issue Tracker

Living document. Add new issues at the bottom. Format: `ISSUE-N: Title — Status`.

Statuses: `triage` | `confirmed` | `in-progress` | `fixed` | `wontfix`

---

## ISSUE-1: Relation-create verify loop spins 45s/edge on missing photo entity — `fixed`

**Fixed in**: `api/services/processor.py` — 4 fixes applied and verified in running container.
**Verified**: Jobs completing with `[EXIF Relations] Complete: 4 entities, 9 relations`, zero retry loops, zero false photo matches.

**Symptom**: Image processing stalls for 10+ minutes per image. Logs flood with:
```
WARNING:api.processor:[Relation] 'PXL_...jpg (Photo)' -> '2026-06-27 (Date)' conflict reported but edge not found, retrying (2/5)
...
ERROR:api.processor:[Relation] 'PXL_...jpg (Photo)' -> '2026-06-27 (Date)' reported exists but edge not found after 5 verifies
```

**Root cause** (chain of 3 bugs):

### Bug 1a — Photo entity creation failure is not checked before creating relations
- `api/services/processor.py:1310-1317` — `_create_entity_verified` returns `{"status": "error", ...}` when the entity create fails, but the caller **never checks `photo_result["status"]`**. It unconditionally proceeds to create EXIF relations referencing the photo as source/target.
- Evidence: `GET /graph/entity/exists?name=PXL_20260628_005348911.RAW-01.jpg (Photo)` → `{"exists": false}`. The label list (604 labels) contains no match for `005348911`.
- LightRAG's `/graph/relation/create` requires both entities to pre-exist (docstring at `lightrag/api/routers/graph_routes.py:564-569`). With the photo entity missing, `acreate_relation` raises `ValueError` → HTTP **400**.

### Bug 1b — `_classify_create_error` misclassifies "entity not found" 400 as "exists"
- `api/services/processor.py:91` — any HTTP 400 is classified as `"exists"`:
  ```python
  if status == 400 or "400" in msg or "already exists" in combined:
      return "exists"
  ```
- But 400 from `/graph/relation/create` covers **two different conditions**: (a) "relation already exists" (the legitimate "exists" case) and (b) "source/target entity not found" (a `ValueError` from `acreate_relation`, `graph_routes.py:634-638`).
- Misclassification sends the code into the verify-and-retry path instead of failing fast, burning 5 retries × (3+6+9+12+15)s sleep = **~45s per edge**. With ~10 EXIF relations per photo that's **~7.5 min per photo** of pure wasted sleep.

### Bug 1c — Subgraph BFS truncation makes the verify step unreliable for hub nodes
- `api/services/processor.py:317` — the verify query is `GET /graphs?label=<source>` with **no `max_depth`/`max_nodes` params**, hitting LightRAG defaults (`max_depth=3, max_nodes=1000`).
- LightRAG performs BFS and truncates to `max_nodes` (`lightrag/kg/postgres_impl.py:7597`, neo4j_impl, etc — "Graph truncated: ... nodes found, limited to max_nodes").
- Hub nodes like `"2026-06-27 (Date)"` have 584+ edges in a depth-3 traversal (confirmed: `GET /graphs?label=2026-06-27 (Date)` → 251 nodes, 584 edges, 368 photo edges). BFS truncation drops the specific edge being verified → false negative → retry.
- The orphan-fix script (`scripts/fix_orphan_nodes.py:88`) already works around this with `max_depth=1&max_nodes=50`, but the processor's verify does not narrow the query at all.

**Impact**: 17 of 20 recently-attached images stuck pending; 3 processing jobs stalled 10-12 min each in `linking_entities` stage. ~45s/edge wasted in retries that can never succeed.

**Proposed fixes** (apply all three):

1. **1a**: After `_create_entity_verified` for the photo (line 1310), check `photo_result["status"]`. If `"error"`, log and return early — skip relation creation for this photo. Same guard for dimension entities (line 1332).

2. **1b**: In `_classify_create_error`, distinguish "already exists" from "entity not found":
   - Check the response body for "already exists" (→ `"exists"`) vs "not found"/"does not exist"/"No such entity" (→ `"error"`).
   - Only map a bare 400 to `"exists"` if the body contains an "already exists" marker. Otherwise return `"error"` so the caller fails fast instead of retrying.
   - Add a `_RELATION_EXISTS_MARKERS` tuple similar to `_PIPELINE_BUSY_MARKERS`.

3. **1c**: Make the verify query reliable:
   - Option A (narrow): query with `max_depth=1&max_nodes=N` to get only the direct neighborhood of the source entity. The edge to verify is always 1-hop, so depth 3 is wasted breadth.
   - Option B (direct): use a dedicated edge-existence check endpoint if LightRAG provides one, instead of pulling the whole subgraph and scanning.
   - Option C (pragmatic): on verify miss, treat a 400 "already exists" response as success (trust LightRAG's conflict detection) rather than requiring the edge to appear in a truncated traversal. Accept the small risk of a stale-index false positive in exchange for not blocking on truncation.

**Files**: `api/services/processor.py` (lines 60-93, 207-356, 1305-1350)

**Evidence captured**:
- `curl /graph/entity/exists?name=PXL_20260628_005348911.RAW-01.jpg (Photo)` → `{"exists": false}`
- `curl /graphs?label=2026-06-27 (Date)` → 251 nodes / 584 edges (truncated from more)
- `curl /graphs?label=2026-06-27 (Date)&max_depth=1` → 3 nodes / 3 edges (edge to 005348911 absent)
- `docker logs knowledge-graph-api` — retry loop across 3 photos, all referencing the same missing entities
- LightRAG logs: `Graph truncated: found 94 nodes within max_depth 3` repeated for every Person/* verify query

---

## ISSUE-2: API container restart loses in-flight job stage progress — `triage`

**Symptom**: API container restarted (Up 5 min) and "Resumed 20 pending/interrupted jobs" — but resumed jobs show `stage=""` (empty), meaning stage progress from before the restart was lost.

**Observation**: Jobs pending before restart still show empty stage after resume. The `updated_at` on resumed jobs jumped to restart time, losing the pre-restart timeline. Need to verify whether job_manager persists stage to DB or only in-memory.

**Files to investigate**: `api/services/job_manager.py`, job persistence layer.

**Priority**: Medium — affects observability and resume correctness, not data integrity.

---

## ISSUE-3: EXIF-link cross-photo matching scans full entity catalog per relation — `triage`

**Symptom**: Logs show `[EXIF Links] Checking '<concept>': file_path='<long SEP-list of many files>' vs file_source='<current file>'` for every concept entity on every photo. This is an O(photos × concepts) scan with string-splitting on `<SEP>`-joined file_path fields.

**Observation**: The `<SEP>` separator pattern (`_GRAPH_FIELD_SEP`) means a single concept entity's `source_id`/`file_path` accumulates every photo it was ever linked from. The EXIF-link step splits that string and linear-scans it for the current file_source — O(n) per relation per photo. As the graph grows this becomes a hot path.

**Files to investigate**: `api/services/processor.py` (EXIF Links section, ~line 1340+), `_GRAPH_FIELD_SEP` usage.

**Priority**: Low — correctness is fine, but scales poorly with graph size.

---

## ISSUE-4: Fuzzy entity matcher matches different photos to each other — `fixed`

**Symptom**: `[EXIF Relations] Photo entity 'PXL_20260628_005345424.RAW-01.jpg (Photo)' matches existing 'PXL_20260628_001655204.RAW-01.jpg (Photo)', reusing` — completely different photo files being treated as the same entity.

**Root cause**: `_find_matching_entity` (processor.py:1283) uses `SequenceMatcher` with threshold 0.85 on the full entity name. Photo filenames like `PXL_20260628_005345424.RAW-01.jpg` and `PXL_20260628_001655204.RAW-01.jpg` share long prefix/suffix substrings (`PXL_20260628_00...RAW-01.jpg`), producing a ratio >0.85. The matcher was designed for concept deduplication ("Saint Petersburg, Florida" vs "St Petersburg FL"), not for unique filenames.

**Impact**: This was the true root cause of ISSUE-1 bug 1a. The wrong photo entity gets reused → LightRAG's relation-create can't find the source entity → 400 → misclassified as "exists" → 45s retry loop. Every relation for the affected photo fails.

**Fix**: Skip fuzzy matching entirely for entities ending in ` (Photo)` — each photo is unique and must always get its own entity node. Exact match only.

**File**: `api/services/processor.py:1291`

**Verified**: After fix, logs show `[Relation] Created 'PXL_20260628_013332134.RAW-01.jpg (Photo)' -> '2026-06-27 (Date)'` — correct photo entity created, relations succeeding.

---

## ISSUE-5: Pipeline runs full sequence per-job (EXIF→VLM→linking), not batch EXIF-first — `fixed`

**Symptom**: User expectation: all 20 photos should have EXIF extracted before any VLM processing begins. Actual: each job runs the full pipeline sequentially (EXIF → entity creation → VLM → LightRAG wait → linking), and only `MAX_CONCURRENT_JOBS = 3` run at once.

**Current flow** (`api/routes/images.py:166` `_process_and_stream`):
1. EXIF extraction + face detection (fast, seconds)
2. EXIF entity creation in LightRAG (Photo/Date/Location/Camera nodes) — happens immediately after EXIF, before VLM
3. VLM description generation (slow, ~30-60s per image, single-slot semaphore)
4. LightRAG document upload + wait for ingestion (slow, ~2-5 min)
5. Visual entity linking

With 20 photos and `MAX_CONCURRENT_JOBS=3`, photos 4-20 don't start EXIF until photos 1-3 finish the entire pipeline (~5-8 min each). So the last photo's EXIF won't be extracted for ~30+ minutes.

**VLM semaphore**: `_vlm_semaphore` (processor.py:102) serializes VLM calls to 1 concurrent (matching the llama-server `-np 1` slot). So even with higher `MAX_CONCURRENT_JOBS`, VLM calls would still serialize.

**Possible approaches**:
- **A: Two-phase batch**: Phase 1 runs EXIF for all N photos (fast, parallelizable). Phase 2 runs VLM/ingestion for all N (serialized by VLM semaphore). Needs a batch coordinator.
- **B: Increase concurrency**: Raise `MAX_CONCURRENT_JOBS` so more jobs run EXIF in parallel. VLM still serializes, but EXIF/entity-creation happens sooner for more photos.
- **C: Separate EXIF-only pass**: Add an endpoint that does EXIF + entity creation only (no VLM), then a second pass for VLM/ingestion.

**Files**: `api/services/job_manager.py:20` (`MAX_CONCURRENT_JOBS=3`), `api/routes/images.py:166` (`_process_and_stream`), `api/services/processor.py` (`_vlm_semaphore`).

**Priority**: Medium — affects throughput and user-perceived latency for batch uploads.

**Fixed**: Implemented two-phase batch model (Option A).

**Changes**:
- Split `_process_and_stream` into `_run_exif_phase` (EXIF + faces + entity creation) and `_run_ai_phase` (VLM + LightRAG + linking) in `api/routes/images.py`.
- Added `run_batch_phases` coordinator to `api/services/job_manager.py`: runs all jobs through phase 1 with high concurrency (`MAX_EXIF_CONCURRENT = 10`), then promotes phase-1-complete jobs to phase 2 with the existing `_semaphore` (3 concurrent, VLM-gated).
- Added `metadata_text` column to `photo_metadata` table to bridge phase state across the phase boundary and restarts.
- Updated `resume_pending_jobs` to classify jobs by phase: pending → phase 1, `exif_complete` stage → phase 2, mid-phase-1 → restart phase 1, mid-phase-2 → restart phase 2.
- Added `enqueue_and_maybe_batch` with 1.5s debounce so burst uploads coalesce into one batch.
- New stage `exif_complete` marks the phase boundary.
- New event `exif_phase_complete` emitted at end of phase 1.
- Single-image upload (`POST /images/process`, `POST /images/jobs`) still works — runs both phases for that one job.

**Verified**: 4 resumed jobs all ran phase 1 in parallel (10-concurrency), then transitioned to phase 2 (3-concurrency) as slots freed. EXIF + entity creation completed for all 4 within ~2 min; VLM/LightRAG phase proceeded as expected.
