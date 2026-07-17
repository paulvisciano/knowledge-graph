/**
 * `Chunk` — a `THREE.Group` owning the `NodePlane`s for one chunk cell.
 *
 * The chunk origin is `cell * CHUNK_SIZE` along each axis; each child
 * `NodePlane` is positioned at `chunkOrigin + (localX, localY, localZ)` so
 * the group can be added/removed from the scene as a unit by `ChunkManager`.
 */
import * as THREE from 'three';
import { CHUNK_SIZE } from './constants';
import { NodePlane } from './NodePlane';
import { chunkKey, type CanvasNode, type ChunkKey } from './types';

/**
 * Owns the `NodePlane`s for a single chunk cell and exposes a `THREE.Group`
 * that `ChunkManager` mounts/unmounts from the scene.
 */
export class Chunk {
  private readonly _cellX: number;
  private readonly _cellY: number;
  private readonly _cellZ: number;
  private readonly _chunkOrigin: THREE.Vector3;
  private readonly _group: THREE.Group;
  private readonly _sharedGeometry: THREE.PlaneGeometry;
  private readonly _planes: NodePlane[] = [];
  private _disposed = false;

  /**
   * @param cellX - chunk-space X.
   * @param cellY - chunk-space Y.
   * @param cellZ - chunk-space Z.
   * @param sharedGeometry - the global 1×1 `PlaneGeometry` (shared, not owned).
   */
  constructor(
    cellX: number,
    cellY: number,
    cellZ: number,
    sharedGeometry: THREE.PlaneGeometry,
  ) {
    this._cellX = cellX;
    this._cellY = cellY;
    this._cellZ = cellZ;
    this._sharedGeometry = sharedGeometry;
    this._chunkOrigin = new THREE.Vector3(
      cellX * CHUNK_SIZE,
      cellY * CHUNK_SIZE,
      cellZ * CHUNK_SIZE,
    );
    this._group = new THREE.Group();
    this._group.position.copy(this._chunkOrigin);
  }

  /** The `THREE.Group` to add/remove from the scene. */
  get group(): THREE.Group {
    return this._group;
  }

  /** Canonical `${cellX},${cellY},${cellZ}` key. */
  get key(): ChunkKey {
    return chunkKey(this._cellX, this._cellY, this._cellZ);
  }

  /** Chunk-space X coordinate. */
  get cellX(): number {
    return this._cellX;
  }

  /** Chunk-space Y coordinate. */
  get cellY(): number {
    return this._cellY;
  }

  /** Chunk-space Z coordinate. */
  get cellZ(): number {
    return this._cellZ;
  }

  /** World-space origin of this chunk. */
  get chunkOrigin(): THREE.Vector3 {
    return this._chunkOrigin;
  }

  /** The `NodePlane`s owned by this chunk (read-only view). */
  get planes(): readonly NodePlane[] {
    return this._planes;
  }

  /**
   * (Re)builds the `NodePlane`s for this chunk from a node list. Disposes any
   * previously built planes first.
   *
   * @param nodes - nodes assigned to this chunk.
   */
  setNodes(nodes: CanvasNode[]): void {
    for (const plane of this._planes) {
      this._group.remove(plane.mesh);
      plane.dispose();
    }
    this._planes.length = 0;

    for (const node of nodes) {
      const plane = new NodePlane(node, this._sharedGeometry);
      this._planes.push(plane);
      this._group.add(plane.mesh);
    }
  }

  /**
   * Per-frame fade update — delegates to each `NodePlane.updateFade` with the
   * chunk origin so planes compute their world position correctly.
   *
   * @param cameraPos - current camera world position.
   */
  updateFade(cameraPos: THREE.Vector3): void {
    for (const plane of this._planes) {
      plane.updateFade(cameraPos, this._chunkOrigin);
    }
  }

  /** Disposes all owned `NodePlane`s and clears the group. */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    for (const plane of this._planes) {
      this._group.remove(plane.mesh);
      plane.dispose();
    }
    this._planes.length = 0;
  }
}