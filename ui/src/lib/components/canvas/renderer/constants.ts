/**
 * Constants for the infinite-canvas renderer.
 *
 * Ported from the reference repo `edoardolunardi/infinite-canvas`
 * (commit 528d811) `src/infinite-canvas/constants.ts`, adapted to plain
 * TypeScript with `CHUNK_SIZE` tuned to 160 world units so a 4:3 photo
 * plane (e.g. 120×90) fits inside a chunk cube with margin.
 */

/** World units per chunk cube edge. 160 fits a 4:3 photo plane with margin. */
export const CHUNK_SIZE = 160;

/** Half-extent (in chunks) of the always-mounted ring around the camera. */
export const RENDER_DISTANCE = 4;

/** Extra ring of chunks beyond `RENDER_DISTANCE` that fades out smoothly. */
export const CHUNK_FADE_MARGIN = 1;

/** Maximum camera velocity magnitude (world units / frame at 60fps reference). */
export const MAX_VELOCITY = 3.2;

/**
 * Absolute depth (world units) at which the far depth-fade ramp begins.
 * Tuned relative to MAX_CAMERA_Z (1000): planes start fading at ~45% zoom-out
 * and are fully invisible by ~70%, so users see depth fade as they zoom out.
 * Mirrors the reference repo's 140/260 band scaled to our larger world.
 */
export const DEPTH_FADE_START = 450;

/** Absolute depth (world units) at which planes are fully hidden by depth fade. */
export const DEPTH_FADE_END = 700;

/** Mouse-parallax drift amplitude base; multiplied by zoom factor each frame. */
export const DRIFT_AMOUNT = 8.0;

/** Lerp factor smoothing drift toward the mouse-driven target. */
export const DRIFT_LERP = 0.12;

/** Lerp factor smoothing drift while zooming (snappier to avoid lag). */
export const DRIFT_LERP_ZOOMING = 0.2;

/** Velocity magnitude (|velocity.z|) above which the camera is considered zooming. */
export const ZOOMING_VEL_THRESHOLD = 0.05;

/** Minimum of zoomFactor = clamp(basePos.z / ZOOM_FACTOR_DIVISOR, min, max). */
export const ZOOM_FACTOR_MIN = 0.3;

/** Maximum of zoomFactor = clamp(basePos.z / ZOOM_FACTOR_DIVISOR, min, max). */
export const ZOOM_FACTOR_MAX = 2.0;

/** Divisor for basePos.z → zoomFactor. */
export const ZOOM_FACTOR_DIVISOR = 50;

/** Oposity below which a plane is considered invisible (mesh.visible = false). */
export const INVIS_THRESHOLD = 0.01;

/** Per-frame camera translation when a movement key is held. */
export const KEYBOARD_SPEED = 0.18;

/** Lerp factor for smoothing camera position toward velocity target. */
export const VELOCITY_LERP = 0.16;

/** Per-frame decay applied to velocity when no input is active (inertia). */
export const VELOCITY_DECAY = 0.9;

/** Initial camera Z (distance from the z=0 plane of the canvas). */
export const INITIAL_CAMERA_Z = 250;

/** Minimum camera Z — prevents flying through the canvas. */
export const MIN_CAMERA_Z = 5;

/** Maximum camera Z — prevents zooming out to nothing. */
export const MAX_CAMERA_Z = 1000;

/** A precomputed cube of integer offsets with its Chebyshev distance. */
export interface ChunkOffset {
  /** X offset in chunks relative to the camera chunk. */
  readonly dx: number;
  /** Y offset in chunks relative to the camera chunk. */
  readonly dy: number;
  /** Z offset in chunks relative to the camera chunk. */
  readonly dz: number;
  /** Chebyshev distance (max(|dx|,|dy|,|dz|)) from the origin. */
  readonly dist: number;
}

/**
 * Precomputed list of `{ dx, dy, dz, dist }` offsets spanning the full cube
 * out to Chebyshev distance `RENDER_DISTANCE + CHUNK_FADE_MARGIN`. Sorted by
 * ascending distance so nearer chunks mount first. ~125 entries for the
 * default 3×3×3 + fade ring.
 */
export const CHUNK_OFFSETS: readonly ChunkOffset[] = (() => {
  const radius = RENDER_DISTANCE + CHUNK_FADE_MARGIN;
  const offsets: ChunkOffset[] = [];
  for (let dx = -radius; dx <= radius; dx++) {
    for (let dy = -radius; dy <= radius; dy++) {
      for (let dz = -radius; dz <= radius; dz++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy), Math.abs(dz));
        if (dist <= radius) {
          offsets.push({ dx, dy, dz, dist });
        }
      }
    }
  }
  offsets.sort((a, b) => a.dist - b.dist);
  return offsets;
})();

/** Chebyshev chunk distance at/below which a photo node promotes to full-res. */
export const LOD_FULL_CHEBY = 1;
/** Absolute depth (world units) at/below which a photo node promotes to full-res. */
export const LOD_FULL_DEPTH = 120;
/** Extra Chebyshev distance beyond LOD_FULL_CHEBY where a node stays full-res before demoting (hysteresis band). */
export const LOD_HYSTERESIS = 1;
/** Absolute depth hysteresis band (world units) beyond LOD_FULL_DEPTH before demoting. */
export const LOD_FULL_DEPTH_HYSTERESIS = 60;
/** Maximum simultaneous full-res textures held in GPU memory (LRU eviction above this). */
export const LOD_FULL_MAX = 12;

/**
 * Returns the chunk-update throttle delay in milliseconds based on the
 * current camera velocity magnitude. Ported from the reference's
 * `getChunkUpdateThrottleMs`.
 *
 * - Fast zoom (>2 world units/frame) → 500ms (aggressive throttle).
 * - Zoom (>0.5) → 400ms.
 * - Idle (≤0.5) → 100ms.
 *
 * @param velocityMag - magnitude of the camera velocity vector.
 * @returns throttle delay in milliseconds.
 */
export function getChunkUpdateThrottleMs(velocityMag: number): number {
  if (velocityMag > 2) return 500;
  if (velocityMag > 0.5) return 400;
  return 100;
}