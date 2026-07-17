/**
 * `ChunkManager` — mounts/unmounts `Chunk`s around the camera.
 *
 * On `setLayout`, nodes are bucketed by `chunkKey` into a `Map`. On each
 * `update`, the camera chunk `(cx,cy,cz)` is computed from `floor(pos /
 * CHUNK_SIZE)`; if it has changed since the last mount and the throttle
 * window has elapsed, chunks are remounted: every offset in `CHUNK_OFFSETS`
 * that has nodes is created and added to the scene, and chunks outside the
 * offset set are disposed and removed. Fade animation runs every frame on
 * already-mounted chunks regardless of throttle.
 */
import * as THREE from 'three';
import {
  CHUNK_OFFSETS,
  CHUNK_SIZE,
  getChunkUpdateThrottleMs,
} from './constants';
import { Chunk } from './Chunk';
import type { NodePlane } from './NodePlane';
import { chunkKey, type CanvasNode, type ChunkKey } from './types';

/**
 * Computes the chunk cell for a world position component.
 *
 * @param v - world coordinate.
 * @returns `floor(v / CHUNK_SIZE)`.
 */
function cellOf(v: number): number {
  return Math.floor(v / CHUNK_SIZE);
}

/**
 * Owns the mounted-chunk set and the node layout map. `SceneManager` drives
 * it with the camera position every frame.
 */
export class ChunkManager {
  private readonly _scene: THREE.Scene;
  private readonly _sharedGeometry: THREE.PlaneGeometry;
  private readonly _layout = new Map<ChunkKey, CanvasNode[]>();
  private readonly _mounted = new Map<ChunkKey, Chunk>();
  private _lastCameraChunk: ChunkKey | null = null;
  private _lastRemountTime = 0;
  private _lastVelocityMag = 0;

  /**
   * @param scene - the `THREE.Scene` to add/remove chunk groups to/from.
   * @param sharedGeometry - the global 1×1 `PlaneGeometry` (shared, not owned).
   */
  constructor(scene: THREE.Scene, sharedGeometry: THREE.PlaneGeometry) {
    this._scene = scene;
    this._sharedGeometry = sharedGeometry;
  }

  /**
   * Replaces the full node layout. Nodes are bucketed by chunk key. Any
   * currently mounted chunks whose layout entry disappeared are unmounted.
   *
   * @param nodes - all canvas nodes.
   */
  setLayout(nodes: CanvasNode[]): void {
    this._layout.clear();
    for (const node of nodes) {
      const key = chunkKey(node.cellX, node.cellY, node.cellZ);
      const bucket = this._layout.get(key);
      if (bucket) {
        bucket.push(node);
      } else {
        this._layout.set(key, [node]);
      }
    }

    for (const [key, chunk] of this._mounted) {
      if (!this._layout.has(key)) {
        this._scene.remove(chunk.group);
        chunk.dispose();
        this._mounted.delete(key);
      } else {
        chunk.setNodes(this._layout.get(key) ?? []);
      }
    }

    this._lastCameraChunk = null;
  }

  /**
   * Per-frame update. Computes the camera chunk; if it changed and the
   * throttle window has elapsed, remounts chunks around the new chunk.
   * Always animates fade on mounted chunks.
   *
   * @param cameraPos - current camera world position.
   * @param velocityMag - magnitude of camera velocity (drives throttle).
   */
  update(cameraPos: THREE.Vector3, velocityMag = 0): void {
    const cx = cellOf(cameraPos.x);
    const cy = cellOf(cameraPos.y);
    const cz = cellOf(cameraPos.z);
    const key = chunkKey(cx, cy, cz);

    this._lastVelocityMag = velocityMag;

    const changed = this._lastCameraChunk !== key;
    const now = performance.now();
    const throttleMs = getChunkUpdateThrottleMs(velocityMag);
    const throttleElapsed = now - this._lastRemountTime >= throttleMs;

    if (changed && throttleElapsed) {
      this.remount(cx, cy, cz);
      this._lastCameraChunk = key;
      this._lastRemountTime = now;
    }

    for (const chunk of this._mounted.values()) {
      chunk.updateFade(cameraPos);
    }
  }

  /** Number of chunks currently mounted in the scene. */
  get mountedChunkCount(): number {
    return this._mounted.size;
  }

  /**
   * Returns the meshes of all currently mounted chunk planes for raycasting.
   * Fresh array each call — the mounted set changes as the camera moves.
   */
  getPickableMeshes(): THREE.Mesh[] {
    const meshes: THREE.Mesh[] = [];
    for (const chunk of this._mounted.values()) {
      for (const plane of chunk.planes) {
        meshes.push(plane.mesh);
      }
    }
    return meshes;
  }

  /**
   * Finds the `NodePlane` for `nodeId` among the mounted chunks, or
   * `undefined` if the node's chunk is not currently mounted.
   */
  findPlaneByNodeId(nodeId: string): NodePlane | undefined {
    for (const chunk of this._mounted.values()) {
      for (const plane of chunk.planes) {
        if (plane.node.id === nodeId) return plane;
      }
    }
    return undefined;
  }

  /**
   * Remounts chunks around `(cx, cy, cz)`. For each `CHUNK_OFFSETS` entry, if
   * a layout bucket exists, a `Chunk` is created (or reused) and added to the
   * scene; chunks outside the new offset set are disposed and removed.
   */
  private remount(cx: number, cy: number, cz: number): void {
    const wanted = new Set<ChunkKey>();

    for (const off of CHUNK_OFFSETS) {
      const tx = cx + off.dx;
      const ty = cy + off.dy;
      const tz = cz + off.dz;
      const key = chunkKey(tx, ty, tz);
      if (!this._layout.has(key)) continue;
      wanted.add(key);

      let chunk = this._mounted.get(key);
      if (!chunk) {
        chunk = new Chunk(tx, ty, tz, this._sharedGeometry);
        chunk.setNodes(this._layout.get(key) ?? []);
        this._scene.add(chunk.group);
        this._mounted.set(key, chunk);
      }
    }

    for (const [key, chunk] of this._mounted) {
      if (!wanted.has(key)) {
        this._scene.remove(chunk.group);
        chunk.dispose();
        this._mounted.delete(key);
      }
    }
  }

  /** Disposes all mounted chunks (the shared geometry is owned by the caller). */
  dispose(): void {
    for (const chunk of this._mounted.values()) {
      this._scene.remove(chunk.group);
      chunk.dispose();
    }
    this._mounted.clear();
    this._layout.clear();
    this._lastCameraChunk = null;
  }
}