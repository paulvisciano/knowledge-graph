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
  CHUNK_SIZE,
  INITIAL_CAMERA_Z,
  KEYBOARD_SPEED,
  MAX_CAMERA_Z,
  MAX_VELOCITY,
  MIN_CAMERA_Z,
  VELOCITY_DECAY,
  VELOCITY_LERP,
} from './constants';
import { ChunkManager } from './ChunkManager';
import type { CanvasNode } from './types';

/** Camera field of view in degrees. */
const CAMERA_FOV = 60;
/** Near clipping plane. */
const CAMERA_NEAR = 0.1;
/** Far clipping plane. */
const CAMERA_FAR = 2000;
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
      CAMERA_FAR,
    );
    this._camera.position.set(0, 0, INITIAL_CAMERA_Z);

    this._scene = new THREE.Scene();
    this._scene.background = new THREE.Color(BACKGROUND_COLOR);

    this._sharedGeometry = new THREE.PlaneGeometry(1, 1);
    this._chunkManager = new ChunkManager(this._scene, this._sharedGeometry);

    this._resizeObserver = new ResizeObserver(() => this.onResize());
    this._resizeObserver.observe(container);

    this.bindEvents();
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
   * (Re)builds the layout from canvas nodes and forwards to `ChunkManager`.
   *
   * @param nodes - all canvas nodes.
   */
  setNodes(nodes: CanvasNode[]): void {
    if (!this._userMoved && nodes.length > 0) {
      this.centerOnLayout(nodes);
    }
    this._chunkManager.setLayout(nodes);
  }

  /** Moves the camera to the world-space centroid of the node layout. */
  private centerOnLayout(nodes: CanvasNode[]): void {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      const wx = n.cellX * CHUNK_SIZE + n.localX;
      const wy = n.cellY * CHUNK_SIZE + n.localY;
      if (wx < minX) minX = wx;
      if (wy < minY) minY = wy;
      if (wx > maxX) maxX = wx;
      if (wy > maxY) maxY = wy;
    }
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const spanX = maxX - minX;
    const spanY = maxY - minY;
    const span = Math.max(spanX, spanY, CHUNK_SIZE);
    // Fit the layout in view: z = span / (2 * tan(fov/2)), clamped to camera limits.
    const fovRad = (this._camera.fov * Math.PI) / 180;
    const fitZ = span / (2 * Math.tan(fovRad / 2));
    const z = Math.max(MIN_CAMERA_Z, Math.min(MAX_CAMERA_Z, fitZ));
    this._camera.position.set(cx, cy, z);
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

    const prevChunkX = Math.floor(this._camera.position.x / CHUNK_SIZE);
    const prevChunkY = Math.floor(this._camera.position.y / CHUNK_SIZE);
    const prevChunkZ = Math.floor(this._camera.position.z / CHUNK_SIZE);

    const velMag = this._velocity.length();
    this._chunkManager.update(this._camera.position, velMag);

    const cx = Math.floor(this._camera.position.x / CHUNK_SIZE);
    const cy = Math.floor(this._camera.position.y / CHUNK_SIZE);
    const cz = Math.floor(this._camera.position.z / CHUNK_SIZE);
    if (cx !== prevChunkX || cy !== prevChunkY || cz !== prevChunkZ) {
      this.onChunkChange?.(cx, cy, cz);
    }

    this._renderer.render(this._scene, this._camera);
  };

  /** Applies held keyboard keys to the velocity vector. */
  private applyKeyboard(): void {
    const k = this._keys;
    const step = KEYBOARD_SPEED;
    let moved = false;
    if (k.has('ArrowLeft') || k.has('a')) { this._velocity.x -= step; moved = true; }
    if (k.has('ArrowRight') || k.has('d')) { this._velocity.x += step; moved = true; }
    if (k.has('ArrowUp') || k.has('w')) { this._velocity.y += step; moved = true; }
    if (k.has('ArrowDown') || k.has('s')) { this._velocity.y -= step; moved = true; }
    if (k.has('q')) { this._velocity.z += step; moved = true; }
    if (k.has('e')) { this._velocity.z -= step; moved = true; }
    if (moved) this._userMoved = true;
  }

  /** Lerps camera toward velocity, decays velocity, clamps camera Z. */
  private applyVelocity(): void {
    if (this._velocity.length() > MAX_VELOCITY) {
      this._velocity.normalize().multiplyScalar(MAX_VELOCITY);
    }
    this._camera.position.x += this._velocity.x * VELOCITY_LERP;
    this._camera.position.y += this._velocity.y * VELOCITY_LERP;
    this._camera.position.z += this._velocity.z * VELOCITY_LERP;

    if (this._camera.position.z < MIN_CAMERA_Z) this._camera.position.z = MIN_CAMERA_Z;
    if (this._camera.position.z > MAX_CAMERA_Z) this._camera.position.z = MAX_CAMERA_Z;

    this._velocity.multiplyScalar(VELOCITY_DECAY);
    if (this._velocity.lengthSq() < 1e-6) this._velocity.set(0, 0, 0);
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
  };

  private onPointerMove = (e: PointerEvent): void => {
    if (this._pointer.down) {
      const dx = e.clientX - this._pointer.lastX;
      const dy = e.clientY - this._pointer.lastY;
      this._pointer.lastX = e.clientX;
      this._pointer.lastY = e.clientY;
      // 5px threshold distinguishes a click from a drag (report §effort/risks).
      if (Math.hypot(e.clientX - this._pointer.downStartX, e.clientY - this._pointer.downStartY) > 5) {
        this._pointer.dragged = true;
      }
      const panScale = this._camera.position.z * 0.002;
      this._camera.position.x -= dx * panScale;
      this._camera.position.y += dy * panScale;
      this._userMoved = true;
    } else {
      this.hoverRaycast(e);
    }
  };

  private onPointerUp = (e: PointerEvent): void => {
    if (this._pointer.down && !this._pointer.dragged) {
      this.clickRaycast(e);
    }
    this._pointer.down = false;
  };

  private onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    this._camera.position.z -= e.deltaY * WHEEL_ZOOM_STEP;
    if (this._camera.position.z < MIN_CAMERA_Z) this._camera.position.z = MIN_CAMERA_Z;
    if (this._camera.position.z > MAX_CAMERA_Z) this._camera.position.z = MAX_CAMERA_Z;
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
    this.onHoverNode?.(hit ?? null);
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