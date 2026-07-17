/**
 * Type definitions for the infinite-canvas renderer.
 *
 * `CanvasNode` is the renderer-facing projection of a `KGNode`: it carries
 * the graph identity plus the spatial layout (chunk + local offset) and the
 * image-LOD URLs needed by `NodePlane`. `Layout.ts` (owned by Agent B) is
 * responsible for turning `KGNode[]` into `CanvasNode[]`.
 */
import type { KGNode } from '$lib/constants';

/** High-level visual category of a canvas node. */
export type NodeKind = 'photo' | 'person' | 'location' | 'event' | 'concept';

/**
 * A node projected into canvas space. Mirrors `KGNode` identity but adds the
 * spatial assignment (chunk coords + local offset) and image-LOD URLs that
 * the renderer needs.
 */
export interface CanvasNode {
  /** Original `KGNode.id`. */
  readonly id: string;
  /** Original `KGNode.labels` (Neo4j labels). */
  readonly labels: string[];
  /** Original `KGNode.properties`. */
  readonly properties: Record<string, unknown>;
  /** Visual category — drives material color and texture loading. */
  readonly kind: NodeKind;
  /** Thumbnail URL (LOD) — loaded eagerly by `NodePlane`. */
  readonly imageUrl?: string;
  /** Full-res URL — swapped in on hover/select (Phase 2). */
  readonly fullUrl?: string;
  /** Chunk-space X coordinate (cellX = floor(worldX / CHUNK_SIZE)). */
  readonly cellX: number;
  /** Chunk-space Y coordinate. */
  readonly cellY: number;
  /** Chunk-space Z coordinate. */
  readonly cellZ: number;
  /** World-unit X offset within the chunk (0..CHUNK_SIZE). */
  readonly localX: number;
  /** World-unit Y offset within the chunk. */
  readonly localY: number;
  /** World-unit Z offset within the chunk. */
  readonly localZ: number;
  /** Plane width in world units. */
  readonly width: number;
  /** Plane height in world units. */
  readonly height: number;
}

/** String key uniquely identifying a chunk: `${cellX},${cellY},${cellZ}`. */
export type ChunkKey = string;

/**
 * Builds the canonical string key for a chunk cell.
 *
 * @param cx - chunk-space X.
 * @param cy - chunk-space Y.
 * @param cz - chunk-space Z.
 * @returns the `${cx},${cy},${cz}` key.
 */
export function chunkKey(cx: number, cy: number, cz: number): ChunkKey {
  return `${cx},${cy},${cz}`;
}