import * as THREE from 'three';

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

    this.textureLoader.load(
      url,
      (texture: THREE.Texture) => this.onDone(url, texture),
      undefined,
      (err: unknown) => {
        // On error the texture may still be partially loaded (or a default);
        // flush queued callbacks with whatever we have so requesters don't
        // hang forever, then drop the loaders entry.
        console.warn(`[TextureCache] failed to load texture: ${url}`, err);
        const tex = this.cache.get(url);
        this.flush(url, tex);
      },
    );

    return undefined;
  }

  private onDone(url: string, texture: THREE.Texture): void {
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
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

  clear(): void {
    for (const texture of this.cache.values()) {
      texture.dispose();
    }
    this.cache.clear();
    this.loaders.clear();
  }

  size(): number {
    return this.cache.size;
  }
}

export const textureCache = new TextureCache();