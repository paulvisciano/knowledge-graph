/**
 * `SceneManager` — owns the Three.js scene graph, renderer, camera, RAF loop,
 * and input handling for the infinite canvas.
 *
 * This is the only renderer module that touches the DOM and `requestAnimationFrame`.
 * It wires a shared 1×1 `PlaneGeometry`, a `ChunkManager`, a
 * `PerspectiveCamera`, a `WebGLRenderer`, and a minimal direct-control
 * velocity model (drag pan / wheel zoom / WASD+arrows move) with inertia,
 * matching the reference repo's scene loop. Pause-on-hidden-tab and WebGL
 * context-loss handling are included for Phase 1 robustness.
 */
import * as THREE from 'three';
import {
  CHUNK_FADE_MARGIN,
  CHUNK_SIZE,
  DRIFT_AMOUNT,
  DRIFT_LERP,
  DRIFT_LERP_ZOOMING,
  INITIAL_CAMERA_Z,
  KEYBOARD_SPEED,
  MAX_CAMERA_Z,
  MAX_VELOCITY,
  MIN_CAMERA_Z,
  RENDER_DISTANCE,
  VELOCITY_DECAY,
  VELOCITY_LERP,
  ZOOMING_VEL_THRESHOLD,
  ZOOM_FACTOR_DIVISOR,
  ZOOM_FACTOR_MAX,
  ZOOM_FACTOR_MIN,
} from './constants';

/** Drag-to-keyboard pan scale factor (matches drag panScale of z*0.002 per pixel). */
const DRAG_PAN_SCALE = 0.002;
/** Pixels-equivalent moved per held-key frame — makes arrow keys pan at the same world-speed as dragging. */
const KEYBOARD_PAN_PIXELS = 20;
import { ChunkManager } from './ChunkManager';
import type { CanvasNode } from './types';

/** Camera field of view in degrees. */
const CAMERA_FOV = 60;
/** Near clipping plane. */
const CAMERA_NEAR = 0.1;
/** Minimum far clipping plane — used when no layout is applied yet. */
const CAMERA_FAR_MIN = 2000;
/** Dark canvas background. */
const BACKGROUND_COLOR = 0x0a0e17;
/** Wheel zoom step (world units per click, applied directly to camera Z). */
const WHEEL_ZOOM_STEP = 0.8;
/**
 * Top-level renderer façade. Construct with a container element, call
 * `setNodes` with a `CanvasNode[]` layout, then `start()`.
 */
export class SceneManager {
  private readonly _container: HTMLElement;
  private readonly _renderer: THREE.WebGLRenderer;
  private readonly _camera: THREE.PerspectiveCamera;
  private readonly _scene: THREE.Scene;
  private readonly _sharedGeometry: THREE.PlaneGeometry;
  private readonly _chunkManager: ChunkManager;
  private readonly _resizeObserver: ResizeObserver;
  private readonly _velocity = new THREE.Vector3();
  private readonly _basePos = new THREE.Vector3();
  private readonly _drift = new THREE.Vector2();
  private readonly _mouse = new THREE.Vector2();
  private readonly _keys = new Set<string>();
  private readonly _raycaster = new THREE.Raycaster();
  private readonly _pointerNdc = new THREE.Vector2();
  private readonly _pointer = {
    down: false,
    lastX: 0,
    lastY: 0,
    downStartX: 0,
    downStartY: 0,
    dragged: false,
  };

  private _rafId = 0;
  private _running = false;
  private _visible = true;
  private _disposed = false;
  private _userMoved = false;
  private _lastHoverTime = 0;
  private _hoveredNodeId: string | null = null;
  private _cursorMode: 'idle' | 'grabbing' | 'pointer' = 'idle';

  // Dynamic camera Z bounds derived from the layout's depth (time) range.
  // Newest photos sit at maxCellZ*CHUNK_SIZE; the camera starts just above
  // that and can zoom down toward 0 (oldest). Updated by setNodes.
  private _maxCameraZ = MAX_CAMERA_Z;
  private _minCameraZ = MIN_CAMERA_Z;
  private _lastChunkX = Infinity;
  private _lastChunkY = Infinity;
  private _lastChunkZ = Infinity;

  /** Optional callback fired when the camera crosses a chunk boundary. */
  onChunkChange?: (cx: number, cy: number, cz: number) => void;

  /** Fired when the user clicks a photo plane (or null on empty-space click). */
  onSelectNode?: (nodeId: string | null) => void;

  /** Fired as the pointer moves over a photo plane (or null when off-plane). */
  onHoverNode?: (nodeId: string | null) => void;

  /**
   * Creates the renderer, camera, scene, shared geometry, and chunk manager,
   * appends the renderer's DOM element to `container`, and attaches input +
   * resize listeners. Does not start the RAF loop — call `start()`.
   *
   * @param container - DOM element to mount the canvas in.
   */
  constructor(container: HTMLElement) {
    this._container = container;
    const width = container.clientWidth || 1;
    const height = container.clientHeight || 1;

    this._renderer = new THREE.WebGLRenderer({ antialias: true });
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.setSize(width, height);
    container.appendChild(this._renderer.domElement);
    this._renderer.domElement.style.display = 'block';
    this._renderer.domElement.style.touchAction = 'none';

    this._camera = new THREE.PerspectiveCamera(
      CAMERA_FOV,
      width / height,
      CAMERA_NEAR,
      CAMERA_FAR_MIN,
    );
    this._camera.position.set(0, 0, INITIAL_CAMERA_Z);

    this._scene = new THREE.Scene();
    this._scene.background = new THREE.Color(BACKGROUND_COLOR);

    this._sharedGeometry = new THREE.PlaneGeometry(1, 1);
    this._chunkManager = new ChunkManager(this._scene, this._sharedGeometry);

    this._resizeObserver = new ResizeObserver(() => this.onResize());
    this._resizeObserver.observe(container);

    this.bindEvents();
    this._renderer.domElement.style.cursor = 'grab';
  }

  /** The perspective camera. */
  get camera(): THREE.PerspectiveCamera {
    return this._camera;
  }

  /** Number of chunks currently mounted (debug). */
  get mountedChunkCount(): number {
    return this._chunkManager.mountedChunkCount;
  }

  /**
   * Looks up the `CanvasNode` for `nodeId` among mounted chunks. Returns
   * `undefined` when the node's chunk is outside the render distance.
   */
  getCanvasNode(nodeId: string): CanvasNode | undefined {
    return this._chunkManager.findPlaneByNodeId(nodeId)?.node;
  }

  /**
   * Returns the world-space position of the plane mesh for `nodeId`, or
   * `null` when the node's chunk is not currently mounted. Safe to call
   * outside the RAF loop — `getWorldPosition` updates the world matrix on
   * demand, so the returned position is correct even between frames.
   */
  getPlaneWorldPosition(nodeId: string): THREE.Vector3 | null {
    const plane = this._chunkManager.findPlaneByNodeId(nodeId);
    if (!plane) return null;
    const worldPos = new THREE.Vector3();
    plane.mesh.getWorldPosition(worldPos);
    return worldPos;
  }

  /**
   * Projects a world-space position to canvas-relative screen coordinates.
   * Returns `null` when the point is behind the camera.
   */
  projectToScreen(worldPos: THREE.Vector3): { x: number; y: number } | null {
    const rect = this._renderer.domElement.getBoundingClientRect();
    const ndc = worldPos.clone().project(this._camera);
    if (ndc.z > 1) return null;
    return {
      x: ((ndc.x + 1) / 2) * rect.width,
      y: ((1 - ndc.y) / 2) * rect.height,
    };
  }

  /**
   * (Re)builds the layout from canvas nodes and forwards to `ChunkManager`.
   *
   * @param nodes - all canvas nodes.
   */
  setNodes(nodes: CanvasNode[]): void {
    this.updateDepthBounds(nodes);
    if (!this._userMoved && nodes.length > 0) {
      this.centerOnLayout(nodes);
    }
    this._chunkManager.setLayout(nodes);
  }

  /**
   * Derive dynamic camera Z bounds + far clipping plane from the layout's
   * depth (time) range. Newest photos at maxCellZ get a starting camera Z
   * above them; the min is just above the oldest (z=0) plane so the user
   * can't fly past the oldest photos.
   */
   private updateDepthBounds(nodes: CanvasNode[]): void {
     if (nodes.length === 0) {
       this._maxCameraZ = MAX_CAMERA_Z;
       this._minCameraZ = MIN_CAMERA_Z;
       this._camera.far = CAMERA_FAR_MIN;
       this._camera.updateProjectionMatrix();
       return;
     }
     let maxCellZ = 0;
     let minCellZ = Infinity;
     for (const n of nodes) {
       if (n.cellZ > maxCellZ) maxCellZ = n.cellZ;
       if (n.cellZ < minCellZ) minCellZ = n.cellZ;
     }
     const newestZ = maxCellZ * CHUNK_SIZE + CHUNK_SIZE;
     this._maxCameraZ = newestZ + INITIAL_CAMERA_Z;
     // Keep the camera at least INITIAL_CAMERA_Z above the oldest bucket so
     // zooming in past the oldest photos can't fly the camera inside the
     // photo plane (which renders a blank scene).
     this._minCameraZ = Math.max(MIN_CAMERA_Z, minCellZ * CHUNK_SIZE + INITIAL_CAMERA_Z);
     const far = Math.max(CAMERA_FAR_MIN, this._maxCameraZ + CHUNK_SIZE * (RENDER_DISTANCE + CHUNK_FADE_MARGIN + 1));
     if (this._camera.far !== far) {
       this._camera.far = far;
       this._camera.updateProjectionMatrix();
     }
   }

  /** Moves the camera to face the newest photos at a comfortable viewing distance. */
  private centerOnLayout(nodes: CanvasNode[]): void {
    if (nodes.length === 0) {
      this._camera.position.set(0, 0, INITIAL_CAMERA_Z);
      this._basePos.set(0, 0, INITIAL_CAMERA_Z);
      return;
    }
    // Center on the newest time bucket (highest cellZ) — that's where the
    // user starts. X=0 is the center of each layer's within-bucket spread, and
    // Y is the first cluster band. Zooming in (decreasing camera z) travels
    // back in time.
    let newest = nodes[0];
    for (const n of nodes) if (n.cellZ > newest.cellZ) newest = n;
    const targetX = 0;
    const targetY = 0;
    const targetZ = newest.cellZ * CHUNK_SIZE + INITIAL_CAMERA_Z;
    this._camera.position.set(targetX, targetY, targetZ);
    this._basePos.set(targetX, targetY, targetZ);
  }

  /** Begins the RAF loop. Safe to call once; idempotent if already running. */
  start(): void {
    if (this._running || this._disposed) return;
    this._running = true;
    this._rafId = requestAnimationFrame(this.onFrame);
  }

  /** Cancels the RAF loop. Idempotent. */
  stop(): void {
    this._running = false;
    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId = 0;
    }
  }

  /** Stops the loop, disposes chunks, renderer, geometry, and removes DOM. */
  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this.stop();
    this._resizeObserver.disconnect();
    this.unbindEvents();
    this._chunkManager.dispose();
    this._sharedGeometry.dispose();
    this._renderer.dispose();
    if (this._renderer.domElement.parentNode === this._container) {
      this._container.removeChild(this._renderer.domElement);
    }
  }

  /** Resizes renderer + camera aspect to the current container size. */
  private onResize(): void {
    if (this._disposed) return;
    const width = this._container.clientWidth || 1;
    const height = this._container.clientHeight || 1;
    this._renderer.setSize(width, height);
    this._camera.aspect = width / height;
    this._camera.updateProjectionMatrix();
  }

  /** RAF callback — applies input, updates chunks, renders. */
  private readonly onFrame = (): void => {
    if (!this._running || this._disposed) return;
    this._rafId = requestAnimationFrame(this.onFrame);
    if (!this._visible) return;

    this.applyKeyboard();
    this.applyVelocity();
    this.applyDrift();

    // Compose final camera position from basePos + drift. Chunk/fade logic
    // uses basePos only so mouse parallax never triggers remounts or pop-in.
    this._camera.position.set(
      this._basePos.x + this._drift.x,
      this._basePos.y + this._drift.y,
      this._basePos.z,
    );

    const velMag = this._velocity.length();
    this._chunkManager.update(this._basePos, velMag);

    const cx = Math.floor(this._basePos.x / CHUNK_SIZE);
    const cy = Math.floor(this._basePos.y / CHUNK_SIZE);
    const cz = Math.floor(this._basePos.z / CHUNK_SIZE);
    if (cx !== this._lastChunkX || cy !== this._lastChunkY || cz !== this._lastChunkZ) {
      this._lastChunkX = cx;
      this._lastChunkY = cy;
      this._lastChunkZ = cz;
      this.onChunkChange?.(cx, cy, cz);
    }

    this._renderer.render(this._scene, this._camera);

    // DEBUG: expose scene graph for black-canvas diagnosis
    (this as unknown as { __debugLastFrame?: number }).__debugLastFrame = performance.now();
    (window as unknown as { __sm?: unknown }).__sm = this;
  };

  /** Applies held keyboard keys to the velocity vector. */
  private applyKeyboard(): void {
    const k = this._keys;
    const panStep = KEYBOARD_PAN_PIXELS * this._basePos.z * DRAG_PAN_SCALE;
    const zoomStep = KEYBOARD_SPEED;
    let moved = false;
    if (k.has('ArrowLeft') || k.has('a')) { this._velocity.x -= panStep; moved = true; }
    if (k.has('ArrowRight') || k.has('d')) { this._velocity.x += panStep; moved = true; }
    if (k.has('ArrowUp') || k.has('w')) { this._velocity.y += panStep; moved = true; }
    if (k.has('ArrowDown') || k.has('s')) { this._velocity.y -= panStep; moved = true; }
    if (k.has('q')) { this._velocity.z += zoomStep; moved = true; }
    if (k.has('e')) { this._velocity.z -= zoomStep; moved = true; }
    if (moved) this._userMoved = true;
  }

  /** Lerps basePos toward velocity, decays velocity, clamps basePos Z. */
  private applyVelocity(): void {
    if (this._velocity.length() > MAX_VELOCITY) {
      this._velocity.normalize().multiplyScalar(MAX_VELOCITY);
    }
    this._basePos.x += this._velocity.x * VELOCITY_LERP;
    this._basePos.y += this._velocity.y * VELOCITY_LERP;
    this._basePos.z += this._velocity.z * VELOCITY_LERP;

    if (this._basePos.z < this._minCameraZ) this._basePos.z = this._minCameraZ;
    if (this._basePos.z > this._maxCameraZ) this._basePos.z = this._maxCameraZ;

    this._velocity.multiplyScalar(VELOCITY_DECAY);
    if (this._velocity.lengthSq() < 1e-6) this._velocity.set(0, 0, 0);
  }

  /** Smooths drift toward mouse * driftAmount (zoom-scaled parallax). */
  private applyDrift(): void {
    const isZooming = Math.abs(this._velocity.z) > ZOOMING_VEL_THRESHOLD;
    const zoomFactor = Math.max(
      ZOOM_FACTOR_MIN,
      Math.min(ZOOM_FACTOR_MAX, this._basePos.z / ZOOM_FACTOR_DIVISOR),
    );
    const amount = DRIFT_AMOUNT * zoomFactor;
    const lerpFactor = isZooming ? DRIFT_LERP_ZOOMING : DRIFT_LERP;
    if (this._pointer.down) {
      // Freeze drift during drag — keep it at its current value.
      return;
    }
    this._drift.x = this._drift.x + (this._mouse.x * amount - this._drift.x) * lerpFactor;
    this._drift.y = this._drift.y + (this._mouse.y * amount - this._drift.y) * lerpFactor;
  }

  // --- input handlers --------------------------------------------------

  private onKeyDown = (e: KeyboardEvent): void => {
    this._keys.add(e.key);
  };

  private onKeyUp = (e: KeyboardEvent): void => {
    this._keys.delete(e.key);
  };

  private onPointerDown = (e: PointerEvent): void => {
    this._pointer.down = true;
    this._pointer.lastX = e.clientX;
    this._pointer.lastY = e.clientY;
    this._pointer.downStartX = e.clientX;
    this._pointer.downStartY = e.clientY;
    this._pointer.dragged = false;
    this._userMoved = true;
    this.updateCursor();
  };

  private onPointerMove = (e: PointerEvent): void => {
    const rect = this._renderer.domElement.getBoundingClientRect();
    this._mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this._mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    if (this._pointer.down) {
      const dx = e.clientX - this._pointer.lastX;
      const dy = e.clientY - this._pointer.lastY;
      this._pointer.lastX = e.clientX;
      this._pointer.lastY = e.clientY;
      // 5px threshold distinguishes a click from a drag (report §effort/risks).
      if (Math.hypot(e.clientX - this._pointer.downStartX, e.clientY - this._pointer.downStartY) > 5) {
        this._pointer.dragged = true;
      }
      const panScale = this._basePos.z * DRAG_PAN_SCALE;
      this._basePos.x -= dx * panScale;
      this._basePos.y += dy * panScale;
      this._userMoved = true;
      this.updateCursor();
    } else {
      this.hoverRaycast(e);
    }
  };

  private onPointerUp = (e: PointerEvent): void => {
    if (this._pointer.down && !this._pointer.dragged) {
      this.clickRaycast(e);
    }
    this._pointer.down = false;
    this.updateCursor();
  };

  private onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    // Inverted: scroll up (deltaY < 0) zooms in (z decreases), scroll down zooms out.
    this._basePos.z += e.deltaY * WHEEL_ZOOM_STEP;
    if (this._basePos.z < this._minCameraZ) this._basePos.z = this._minCameraZ;
    if (this._basePos.z > this._maxCameraZ) this._basePos.z = this._maxCameraZ;
    this._pointer.dragged = true;
    this._userMoved = true;
  };

  /**
   * Raycasts from pointer NDC into the mounted chunk meshes and fires
   * `onHoverNode` with the hit node id (or null). Throttled to ~30Hz.
   */
  private hoverRaycast(e: PointerEvent): void {
    const now = performance.now();
    if (now - this._lastHoverTime < 33) return;
    this._lastHoverTime = now;
    const hit = this.raycast(e);
    const id = hit ?? null;
    if (id !== this._hoveredNodeId) {
      this._hoveredNodeId = id;
      this.updateCursor();
    }
    this.onHoverNode?.(id);
  }

  /** Raycasts on click and fires `onSelectNode` with the hit id (or null). */
  private clickRaycast(e: PointerEvent): void {
    const hit = this.raycast(e);
    this.onSelectNode?.(hit ?? null);
  }

  /**
   * Converts a pointer event to NDC and raycasts against the mounted chunk
   * meshes. Returns the hit node id, or `undefined` on no hit.
   */
  private raycast(e: PointerEvent): string | undefined {
    const rect = this._renderer.domElement.getBoundingClientRect();
    this._pointerNdc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    this._pointerNdc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    this._raycaster.setFromCamera(this._pointerNdc, this._camera);
    const meshes = this._chunkManager.getPickableMeshes();
    if (meshes.length === 0) return undefined;
    const hits = this._raycaster.intersectObjects(meshes, false);
    if (hits.length === 0) return undefined;
    const nodeId = hits[0].object.userData.nodeId;
    return typeof nodeId === 'string' ? nodeId : undefined;
  }

  private onContextLoss = (): void => {
    console.warn('[SceneManager] WebGL context lost — stopping RAF loop.');
    this.stop();
  };

  private onVisibilityChange = (): void => {
    this._visible = document.visibilityState === 'visible';
    if (this._visible && this._running && !this._rafId) {
      this._rafId = requestAnimationFrame(this.onFrame);
    }
  };

  /** Updates the canvas cursor based on interaction state. */
  private updateCursor(): void {
    let mode: 'idle' | 'grabbing' | 'pointer';
    if (this._pointer.down) {
      mode = 'grabbing';
    } else if (this._hoveredNodeId !== null) {
      mode = 'pointer';
    } else {
      mode = 'idle';
    }
    if (mode === this._cursorMode) return;
    this._cursorMode = mode;
    const el = this._renderer.domElement;
    if (mode === 'grabbing') el.style.cursor = 'grabbing';
    else if (mode === 'pointer') el.style.cursor = 'pointer';
    else el.style.cursor = 'grab';
  }

  private bindEvents(): void {
    window.addEventListener('keydown', this.onKeyDown);
    window.addEventListener('keyup', this.onKeyUp);
    const el = this._renderer.domElement;
    el.addEventListener('pointerdown', this.onPointerDown);
    el.addEventListener('pointermove', this.onPointerMove);
    el.addEventListener('pointerup', this.onPointerUp);
    el.addEventListener('pointerleave', this.onPointerUp);
    el.addEventListener('wheel', this.onWheel, { passive: false });
    el.addEventListener('webglcontextlost', this.onContextLoss);
    document.addEventListener('visibilitychange', this.onVisibilityChange);
  }

  private unbindEvents(): void {
    window.removeEventListener('keydown', this.onKeyDown);
    window.removeEventListener('keyup', this.onKeyUp);
    const el = this._renderer.domElement;
    el.removeEventListener('pointerdown', this.onPointerDown);
    el.removeEventListener('pointermove', this.onPointerMove);
    el.removeEventListener('pointerup', this.onPointerUp);
    el.removeEventListener('pointerleave', this.onPointerUp);
    el.removeEventListener('wheel', this.onWheel);
    el.removeEventListener('webglcontextlost', this.onContextLoss);
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
  }
}