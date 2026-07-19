import * as THREE from 'three';
import { LOD_FULL_MAX } from '$lib/components/canvas/renderer/constants';

/**
 * Global texture cache keyed by image URL. Ports the reference repo's
 * texture-manager.ts pattern: a shared `Map<url, THREE.Texture>` plus a
 * per-key callback set so multiple requesters are notified when a single
 * in-flight load completes. Textures are loaded on demand via
 * `THREE.TextureLoader` with sRGB color space and anisotropic filtering.
 *
 * The cache is keyed by URL (not nodeId) so multiple nodes that share the
 * same image reuse a single GPU texture across graph refreshes.
 */
class TextureCache {
  private cache = new Map<string, THREE.Texture>();
  private loaders = new Map<string, Set<(t: THREE.Texture) => void>>();
  private textureLoader = new THREE.TextureLoader();
  private fullResLru = new Map<string, Set<() => void>>();
  /**
   * In-flight HTMLImageElements keyed by URL. Three's `ImageLoader` creates
   * an `<img>` per URL and assigns `image.src = url`, which is what actually
   * holds the browser HTTP/1.1 connection slot. Setting `src = ''` on these
   * cancels the pending fetch and frees the connection for other consumers
   * (e.g. the Ingestion tab's `/documents/paginated` request).
   */
  private inFlightImages = new Map<string, HTMLImageElement>();

  /** Returns the cached texture for `url`, or `undefined` if not yet loaded. */
  get(url: string): THREE.Texture | undefined {
    return this.cache.get(url);
  }

  /**
   * Returns the cached texture if present (calling `onLoad` synchronously),
   * otherwise kicks off (or joins) an in-flight load for `url` and returns
   * `undefined`. When the load completes, `onLoad` is invoked with the
   * texture — callers use it to swap the texture onto an already-created
   * material.
   */
  load(url: string, onLoad?: (t: THREE.Texture) => void): THREE.Texture | undefined {
    const cached = this.cache.get(url);
    if (cached) {
      onLoad?.(cached);
      return cached;
    }

    // Join an in-flight load if one exists for this URL.
    const queued = this.loaders.get(url);
    if (queued) {
      if (onLoad) queued.add(onLoad);
      return undefined;
    }

    // Start a new load. Register the callback set first so an immediate
    // (sync) load completion still has a place to flush.
    const callbacks = new Set<(t: THREE.Texture) => void>();
    if (onLoad) callbacks.add(onLoad);
    this.loaders.set(url, callbacks);

    const texture = this.textureLoader.load(
      url,
      (t: THREE.Texture) => this.onDone(url, t),
      undefined,
      (err: unknown) => {
        // On error the texture may still be partially loaded (or a default);
        // flush queued callbacks with whatever we have so requesters don't
        // hang forever, then drop the loaders entry.
        console.warn(`[TextureCache] failed to load texture: ${url}`, err);
        this.inFlightImages.delete(url);
        const tex = this.cache.get(url);
        this.flush(url, tex);
      },
    );

    // `TextureLoader.load` returns synchronously with a Texture whose
    // `.image` is the HTMLImageElement created by ImageLoader. Track it so
    // `abortInFlight()` can cancel the underlying fetch.
    const img = texture.image as HTMLImageElement | undefined;
    if (img && typeof img === 'object' && 'src' in img) {
      this.inFlightImages.set(url, img);
    }

    return undefined;
  }

  private onDone(url: string, texture: THREE.Texture): void {
    this.inFlightImages.delete(url);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.generateMipmaps = true;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.magFilter = THREE.LinearFilter;
    texture.anisotropy = 8;
    texture.needsUpdate = true;
    this.cache.set(url, texture);
    this.flush(url, texture);
  }

  private flush(url: string, texture: THREE.Texture | undefined): void {
    const callbacks = this.loaders.get(url);
    if (callbacks) {
      for (const cb of callbacks) {
        if (texture) cb(texture);
      }
      this.loaders.delete(url);
    }
  }

  has(url: string): boolean {
    return this.cache.has(url);
  }

  /**
   * Requests a full-res texture for `url`. If cached, `onLoaded` fires sync
   * and `onEvicted` is registered for later LRU eviction. If not cached, a
   * load is kicked off; `onLoaded` fires when it completes. When adding a new
   * LRU entry exceeds `LOD_FULL_MAX`, the LRU (oldest) entry is evicted: all
   * its `onEvicted` callbacks fire, and the texture is disposed + removed.
   */
  requestFullRes(
    url: string,
    onLoaded: (t: THREE.Texture) => void,
    onEvicted: () => void,
  ): void {
    const existing = this.cache.get(url);
    if (existing) {
      const prevCbs = this.fullResLru.get(url);
      this.fullResLru.delete(url);
      const cbs = prevCbs ?? new Set<() => void>();
      cbs.add(onEvicted);
      this.fullResLru.set(url, cbs);
      onLoaded(existing);
      return;
    }

    const prevCbs = this.fullResLru.get(url);
    const cbs = prevCbs ?? new Set<() => void>();
    cbs.add(onEvicted);
    this.fullResLru.delete(url);
    this.fullResLru.set(url, cbs);

    if (this.fullResLru.size > LOD_FULL_MAX) {
      const oldestUrl = this.fullResLru.keys().next().value;
      if (oldestUrl !== undefined && oldestUrl !== url) {
        this._evictFullRes(oldestUrl);
      }
    }

    this.load(url, (t) => {
      onLoaded(t);
    });
  }

  /** Deregisters a plane's `onEvicted` callback. If no callbacks remain, evicts. */
  releaseFullRes(url: string, onEvicted: () => void): void {
    const cbs = this.fullResLru.get(url);
    if (!cbs) return;
    cbs.delete(onEvicted);
    if (cbs.size === 0) {
      this._evictFullRes(url);
    }
  }

  private _evictFullRes(url: string): void {
    const cbs = this.fullResLru.get(url);
    if (cbs) {
      for (const cb of cbs) cb();
      this.fullResLru.delete(url);
    }
    const tex = this.cache.get(url);
    if (tex) {
      tex.dispose();
      this.cache.delete(url);
    }
  }

  clear(): void {
    for (const texture of this.cache.values()) {
      texture.dispose();
    }
    this.cache.clear();
    this.loaders.clear();
    this.fullResLru.clear();
    this.inFlightImages.clear();
  }

  /**
   * Cancels all in-flight texture image fetches and frees the browser
   * HTTP/1.1 connection slots they were holding. Used when the canvas is
   * unmounted (e.g. switching to the Ingest tab) so its pending image loads
   * don't starve other consumers' fetches for tens of seconds.
   */
  abortInFlight(): void {
    for (const img of this.inFlightImages.values()) {
      img.src = '';
    }
    this.inFlightImages.clear();
  }

  size(): number {
    return this.cache.size;
  }
}

export const textureCache = new TextureCache();