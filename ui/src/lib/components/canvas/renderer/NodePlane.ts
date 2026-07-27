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
  // Warm paper tone so note planes read as "text" and contrast with photos.
  note: 0xf5e9c8,
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
  /** Locally-baked text texture for `note` nodes (NOT routed through `textureCache`). */
  private _noteTexture?: THREE.CanvasTexture;

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
    } else if (node.kind === 'note' && node.textContent) {
      // Notes bake their text into a local CanvasTexture — never routed
      // through `textureCache` (no URL, no 404). Same text → same texture.
      const tex = this._createTextTexture(node.textContent);
      this._noteTexture = tex;
      this._material.map = tex;
      this._material.needsUpdate = true;
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
    if (this._noteTexture) {
      this._noteTexture.dispose();
      this._noteTexture = undefined;
    }
  }

  /**
   * Bake `text` into a `THREE.CanvasTexture` for a `note` plane. Draws
   * wrapped, ellipsis-truncated prose on a warm paper background sized to
   * the node's plane aspect. Deterministic: identical `text` + aspect
   * produces an identical texture. The texture is owned by this `NodePlane`
   * and disposed in {@link dispose} — it is NOT registered with `textureCache`.
   */
  private _createTextTexture(text: string): THREE.CanvasTexture {
    // Canvas resolution: square base, tall enough for wrapped prose. The
    // plane geometry is 1×1 scaled by (width, height), so the texture's
    // pixel aspect should match width/height to avoid stretching.
    const aspect = this._node.width > 0 && this._node.height > 0
      ? this._node.width / this._node.height
      : 1 / 1.4;
    const canvasH = 512;
    const canvasW = Math.max(128, Math.round(canvasH * aspect));
    const canvas = document.createElement('canvas');
    canvas.width = canvasW;
    canvas.height = canvasH;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      // Fallback: blank paper texture (no text) — still a valid texture.
      return new THREE.CanvasTexture(canvas);
    }

    // Warm paper background + subtle border so the plane reads as a card.
    ctx.fillStyle = '#f5e9c8';
    ctx.fillRect(0, 0, canvasW, canvasH);
    ctx.strokeStyle = '#d8c79a';
    ctx.lineWidth = 4;
    ctx.strokeRect(2, 2, canvasW - 4, canvasH - 4);

    // Readable sans-serif, sized relative to canvas height. Margin keeps
    // text off the border; line spacing ~1.32× the font size.
    const margin = 16;
    const fontPx = Math.max(16, Math.round(canvasH / 26));
    const lineHeight = Math.round(fontPx * 1.32);
    ctx.fillStyle = '#2b2620';
    ctx.font = `${fontPx}px sans-serif`;
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';

    const maxWidth = canvasW - margin * 2;
    const words = text.split(/\s+/).filter(Boolean);
    let line = '';
    let y = margin;
    const ellipsis = '…';
    for (let i = 0; i < words.length; i++) {
      const candidate = line ? `${line} ${words[i]}` : words[i];
      if (ctx.measureText(candidate).width <= maxWidth) {
        line = candidate;
        continue;
      }
      // Flush the current line if it fits.
      if (line) {
        ctx.fillText(line, margin, y);
        y += lineHeight;
        line = words[i];
      } else {
        // Single word too long — hard-break it to maxWidth.
        let chunk = words[i];
        while (chunk && ctx.measureText(chunk).width > maxWidth) {
          let cut = chunk.length - 1;
          while (cut > 0 && ctx.measureText(chunk.slice(0, cut)).width > maxWidth) {
            cut--;
          }
          if (cut <= 0) break;
          ctx.fillText(chunk.slice(0, cut), margin, y);
          y += lineHeight;
          chunk = chunk.slice(cut);
        }
        line = chunk;
      }
      // Stop once the next line would overflow the canvas; append ellipsis.
      if (y + lineHeight > canvasH - margin) {
        let last = line;
        // Try to fit an ellipsis on the final line; trim if needed.
        while (last && ctx.measureText(last + ellipsis).width > maxWidth && last.length > 0) {
          last = last.slice(0, -1);
        }
        ctx.fillText(last ? last + ellipsis : ellipsis, margin, y);
        line = '';
        y = canvasH;
        break;
      }
    }
    if (line && y + lineHeight <= canvasH - margin) {
      ctx.fillText(line, margin, y);
    }

    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.needsUpdate = true;
    return texture;
  }
}