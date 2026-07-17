/**
 * Layout.ts — pure, deterministic graph → canvas-cell mapping.
 *
 * Turns `KGNode[]` + `KGEdge[]` into `CanvasNode[]` by assigning each node a
 * stable chunk-cell coordinate `(cellX, cellY, cellZ)` and a within-chunk
 * offset `(localX, localY, localZ)`. The strategy (see
 * `docs/infinite-canvas-report.html` §data) is:
 *
 *   cellX = time bucket  (primary axis — new photos scroll in from +X)
 *   cellY = cluster band (hubMap grouping → dense band index)
 *   cellZ = relationship depth (0 = focused + 1-hop, 1 = 2-hop, 2 = far; 0 when nothing focused)
 *
 * No Three.js / DOM access here — this module is pure data and must be
 * deterministic: two calls with the same input produce identical coords.
 */

import type { KGNode, KGEdge } from '$lib/constants';
import { API } from '$lib/constants';
import type { CanvasNode, NodeKind } from './renderer/types';
import { CHUNK_SIZE } from './renderer/constants';

const KG_API_BASE = '/api/kg';

// ---------------------------------------------------------------------------
// Kind classification (ported from GraphCanvas.svelte:132-144)
// ---------------------------------------------------------------------------

/** True if the node represents a Photo/Image entity. */
function isPhotoNode(node: {
  labels?: string[];
  properties?: Record<string, unknown>;
  id?: string;
}): boolean {
  return (
    !!node.labels?.some((l) => /^(Photo|Image)$/i.test(l)) ||
    (node.properties?.entity_type as string) === 'Photo' ||
    (node.properties?.entity_type as string) === 'Image' ||
    (node.id ?? '').includes('(Photo)') ||
    (node.id ?? '').includes('(Image)')
  );
}

/** True if the node represents a Person entity. */
function isPersonNode(node: {
  labels?: string[];
  properties?: Record<string, unknown>;
}): boolean {
  const et = node.properties?.entity_type;
  if (typeof et === 'string' && et.toLowerCase() === 'person') return true;
  return !!node.labels?.some((l) => l.toLowerCase() === 'person');
}

/** True if the node represents a Location entity. */
function isLocationNode(node: {
  labels?: string[];
  properties?: Record<string, unknown>;
}): boolean {
  const et = node.properties?.entity_type;
  if (typeof et === 'string' && et.toLowerCase() === 'location') return true;
  return !!node.labels?.some((l) => /^(Location|Place|GpsPoint)$/i.test(l));
}

/** True if the node represents an Event entity. */
function isEventNode(node: {
  labels?: string[];
  properties?: Record<string, unknown>;
}): boolean {
  const et = node.properties?.entity_type;
  if (typeof et === 'string' && et.toLowerCase() === 'event') return true;
  return !!node.labels?.some((l) => /^Event$/i.test(l));
}

/**
 * Classify a `KGNode` into one of the renderer's visual categories.
 * Order matters: photo → person → location → event → concept (fallback).
 */
export function classifyKind(node: KGNode): NodeKind {
  if (isPhotoNode(node)) return 'photo';
  if (isPersonNode(node)) return 'person';
  if (isLocationNode(node)) return 'location';
  if (isEventNode(node)) return 'event';
  return 'concept';
}

// ---------------------------------------------------------------------------
// Small deterministic helpers
// ---------------------------------------------------------------------------

/** Stable 32-bit hash of a string → unsigned int. */
function hashStr(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/**
 * Parse a date from any of the known photo-timestamp properties.
 * Returns `null` when no usable timestamp is present.
 */
function parseNodeDate(node: KGNode): Date | null {
  const p = node.properties ?? {};
  const raw =
    p.date_taken_friendly ??
    p.datetime_original ??
    p.created_at ??
    p.timestamp ??
    p.date_taken ??
    p.datetime;
  if (raw === undefined || raw === null) return null;
  if (typeof raw === 'number') {
    // Unix seconds or ms — heuristic: ms if > 1e12, else seconds.
    const ms = raw > 1e12 ? raw : raw * 1000;
    const d = new Date(ms);
    return isNaN(d.getTime()) ? null : d;
  }
  if (typeof raw === 'string') {
    const d = new Date(raw);
    return isNaN(d.getTime()) ? null : d;
  }
  return null;
}

/** Day bucket key (YYYY-MM-DD). */
function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

/** Month bucket key (YYYY-MM). */
function monthKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Cluster band assignment (ported hubMap pattern, GraphCanvas.svelte:174-236)
// ---------------------------------------------------------------------------

interface ClusterAssignment {
  /** nodeId → hubId it belongs to. */
  hubOf: Map<string, string>;
  /** hubId → stable band index (0-based). */
  bandOfHub: Map<string, number>;
  /** Set of hub node ids (degree ≥ median). */
  hubSet: Set<string>;
}

/**
 * Compute the hubMap cluster assignment and map each hub to a stable Y band
 * index. Mirrors `GraphCanvas.svelte` buildDegreeMap: hubs are nodes with
 * degree ≥ median; non-hubs attach to their highest-degree neighbor hub.
 */
function buildClusterAssignment(nodes: KGNode[], edges: KGEdge[]): ClusterAssignment {
  const degree = new Map<string, number>();
  for (const e of edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }

  const degValues = Array.from(degree.values()).sort((a, b) => a - b);
  const medianDeg =
    degValues.length > 0 ? degValues[Math.floor(degValues.length / 2)] : 1;

  const hubSet = new Set<string>();
  for (const [id, deg] of degree) {
    if (deg >= medianDeg) hubSet.add(id);
  }
  if (hubSet.size === 0 && degValues.length > 0) {
    // Fall back to the single highest-degree node.
    let bestId = '';
    let bestDeg = -1;
    for (const [id, deg] of degree) {
      if (deg > bestDeg) {
        bestDeg = deg;
        bestId = id;
      }
    }
    if (bestId) hubSet.add(bestId);
  }

  // Adjacency for O(1) neighbor lookup.
  const adjacency = new Map<string, string[]>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, []);
    if (!adjacency.has(e.target)) adjacency.set(e.target, []);
    adjacency.get(e.source)!.push(e.target);
    adjacency.get(e.target)!.push(e.source);
  }

  const hubOf = new Map<string, string>();
  for (const n of nodes) {
    if (hubSet.has(n.id)) {
      hubOf.set(n.id, n.id);
      continue;
    }
    const neighbors = adjacency.get(n.id) ?? [];
    let bestHub: string | null = null;
    let bestDeg = 0;
    for (const nid of neighbors) {
      const d = degree.get(nid) ?? 1;
      if (d > bestDeg) {
        bestDeg = d;
        bestHub = nid;
      }
    }
    hubOf.set(n.id, bestHub ?? n.id);
  }

  // Assign each hub a stable, dense band index (0, 1, 2, …) so clusters form
  // adjacent horizontal bands near the camera's origin chunk. Sort hub ids
  // first so the band ordering is deterministic across runs (independent of
  // Map insertion order). Sparse hashes would scatter clusters thousands of
  // chunks away from chunk (0,0,0), putting them outside RENDER_DISTANCE.
  const bandOfHub = new Map<string, number>();
  const sortedHubs = Array.from(hubSet).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  for (let i = 0; i < sortedHubs.length; i++) {
    bandOfHub.set(sortedHubs[i], i);
  }

  return { hubOf, bandOfHub, hubSet };
}

// ---------------------------------------------------------------------------
// Time bucketing
// ---------------------------------------------------------------------------

interface TimePlan {
  /** nodeId → cellX (monotonic bucket index). */
  cellXOf: Map<string, number>;
}

/**
 * Assign each photo a time-bucket index along cellX. Non-photo nodes inherit
 * the bucket of their most-recent connected photo (by edge). Nodes with no
 * photo connection fall back to ingestion-order index.
 *
 * Buckets are days by default; if the photo-date span exceeds ~180 days we
 * switch to month buckets so the X axis stays bounded.
 */
function buildTimePlan(nodes: KGNode[], edges: KGEdge[]): TimePlan {
  const photoDate = new Map<string, Date>();
  for (const n of nodes) {
    if (!isPhotoNode(n)) continue;
    const d = parseNodeDate(n);
    if (d) photoDate.set(n.id, d);
  }

  // Decide granularity: if span > 180 days → month buckets; if > 2 days →
  // day buckets; otherwise per-photo (each photo gets its own cellX) so a
  // burst of same-day photos doesn't pile into one chunk.
  let granularity: 'month' | 'day' | 'photo' = 'photo';
  if (photoDate.size > 0) {
    let minT = Infinity;
    let maxT = -Infinity;
    for (const d of photoDate.values()) {
      const t = d.getTime();
      if (t < minT) minT = t;
      if (t > maxT) maxT = t;
    }
    const spanDays = (maxT - minT) / 86_400_000;
    if (spanDays > 180) granularity = 'month';
    else if (spanDays > 2) granularity = 'day';
  }

  const bucketKeyOf = (d: Date): string => {
    if (granularity === 'month') return monthKey(d);
    if (granularity === 'day') return dayKey(d);
    return d.getTime().toString();
  };

  // Collect & sort unique bucket keys → dense index.
  const bucketKeys = new Set<string>();
  for (const d of photoDate.values()) {
    bucketKeys.add(bucketKeyOf(d));
  }
  const sortedBuckets = Array.from(bucketKeys).sort();
  const bucketIndex = new Map<string, number>();
  for (let i = 0; i < sortedBuckets.length; i++) {
    bucketIndex.set(sortedBuckets[i], i);
  }

  // Build adjacency (nodeId → neighbor nodeIds) for photo-inheritance.
  const adjacency = new Map<string, string[]>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, []);
    if (!adjacency.has(e.target)) adjacency.set(e.target, []);
    adjacency.get(e.source)!.push(e.target);
    adjacency.get(e.target)!.push(e.source);
  }

  const cellXOf = new Map<string, number>();

  // Photos: direct bucket.
  for (const n of nodes) {
    if (!isPhotoNode(n)) continue;
    const d = photoDate.get(n.id);
    if (!d) continue;
    const key = bucketKeyOf(d);
    const idx = bucketIndex.get(key);
    if (idx !== undefined) cellXOf.set(n.id, idx);
  }

  // Non-photos: inherit most-recent connected photo's bucket.
  for (const n of nodes) {
    if (isPhotoNode(n)) continue;
    if (cellXOf.has(n.id)) continue;
    const neighbors = adjacency.get(n.id) ?? [];
    let best: { idx: number; t: number } | null = null;
    for (const nid of neighbors) {
      const d = photoDate.get(nid);
      if (!d) continue;
      const key = bucketKeyOf(d);
      const idx = bucketIndex.get(key);
      if (idx === undefined) continue;
      const t = d.getTime();
      if (!best || t > best.t) best = { idx, t };
    }
    if (best) cellXOf.set(n.id, best.idx);
  }

  // Fallback: ingestion order, appended after the time buckets.
  const baseCount = sortedBuckets.length;
  let fallbackIdx = baseCount;
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (cellXOf.has(n.id)) continue;
    cellXOf.set(n.id, fallbackIdx);
    fallbackIdx++;
  }

  return { cellXOf };
}

// ---------------------------------------------------------------------------
// Relationship-depth plan (cellZ — focus parallax)
// ---------------------------------------------------------------------------

interface DepthPlan {
  /** nodeId → depth bucket (0 = in-focus, 1 = 1 hop out, 2 = far). */
  depthOf: Map<string, number>;
}

/**
 * Assign each node a relationship-depth bucket relative to `selectedNodeId`.
 *
 *  - depth 0: the focused node itself + its 1-hop neighbors (sit at the
 *    camera plane for the "focus and get more details" feel).
 *  - depth 1: 2-hop neighbors (drop back one Z-layer for parallax).
 *  - depth 2: everything else (background).
 *
 * When `selectedNodeId` is null/undefined or not in the graph, every node
 * gets depth 0 so the whole graph sits on one plane.
 */
function buildDepthPlan(
  nodes: KGNode[],
  edges: KGEdge[],
  selectedNodeId?: string | null,
): DepthPlan {
  const depthOf = new Map<string, number>();

  if (!selectedNodeId) {
    for (const n of nodes) depthOf.set(n.id, 0);
    return { depthOf };
  }

  const adjacency = new Map<string, string[]>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, []);
    if (!adjacency.has(e.target)) adjacency.set(e.target, []);
    adjacency.get(e.source)!.push(e.target);
    adjacency.get(e.target)!.push(e.source);
  }

  const oneHop = new Set<string>(adjacency.get(selectedNodeId) ?? []);
  oneHop.add(selectedNodeId);

  const twoHop = new Set<string>();
  for (const id of oneHop) {
    for (const nbr of adjacency.get(id) ?? []) {
      if (!oneHop.has(nbr)) twoHop.add(nbr);
    }
  }

  for (const n of nodes) {
    if (oneHop.has(n.id)) depthOf.set(n.id, 0);
    else if (twoHop.has(n.id)) depthOf.set(n.id, 1);
    else depthOf.set(n.id, 2);
  }
  return { depthOf };
}

// ---------------------------------------------------------------------------
// Image URL resolution
// ---------------------------------------------------------------------------

/** Pick the source filename for a photo node, if any. */
function photoFilename(node: KGNode): string | null {
  const p = node.properties ?? {};
  const f =
    (p.source_id as string | undefined) ??
    (p.file_path as string | undefined) ??
    (p.filename as string | undefined) ??
    (p.file_source as string | undefined);
  if (typeof f === 'string' && f.length > 0) return f;
  return null;
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Build the full canvas layout — a `CanvasNode[]` ready to hand to
 * `SceneManager.setNodes`.
 *
 * Layout axes (Phase 2 — knowledge-aware, see
 * `docs/infinite-canvas-report.html` §data):
 *
 *  - cellX = time bucket index (monotonic). New photos scroll in from +X.
 *  - cellY = cluster band: each hub's cluster occupies a stable, dense
 *    horizontal band derived from `buildClusterAssignment` (degree-based
 *    hubMap). Non-hub nodes inherit their hub's band so a cluster reads as a
 *    contiguous row.
 *  - cellZ = relationship depth from the focused node (0 = neighbors of the
 *    focused node, 1 = 2-hop, 2 = far; 0 when nothing is focused). This is the
 *    "focus and get more details" parallax depth called out in the report.
 *
 * Within a chunk cell, photos are spread on a square grid sized to the cell's
 * node count so they never overlap. Only photo/image nodes are rendered to
 * the canvas; non-photo entities (person/location/event/concept) still
 * participate in the cluster-band and depth computation (via their edges)
 * but do not get planes — they inform the layout, not the render set.
 *
 * @param nodes        all `KGNode`s in the graph (used for clustering + depth).
 * @param edges        all `KGEdge`s in the graph.
 * @param photoImages  `nodeId → thumbnail URL` for photo nodes.
 * @param personImages `nodeId → face-crop URL` for person nodes (reserved for
 *                     later phases; person nodes are not rendered on the canvas).
 * @param selectedNodeId optional focused node id — when set, its 1-hop
 *                     neighbors sit at `cellZ=0`, 2-hop at `cellZ=1`, and
 *                     everything else at `cellZ=2`. When unset, all nodes use
 *                     `cellZ=0`. This is the depth-of-field parallax axis.
 */
export function buildCanvasLayout(
  nodes: KGNode[],
  edges: KGEdge[],
  photoImages: Record<string, string>,
  _personImages: Record<string, string>,
  selectedNodeId?: string | null,
): CanvasNode[] {
  const timePlan = buildTimePlan(nodes, edges);
  const clusters = buildClusterAssignment(nodes, edges);
  const depthPlan = buildDepthPlan(nodes, edges, selectedNodeId);

  const bandCount = Math.max(1, clusters.bandOfHub.size);
  // Y-bands per chunk: cluster bands are dense (0..bandCount-1) and wrapped
  // into a compact vertical stack. Each band occupies one chunk-Y row.
  const yBandsPerChunk = Math.max(1, Math.min(bandCount, 4));

  const cellCount = new Map<string, number>();
  const provisional: {
    node: KGNode;
    kind: NodeKind;
    cellX: number;
    cellY: number;
    cellZ: number;
    cellKey: string;
  }[] = [];

  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    const kind = classifyKind(node);
    if (kind !== 'photo') continue;
    const cellX = timePlan.cellXOf.get(node.id) ?? i;

    const hubId = clusters.hubOf.get(node.id) ?? node.id;
    const band = clusters.bandOfHub.get(hubId) ?? 0;
    const cellY = band % yBandsPerChunk;

    const depth = depthPlan.depthOf.get(node.id) ?? 0;
    const cellZ = depth;

    const cellKey = `${cellX},${cellY},${cellZ}`;
    cellCount.set(cellKey, (cellCount.get(cellKey) ?? 0) + 1);
    provisional.push({ node, kind, cellX, cellY, cellZ, cellKey });
  }

  // Per-cell cursor so we can place nodes on a grid in deterministic order
  // (iteration order of `provisional` = input node order, which is stable).
  const cellCursor = new Map<string, number>();
  const out: CanvasNode[] = new Array(provisional.length);

  for (let i = 0; i < provisional.length; i++) {
    const p = provisional[i];
    const { node, kind, cellX, cellY, cellZ, cellKey } = p;

    const count = cellCount.get(cellKey) ?? 1;
    const idx = cellCursor.get(cellKey) ?? 0;
    cellCursor.set(cellKey, idx + 1);

    const side = Math.max(1, Math.ceil(Math.sqrt(count)));
    const slot = CHUNK_SIZE / side;
    const col = idx % side;
    const row = Math.floor(idx / side);
    const localX = col * slot + slot / 2;
    const localY = row * slot + slot / 2;

    // Per-node Z jitter within a cell so overlapping photos don't z-fight.
    const localZ = (idx % 3) * 10;

    const pw = node.properties?.width;
    const ph = node.properties?.height;
    let width: number;
    let height: number;
    if (typeof pw === 'number' && typeof ph === 'number' && pw > 0 && ph > 0) {
      const maxDim = 140;
      const scale = maxDim / Math.max(pw, ph);
      width = Math.round(pw * scale);
      height = Math.round(ph * scale);
    } else {
      width = 140;
      height = 105;
    }

    let imageUrl: string | undefined;
    let fullUrl: string | undefined;
    const cached = photoImages[node.id];
    if (cached) {
      imageUrl = cached;
    } else {
      const fname = photoFilename(node);
      if (fname) imageUrl = `${KG_API_BASE}${API.kg.photoImageThumb(fname, 512)}`;
    }
    const fname = photoFilename(node);
    if (fname) fullUrl = `${KG_API_BASE}${API.kg.photoImageFull(fname)}`;

    out[i] = {
      id: node.id,
      labels: node.labels,
      properties: node.properties,
      kind,
      imageUrl,
      fullUrl,
      cellX,
      cellY,
      cellZ,
      localX,
      localY,
      localZ,
      width,
      height,
    };
  }

  return out;
}