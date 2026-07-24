# System Vitals — During 20-Image Batch Processing

**Captured:** Fri Jul 24 00:55 EDT 2026
**Context:** Snapshot taken ~8 min after queuing a 20-image batch into the two-phase pipeline (EXIF → AI/VLM → LightRAG). LightRAG pipeline was `pipeline_busy: true`. Compare against `system-vitals-baseline.md` (captured pre-batch at Thu Jul 23 23:18 EDT).

---

## Host (macOS)

| Metric | Baseline (pre-batch) | Now (mid-batch) |
|---|---|---|
| Cores (logical / physical) | 10 / 10 | 10 / 10 |
| Load Average (1m / 5m / 15m) | 3.57 / 8.06 / 6.62 | 4.75 / 6.31 / 6.14 |
| Uptime | 16d 7:59 | 16d 9:41 |
| Total Physical RAM | 16 GB | 16 GB |
| Free pages (16KB) | 4,246 (~66 MB) | 3,816 (~59 MB) |
| Wired | 280,429 (~4.3 GB) | 859,889 (~13.1 GB) |
| Thermal | No throttling | No throttling |
| Power | AC | AC (100%, finishing charge) |

### Top Processes by CPU %

| Process | %CPU | %MEM | RSS |
|---|---|---|---|
| opencode | 41.7% | 1.9% | ~313 MB |
| Terminal | 11.3% | 0.2% | ~30 MB |
| Virtualization VM | 6.7% | 0.4% | ~60 MB |
| Docker Helper (Renderer) | 6.6% | 0.5% | ~84 MB |
| WindowServer | 5.5% | 0.3% | ~44 MB |
| llama-server (host) | 3.5% | 5.5% | ~931 MB |

### Top Processes by MEM %

| Process | %MEM | RSS |
|---|---|---|
| llama-server (host, port 8080) | 5.5% | ~931 MB |
| opencode | 1.9% | ~321 MB |
| whisper-server | 1.0% | ~173 MB |

---

## Docker Containers

### Resource Usage

| Container | CPU % | Mem Usage / Limit | Net I/O | Block I/O |
|---|---|---|---|---|
| knowledge-graph-api | 1.10% | 314 MiB / 1 GiB | 723 MB / 252 MB | 917 MB / 1.24 GB |
| knowledge-graph-nexus | 0.00% | 10.71 MiB / 256 MiB | 4.3 kB / 126 B | 13.8 MB / 2.95 MB |
| knowledge-graph-lightrag | 3.86% | 236 MiB / 2 GiB | 216 MB / 2.81 GB | 486 MB / 769 MB |
| knowledge-graph-mcp | 1.35% | 12.96 MiB / 256 MiB | 45.5 kB / 81.2 kB | 81.9 MB / 59 MB |
| knowledge-graph-postgres | 0.00% | 245 MiB / 1 GiB | 197 MB / 284 MB | 414 MB / 1.18 GB |

### Status

| Container | Status | Ports |
|---|---|---|
| knowledge-graph-api | Up 23 min (healthy) | 8000 |
| knowledge-graph-nexus | Up 2 hr (healthy) | 3000 |
| knowledge-graph-lightrag | Up 6 hr (healthy) | 9621 |
| knowledge-graph-mcp | Up 7 hr (healthy) | 9653 |
| knowledge-graph-postgres | Up 7 hr (healthy) | 5432 |

---

## llama.cpp Servers (host)

| Port | Role | Model | Health | Slots (active) |
|---|---|---|---|---|
| 8080 | LLM / VLM | Gemma-4-12B-OBLITERATED-Q4_K_M | healthy | 4 (1 active) |
| 8081 | Embeddings | bge-m3-Q4_K_M | healthy | 4 (0 active) |
| 8082 | Reranker | bge-reranker-v2-m3-Q4_K_M | healthy | 4 (0 active) |
| 8090 | Whisper (STT) | — | healthy | — |

---

## Disk

| Mount | Size | Used | Avail | Capacity |
|---|---|---|---|---|
| / (system) | 460 Gi | 17 Gi | 47 Gi | 27% |
| /System/Volumes/Data | 460 Gi | 371 Gi | 47 Gi | **89%** |

### Docker Disk

| Type | Total | Active | Size | Reclaimable |
|---|---|---|---|---|
| Images | 20 | 10 | 27.93 GB | 6.40 GB (22%) |
| Containers | 10 | 5 | 80.0 MB | 33.3 MB (41%) |
| Local Volumes | 6 | 5 | 3.03 GB | 0 B |
| Build Cache | 220 | 0 | 49.8 GB | 17.0 GB |

> ⚠️ Data volume at **89%** — within ~50 Gi of capacity. Build cache (49.8 GB, 17 GB reclaimable) is the prime candidate for `docker builder prune`.

---

## LightRAG

| Metric | Value |
|---|---|
| Health endpoint | `healthy` (core v1.5.5, api v0315) |
| `pipeline_busy` | **true** |
| `pipeline_active` | **true** |
| LLM | openai → host:8080, Gemma-4-12B-OBLITERATED-Q4_K_M |
| Embeddings | openai → host:8081, bge-m3-Q4_K_M |
| Reranker | jina → host:8082, bge-reranker-v2-m3-Q4_K_M |
| `max_parallel_insert` | 3 |
| `max_async` (LLM) | 4 |
| Graph storage | NetworkXStorage |
| Vector storage | PGVectorStorage (pgvector) |

### Recent LightRAG Error Patterns (last 20 min)

| Pattern | Count | Severity |
|---|---|---|
| `Entity already exists` / `Validation error creating entity` | many | benign (idempotent re-inserts) |
| `Relation already exists` / `Validation error creating relation` | many | benign (idempotent re-inserts) |

> These "already exists" errors are expected from concurrent/duplicate inserts and do not indicate data loss.

---

## Pipeline Status (Jobs API)

Snapshot of `/images/jobs` at capture time.

| Status | Count |
|---|---|
| complete | 81 |
| processing | 23 |
| **Total** | **104** |

| Stage | Count |
|---|---|
| pipeline_complete | 81 |
| exif_complete | 20 |
| processing_ai | 2 |
| linking_entities | 1 |

### Current 20-Image Batch (most recent 20 jobs)

All 20 are `status=processing`, `stage=exif_complete` — i.e. EXIF phase done, queued for the AI (VLM + LightRAG) phase, blocked on the LightRAG pipeline which is busy.

| file_source | age | idle (since last update) |
|---|---|---|
| PXL_20260425_142156714.MP.jpg | 475 s | 267 s |
| PXL_20260425_142152977.jpg | 475 s | 277 s |
| PXL_20260425_130746998.jpg | 475 s | 267 s |
| PXL_20260424_202725297.MP.jpg | 475 s | 277 s |
| PXL_20260424_202728912.jpg | 475 s | 277 s |
| PXL_20260424_183140200.jpg | 475 s | 277 s |
| PXL_20260424_202727249.jpg | 475 s | 368 s |
| PXL_20260424_183135463.TS-000.jpg | 478 s | 267 s |
| PXL_20260424_183136946.jpg | 504 s | 382 s |
| PXL_20260424_183138846.jpg | 504 s | 377 s |
| PXL_20260424_183132904.jpg | 506 s | 388 s |
| PXL_20260424_183134167.jpg | 506 s | 356 s |
| PXL_20260424_183131076.jpg | 507 s | 349 s |
| PXL_20260424_183128851.jpg | 511 s | 428 s |
| PXL_20260424_173141803.MP.jpg | 515 s | 415 s |
| PXL_20260424_173148930.jpg | 521 s | 429 s |
| PXL_20260424_173143150.jpg | 526 s | 415 s |
| PXL_20260424_160806332.jpg | 529 s | 428 s |
| PXL_20260424_160812149.jpg | 532 s | 411 s |
| PXL_20260424_160808035.jpg | 532 s | 428 s |

13/20 have been idle > 3 min — waiting on the LightRAG insert queue (`max_parallel_insert=3`) which is saturated by the 3 jobs already in `processing_ai` / `linking_entities`.

---

## Image Processing Time Statistics

Computed from 73 completed jobs with valid `created_at` → `updated_at` intervals.

### Summary (seconds)

| Metric | Value |
|---|---|
| Count | 73 |
| Min | 329.4 s (~5.5 min) — PXL_20251213_071646413.jpg |
| P50 (median) | 1,709.1 s (~28.5 min) |
| Mean | 2,454.1 s (~40.9 min) |
| P90 | 4,741.8 s (~79 min) |
| P99 | 24,595.8 s (~6.8 hr) |
| Max | 24,595.8 s (~6.8 hr) — PXL_20260628_205458135.RAW-01.jpg |
| Stdev | 3,019.0 s |

### Duration Distribution

| Bucket | Count |
|---|---|
| < 30 s | 0 |
| 30–60 s | 0 |
| 60–120 s | 0 |
| 120–300 s | 0 |
| 300–600 s | 7 |
| > 600 s | 66 |

> 90% of completed images take **> 10 minutes** end-to-end. The mean (41 min) is dragged up by a long tail; the p99 outlier at ~6.8 hr (PXL_20260628_205458135.RAW-01.jpg) coincides with the `Cooler` → Photo relation that the API logs show "stayed busy for 300s, giving up" — a LightRAG contention timeout.

### Slowest 5

| Duration | file_source |
|---|---|
| 4,801.2 s (~80 min) | PXL_20260628_212821742.RAW-01.jpg |
| 4,911.7 s (~82 min) | PXL_20260628_205459923.RAW-01.jpg |
| 5,353.8 s (~89 min) | PXL_20260605_143802005.RAW-01.jpg |
| 5,561.6 s (~93 min) | PXL_20260628_003058167.RAW-01.jpg |
| 24,595.8 s (~6.8 hr) | PXL_20260628_205458135.RAW-01.jpg |

### Notes

- **`created_at` → `updated_at`** spans the full two-phase pipeline (EXIF extraction → face detection → VLM description → LightRAG insert → entity linking). It is wall-clock, so jobs queued behind a busy LightRAG pipeline accrue idle time while waiting on the insert queue.
- No jobs have a `status` of `error` (errors are surfaced as warnings in logs and as `pipeline_complete` with a `status=...` payload in the SSE stream); the one `Cooler` relation did give up after 300 s of LightRAG contention.
- The 7 jobs in the 300–600 s bucket are the fastest completions and generally correspond to images that entered the AI phase while LightRAG was idle.

---

## Interpretation & Watchpoints

- **Load**: 5-min load 6.31/10 ≈ 63% host utilization — below saturation. Up from baseline's 8.06, so the system has actually cooled as the 20-image batch is mostly *waiting* rather than computing.
- **Memory**: ~59 MB free, wired up to ~13.1 GB (vs 4.3 GB baseline) — the host is memory-pressured but not swapping aggressively; llama-server (931 MB) and the Docker VM are the main consumers.
- **Disk**: Data volume at **89%** — approaching capacity. Build cache prune is the safest reclaim.
- **Pipeline bottleneck**: LightRAG `max_parallel_insert=3` is the gate. The 20-image batch finished EXIF in seconds, then stalled at `exif_complete` waiting for insert slots. Only 3 jobs are actively advancing through `processing_ai`/`linking_entities`.
- **Processing time**: median ~28 min, but with a heavy tail (p99 ~6.8 hr) driven by insert-queue contention, not compute. Tuning `max_parallel_insert` or the VLM semaphore is the highest-leverage optimization if throughput matters more than per-request latency.

### Suggested Follow-ups

1. Prune Docker build cache (`docker builder prune`) to reclaim ~17 GB and ease disk pressure.
2. Investigate the LightRAG insert contention — raising `max_parallel_insert` from 3 may improve batch throughput if the LLM server (only 1/4 slots active) has headroom.
3. Consider backpressure signaling from LightRAG to the API so jobs at `exif_complete` report `queued_for_ai` rather than appearing "stalled".