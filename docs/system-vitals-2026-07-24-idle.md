# System Vitals — Idle Baseline (Post-Teardown)

**Captured:** Fri Jul 24 08:46 EDT 2026
**Context:** All stack processes stopped (llama-servers, whisper-server, Docker containers, Vite dev server). Docker Desktop is still running (GUI app open) but no containers are up. The only active workload is this opencode session. Compare against `system-vitals-baseline.md` (pre-processing) and `system-vitals-2026-07-24-batch.md` (mid-batch).

---

## Host (macOS)

| Metric | Pre-batch baseline | Mid-batch | Idle (now) |
|---|---|---|---|
| Cores (logical / physical) | 10 / 10 | 10 / 10 | 10 / 10 |
| Load Average (1m / 5m / 15m) | 3.57 / 8.06 / 6.62 | 4.75 / 6.31 / 6.14 | **1.57 / 1.71 / 1.70** |
| Uptime | 16d 7:59 | 16d 9:41 | 16d 17:28 |
| Total Physical RAM | 16 GB | 16 GB | 16 GB |
| Free pages (16KB) | 4,246 (~66 MB) | 3,816 (~59 MB) | **260,625 (~4.0 GB)** |
| Active pages | 247,892 (~3.8 GB) | 287,910 (~4.4 GB) | 280,699 (~4.3 GB) |
| Inactive pages | 244,201 (~3.7 GB) | 27,054 (~4.1 GB) | 245,339 (~3.8 GB) |
| Wired pages | 280,429 (~4.3 GB) | 859,889 (~13.1 GB) | **129,187 (~2.0 GB)** |
| Purgeable | 4,469 (~70 MB) | 4 (~0 MB) | 13,415 (~208 MB) |
| Thermal | No throttling | No throttling | No throttling |
| Power | AC | AC (100%) | AC |

### Top Processes by CPU %

| Process | %CPU | %MEM | RSS |
|---|---|---|---|
| opencode | 54.7% | 5.8% | ~946 MB |
| Activity Monitor | 17.4% | 1.1% | ~179 MB |
| WindowServer | 11.7% | 0.6% | ~100 MB |
| Terminal | 6.4% | 0.4% | ~73 MB |
| com.docker.backend | 0.5% | 1.3% | ~209 MB |

### Top Processes by MEM %

| Process | %MEM | RSS |
|---|---|---|
| opencode | 5.8% | ~946 MB |
| Spotlight | 2.2% | ~360 MB |
| com.docker.backend | 1.3% | ~209 MB |
| Docker Desktop (GUI) | 1.1% | ~174 MB |
| Activity Monitor | 1.1% | ~179 MB |

---

## Disk

| Mount | Pre-batch | Mid-batch | Idle (now) |
|---|---|---|---|
| / (system) | — | 27% (47 Gi free) | 23% (56 Gi free) |
| /System/Volumes/Data | — | 89% (47 Gi free) | **87% (56 Gi free)** |

> Data volume dropped from 89% → 87% — the 56 Gi free is up from 47 Gi during the batch. Still tight; build-cache prune remains the safest reclaim (~17 GB).

---

## Stack State

| Component | State |
|---|---|
| llama-server (LLM, port 8080) | stopped |
| llama-server (embed, port 8081) | stopped |
| llama-server (reranker, port 8082) | stopped |
| whisper-server (port 8090) | stopped |
| Docker containers (api, lightrag, postgres, nexus, mcp) | stopped (none running) |
| Docker Desktop (GUI app) | running (no containers) |
| Vite dev server (port 5180) | stopped (killed) |
| Listening ports (all stack ports) | none |
| opencode session | running (sole workload) |

---

## Interpretation

- **Load**: 1.57 on 10 cores = ~16% utilization — effectively idle. Down from 6.31 mid-batch and 8.06 pre-batch (which had prior work running).
- **Memory**: **4.0 GB free** — a dramatic recovery from 59 MB mid-batch and 66 MB pre-batch. Wired memory dropped from 13.1 GB (mid-batch, llama-server + Docker VM loaded) to **2.0 GB**. The host has comfortable headroom now.
- **Docker Desktop**: still running (GUI + helper processes, ~400 MB combined) but no containers and no VM allocated. Quitting Docker Desktop entirely would free another ~400 MB.
- **Disk**: 87% on data volume — still the long-term concern. 56 Gi free is enough for normal operation but batch image processing could push it.

### Comparison Summary

| Metric | Pre-batch | Mid-batch | Idle |
|---|---|---|---|
| Free memory | 66 MB | 59 MB | **4.0 GB** |
| Wired | 4.3 GB | 13.1 GB | **2.0 GB** |
| Load (5m) | 8.06 | 6.31 | **1.71** |
| Data disk free | — | 47 Gi | 56 Gi |

The memory pressure that made the laptop "barely usable" during the batch was driven by: llama-server GPU wired memory (~931 MB for the LLM alone) + Docker VM reservation (3 GiB, now reduced to 1.5 GiB) + container working set (~0.75 GiB). With the stack down, the host recovers ~10 GB of wired memory.