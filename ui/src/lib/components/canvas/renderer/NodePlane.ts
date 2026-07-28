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
import { getProvider } from './NodeKindProvider';
import './providers'; // side-effect: registers all providers so getProvider works
import type { CanvasNode } from './types';

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
  /** Whether LOD thumb→full promotion is enabled (from planeConfig). */
  private _lodEnabled = false;
  /** Locally-baked text texture for text-source nodes (NOT routed through `textureCache`). */
  private _noteTexture?: THREE.CanvasTexture;
  private _hoverTexture?: THREE.CanvasTexture;
  private _hovered = false;

  /**
   * @param node - the canvas node to render.
   * @param sharedGeometry - the global 1×1 `PlaneGeometry` (shared, not owned).
   */
  constructor(node: CanvasNode, sharedGeometry: THREE.PlaneGeometry) {
    this._node = node;
    const provider = getProvider(this._node.kind);
    const planeConfig = provider?.planeConfig;
    this._material = new THREE.MeshBasicMaterial({
      color: planeConfig?.color ?? 0xb57bff,
      transparent: true,
      depthWrite: true,
      side: THREE.DoubleSide,
    });
    this._mesh = new THREE.Mesh(sharedGeometry, this._material);
    this._mesh.scale.set(node.width, node.height, 1);
    this._mesh.position.set(node.localX, node.localY, node.localZ);
    this._mesh.userData.nodeId = node.id;

    this._lodEnabled = planeConfig?.lodEnabled ?? false;

    const textureSource = planeConfig?.textureSource ?? 'none';
    if (textureSource === 'url' && node.imageUrl) {
      this._thumbUrl = node.imageUrl;
      this._fullUrl = node.fullUrl;
      const cached = textureCache.load(node.imageUrl, (t) => this.applyTexture(t));
      if (cached) {
        this.applyTexture(cached);
      }
    } else if (textureSource === 'text' && node.textContent) {
      const isConv = this._node.kind === 'conversation';
      const tex = isConv
        ? this._createConversationTexture(false)
        : this._createTextTexture(node.textContent);
      this._noteTexture = tex;
      this._material.map = tex;
      // A colored material tint MULTIPLIES the texture (color * map.rgb).
      // Reset to white so the baked texture renders at full brightness.
      this._material.color.set(0xffffff);
      this._material.needsUpdate = true;
      if (isConv) {
        this._hoverTexture = this._createConversationTexture(true);
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

  setHovered(hovered: boolean): void {
    if (this._hovered === hovered) return;
    this._hovered = hovered;
    if (this._hoverTexture && this._noteTexture) {
      this._material.map = hovered ? this._hoverTexture : this._noteTexture;
      this._material.needsUpdate = true;
    }
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
    if (!this._lodEnabled || !this._thumbUrl) return;
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
    if (this._hoverTexture) {
      this._hoverTexture.dispose();
      this._hoverTexture = undefined;
    }
  }

  /** Bake a glass-blue conversation card texture matching the prototype's
   *  `.graph-node.chat` visual: accent-blue kicker, bright title, muted foot,
   *  dark glass background. Draws from `node.properties` (title + createdAt)
   *  so the preview is built without eagerly loading messages. */
  private _createConversationTexture(hovered: boolean): THREE.CanvasTexture {
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
      return new THREE.CanvasTexture(canvas);
    }

    const p = this._node.properties ?? {};
    const title = (p.name as string) ?? (p.title as string) ?? this._node.id;
    const createdAt = typeof p.createdAt === 'number' ? p.createdAt : null;
    const dateLabel = createdAt !== null
      ? new Date(createdAt > 1e12 ? createdAt : createdAt * 1000).toLocaleDateString(undefined, {
          year: 'numeric', month: 'short', day: 'numeric',
        })
      : '';

    const accent = '#13dcf6';
    const fg = '#dbdee1';
    const muted = '#87909c';
    const faint = '#6a727d';
    const padX = 16;
    const maxTextWidth = canvasW - padX * 2;
    const radius = 14;

    ctx.fillStyle = hovered ? '#243049' : '#1e2a42';
    ctx.beginPath();
    ctx.moveTo(radius, 0);
    ctx.arcTo(canvasW, 0, canvasW, radius, radius);
    ctx.arcTo(canvasW, canvasH, canvasW - radius, canvasH, radius);
    ctx.arcTo(0, canvasH, 0, canvasH - radius, radius);
    ctx.arcTo(0, 0, radius, 0, radius);
    ctx.closePath();
    ctx.fill();

    if (hovered) {
      ctx.shadowColor = accent;
      ctx.shadowBlur = 32;
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2;
    } else {
      ctx.strokeStyle = 'rgba(19,220,246,0.14)';
      ctx.lineWidth = 1;
    }
    ctx.stroke();
    ctx.shadowBlur = 0;

    const topGrad = ctx.createLinearGradient(0, 0, canvasW, 0);
    topGrad.addColorStop(0, 'rgba(19,220,246,0)');
    topGrad.addColorStop(0.5, accent);
    topGrad.addColorStop(1, 'rgba(19,220,246,0)');
    ctx.fillStyle = topGrad;
    ctx.globalAlpha = 0.7;
    ctx.fillRect(0, 0, canvasW, 3);
    ctx.globalAlpha = 1;

    const kickerFont = Math.max(14, Math.round(canvasH / 38));
    const kickerY = 14;
    const dotR = 3;
    ctx.fillStyle = accent;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.arc(padX + dotR, kickerY + kickerFont * 0.5, dotR, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.font = `600 ${kickerFont}px ui-monospace, "SF Mono", Menlo, monospace`;
    ctx.fillStyle = accent;
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    ctx.fillText('CONVERSATION', padX + dotR * 2 + 6, kickerY);

    const queryFont = Math.max(18, Math.round(canvasH / 24));
    ctx.font = `700 ${queryFont}px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif`;
    ctx.fillStyle = fg;
    const queryY = kickerY + kickerFont + 6;
    const queryLineHeight = Math.round(queryFont * 1.28);
    this._drawWrapped(ctx, title, padX, queryY, maxTextWidth, queryLineHeight, 2);

    const footFont = Math.max(9, Math.round(canvasH / 48));
    const footY = canvasH - 12 - footFont;
    ctx.strokeStyle = 'rgba(88,100,116,0.08)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padX, footY - 8);
    ctx.lineTo(canvasW - padX, footY - 8);
    ctx.stroke();

    ctx.font = `700 ${footFont}px ui-monospace, "SF Mono", Menlo, monospace`;
    ctx.fillStyle = accent;
    ctx.fillText('CHAT', padX, footY);

    ctx.font = `${footFont}px ui-monospace, "SF Mono", Menlo, monospace`;
    ctx.fillStyle = faint;
    if (dateLabel) {
      const footRight = dateLabel.toUpperCase();
      const rightWidth = ctx.measureText(footRight).width;
      ctx.fillText(footRight, canvasW - padX - rightWidth, footY);
    }

    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.needsUpdate = true;
    return texture;
  }

  /** Word-wrap helper for the conversation card. Draws up to `maxLines` lines,
   *  ellipsis-truncating the final line if it overflows. */
  private _drawWrapped(
    ctx: CanvasRenderingContext2D,
    text: string,
    x: number,
    y: number,
    maxWidth: number,
    lineHeight: number,
    maxLines: number,
  ): void {
    const words = text.split(/\s+/).filter(Boolean);
    let line = '';
    let yy = y;
    let lineIdx = 0;
    for (let i = 0; i < words.length; i++) {
      const candidate = line ? `${line} ${words[i]}` : words[i];
      if (ctx.measureText(candidate).width <= maxWidth) {
        line = candidate;
        continue;
      }
      if (lineIdx === maxLines - 1) {
        // Last allowed line — ellipsis-truncate.
        let last = line ? `${line} ${words[i]}` : words[i];
        while (last && ctx.measureText(last + '…').width > maxWidth && last.length > 0) {
          last = last.slice(0, -1);
        }
        ctx.fillText(last + '…', x, yy);
        return;
      }
      if (line) {
        ctx.fillText(line, x, yy);
        yy += lineHeight;
        lineIdx++;
        line = words[i];
      } else {
        // Single word too long — hard break.
        let chunk = words[i];
        while (chunk && ctx.measureText(chunk).width > maxWidth) {
          let cut = chunk.length - 1;
          while (cut > 0 && ctx.measureText(chunk.slice(0, cut)).width > maxWidth) cut--;
          if (cut <= 0) return;
          ctx.fillText(chunk.slice(0, cut), x, yy);
          yy += lineHeight;
          lineIdx++;
          if (lineIdx >= maxLines) return;
          chunk = chunk.slice(cut);
        }
        line = chunk;
      }
    }
    if (line) ctx.fillText(line, x, yy);
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