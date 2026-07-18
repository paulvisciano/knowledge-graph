/**
 * `NodePlane` — a single node rendered as a 3D plane on the infinite canvas.
 *
 * Wraps a `THREE.Mesh` sharing a global 1×1 `PlaneGeometry` (scaled per node),
 * a `MeshBasicMaterial` with per-frame opacity fade, and the texture-LOD
 * wiring for photo nodes (via the shared `textureCache`). Non-photo nodes get
 * a flat colored material for Phase 1; label text is Phase 2.
 */
import * as THREE from 'three';
import { textureCache } from '$lib/services/TextureCache';
import {
  CHUNK_FADE_MARGIN,
  CHUNK_SIZE,
  DEPTH_FADE_END,
  DEPTH_FADE_START,
  INVIS_THRESHOLD,
  LOD_FULL_CHEBY,
  LOD_FULL_DEPTH,
  LOD_FULL_DEPTH_HYSTERESIS,
  LOD_HYSTERESIS,
  RENDER_DISTANCE,
} from './constants';
import type { CanvasNode, NodeKind } from './types';

/** Per-kind base colors for non-photo nodes (Phase 1 flat materials). */
const KIND_COLOR: Record<NodeKind, number> = {
  photo: 0xffffff,
  person: 0x4a9eff,
  location: 0x39d98a,
  event: 0xf6c344,
  concept: 0xb57bff,
};

/** Lerp factor for smoothing current opacity toward the per-frame target. */
const OPACITY_LERP = 0.18;

/**
 * Renders one `CanvasNode` as a textured/colored plane on the canvas.
 *
 * The mesh is positioned at `chunkOrigin + (localX, localY, localZ)` and
 * scaled to `(node.width, node.height, 1)` against the shared 1×1 geometry.
 * Photo nodes load their thumbnail through `textureCache`; non-photo nodes
 * use a flat colored material.
 */
export class NodePlane {
  private readonly _node: CanvasNode;
  private readonly _mesh: THREE.Mesh;
  private readonly _material: THREE.MeshBasicMaterial;
  private _currentOpacity = 1;
  private _disposed = false;
  private _currentLod: 'thumb' | 'full' = 'thumb';
  private _fullUrl?: string;
  private _thumbUrl?: string;
  private _fullEvictCb?: () => void;

  /**
   * @param node - the canvas node to render.
   * @param sharedGeometry - the global 1×1 `PlaneGeometry` (shared, not owned).
   */
  constructor(node: CanvasNode, sharedGeometry: THREE.PlaneGeometry) {
    this._node = node;
    this._material = new THREE.MeshBasicMaterial({
      color: KIND_COLOR[node.kind],
      transparent: true,
      depthWrite: true,
      side: THREE.DoubleSide,
    });
    this._mesh = new THREE.Mesh(sharedGeometry, this._material);
    this._mesh.scale.set(node.width, node.height, 1);
    this._mesh.position.set(node.localX, node.localY, node.localZ);
    this._mesh.userData.nodeId = node.id;

    if (node.kind === 'photo' && node.imageUrl) {
      this._thumbUrl = node.imageUrl;
      this._fullUrl = node.fullUrl;
      const cached = textureCache.load(node.imageUrl, (t) => this.applyTexture(t));
      if (cached) {
        this.applyTexture(cached);
      }
    }
  }

  /** The plane mesh — added to a chunk `THREE.Group` by `Chunk`. */
  get mesh(): THREE.Mesh {
    return this._mesh;
  }

  /** The canvas node this plane renders. */
  get node(): CanvasNode {
    return this._node;
  }

  /**
   * Per-frame fade update. Computes the Chebyshev distance from the camera
   * chunk to this node's chunk, derives a target opacity (1 inside
   * `RENDER_DISTANCE`, a ramp out to the fade margin, 0 beyond), lerps the
   * current opacity toward it, and toggles `mesh.visible` /
   * `material.depthWrite` based on `INVIS_THRESHOLD` and the depth fade.
   *
   * @param cameraPos - current camera world position.
   * @param chunkOrigin - world-space origin of the owning chunk.
   */
  updateFade(cameraPos: THREE.Vector3, chunkOrigin: THREE.Vector3): void {
    if (this._disposed) return;

    const worldPos = this._mesh.position;
    const nodeWorldX = chunkOrigin.x + worldPos.x;
    const nodeWorldY = chunkOrigin.y + worldPos.y;
    const nodeWorldZ = chunkOrigin.z + worldPos.z;

    const camChunkX = Math.floor(cameraPos.x / CHUNK_SIZE);
    const camChunkY = Math.floor(cameraPos.y / CHUNK_SIZE);
    const camChunkZ = Math.floor(cameraPos.z / CHUNK_SIZE);

    const nodeChunkX = Math.floor(nodeWorldX / CHUNK_SIZE);
    const nodeChunkY = Math.floor(nodeWorldY / CHUNK_SIZE);
    const nodeChunkZ = Math.floor(nodeWorldZ / CHUNK_SIZE);

    const cheby = Math.max(
      Math.abs(nodeChunkX - camChunkX),
      Math.abs(nodeChunkY - camChunkY),
      Math.abs(nodeChunkZ - camChunkZ),
    );

    const gridFade =
      cheby <= RENDER_DISTANCE
        ? 1
        : Math.max(0, 1 - (cheby - RENDER_DISTANCE) / Math.max(CHUNK_FADE_MARGIN, 0.0001));

    const absDepth = Math.abs(cameraPos.z - nodeWorldZ);
    const depthFade =
      absDepth <= DEPTH_FADE_START
        ? 1
        : Math.max(0, 1 - (absDepth - DEPTH_FADE_START) / Math.max(DEPTH_FADE_END - DEPTH_FADE_START, 0.0001));

    // Reference repo formula: opacity = min(gridFade, depthFade²). The squared
    // depth term makes far planes fall off faster than grid distance alone.
    const targetOpacity = Math.min(gridFade, depthFade * depthFade);

    this._currentOpacity += (targetOpacity - this._currentOpacity) * OPACITY_LERP;

    if (this._currentOpacity < INVIS_THRESHOLD) {
      this._mesh.visible = false;
      this._material.depthWrite = false;
    } else {
      this._mesh.visible = true;
      this._material.depthWrite = absDepth <= DEPTH_FADE_START;
    }
    this._material.opacity = this._currentOpacity;
    this._material.needsUpdate = true;
  }

  /**
   * Applies a texture (from `textureCache`) to the material. The texture's
   * color space is assumed already set by the cache; we only wire it in.
   *
   * @param texture - the loaded texture.
   */
  applyTexture(texture: THREE.Texture): void {
    if (this._disposed) return;
    this._material.map = texture;
    this._material.color.setHex(0xffffff);
    this._material.needsUpdate = true;
  }

  updateLod(cameraPos: THREE.Vector3, chunkOrigin: THREE.Vector3): void {
    if (this._disposed) return;
    if (this._node.kind !== 'photo' || !this._thumbUrl) return;
    if (!this._fullUrl) return;

    const worldPos = this._mesh.position;
    const nodeWorldX = chunkOrigin.x + worldPos.x;
    const nodeWorldY = chunkOrigin.y + worldPos.y;
    const nodeWorldZ = chunkOrigin.z + worldPos.z;

    const camChunkX = Math.floor(cameraPos.x / CHUNK_SIZE);
    const camChunkY = Math.floor(cameraPos.y / CHUNK_SIZE);
    const camChunkZ = Math.floor(cameraPos.z / CHUNK_SIZE);

    const nodeChunkX = Math.floor(nodeWorldX / CHUNK_SIZE);
    const nodeChunkY = Math.floor(nodeWorldY / CHUNK_SIZE);
    const nodeChunkZ = Math.floor(nodeWorldZ / CHUNK_SIZE);

    const cheby = Math.max(
      Math.abs(nodeChunkX - camChunkX),
      Math.abs(nodeChunkY - camChunkY),
      Math.abs(nodeChunkZ - camChunkZ),
    );
    const absDepth = Math.abs(cameraPos.z - nodeWorldZ);

    const demoteThreshold = LOD_FULL_CHEBY + LOD_HYSTERESIS;
    const demoteDepth = LOD_FULL_DEPTH + LOD_FULL_DEPTH_HYSTERESIS;
    const shouldFull = cheby <= LOD_FULL_CHEBY && absDepth <= LOD_FULL_DEPTH;
    const shouldThumb = cheby > demoteThreshold || absDepth > demoteDepth;

    if (this._currentLod === 'thumb' && shouldFull) {
      this._promoteToFull();
    } else if (this._currentLod === 'full' && shouldThumb) {
      this._demoteToThumb();
    }
  }

  private _promoteToFull(): void {
    if (!this._fullUrl) return;
    this._currentLod = 'full';
    const onEvicted = () => {
      this._currentLod = 'thumb';
      this._fullEvictCb = undefined;
      const thumb = this._thumbUrl ? textureCache.get(this._thumbUrl) : undefined;
      if (thumb) this.applyTexture(thumb);
    };
    this._fullEvictCb = onEvicted;
    textureCache.requestFullRes(this._fullUrl, (t) => this.applyTexture(t), onEvicted);
  }

  private _demoteToThumb(): void {
    this._currentLod = 'thumb';
    if (this._fullUrl && this._fullEvictCb) {
      textureCache.releaseFullRes(this._fullUrl, this._fullEvictCb);
      this._fullEvictCb = undefined;
    }
    const thumb = this._thumbUrl ? textureCache.get(this._thumbUrl) : undefined;
    if (thumb) this.applyTexture(thumb);
  }

  /** Releases the material (the geometry is shared and not disposed here). */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    if (this._fullUrl && this._fullEvictCb) {
      textureCache.releaseFullRes(this._fullUrl, this._fullEvictCb);
      this._fullEvictCb = undefined;
    }
    this._material.map = null;
    this._material.dispose();
  }
}