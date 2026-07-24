<script lang="ts">
  import { onDestroy } from 'svelte';
  import { imageProcessingStore, type ImageStage } from '$lib/stores/image-processing.svelte';
  import type { SceneManager } from './renderer/SceneManager';

  let { sceneManager }: { sceneManager: SceneManager } = $props();

  type OverlayEntry = {
    nodeId: string;
    x: number;
    y: number;
    stage: ImageStage;
    stageLabel: string;
    color: string;
    flashing: boolean;
  };

  let overlays = $state<OverlayEntry[]>([]);

  const STAGE_COLOR: Record<ImageStage, string> = {
    extracting_exif: '#22d3ee',
    detecting_faces: '#22d3ee',
    building_captions: '#22d3ee',
    creating_entities: '#22d3ee',
    queued_for_ai: '#f59e0b',
    describing_image: '#a78bfa',
    uploading_to_graph: '#a78bfa',
    graph_processing: '#a78bfa',
    linking_visual_entities: '#a78bfa',
    complete: '#34d399',
    error: '#ef4444',
  };

  const COMPLETE_FLASH_MS = 2000;
  const ERROR_FLASH_MS = 5000;
  const TICK_MS = 100;

  // nodeId -> timestamp (ms) when the flash should expire. Populated when a
  // status transitions to complete/error so the badge lingers briefly.
  const flashUntil = new Map<string, number>();
  // nodeId -> last stage seen, to detect the transition into complete/error.
  const lastStage = new Map<string, ImageStage>();

  let intervalId: ReturnType<typeof setInterval> | null = null;

  function tick(): void {
    const now = performance.now();
    const statuses = imageProcessingStore.statuses;

    // Detect transitions into complete/error and seed the flash window.
    for (const [nodeId, status] of Object.entries(statuses)) {
      const prev = lastStage.get(nodeId);
      if (prev !== status.stage) {
        lastStage.set(nodeId, status.stage);
        if (status.stage === 'complete' && prev !== undefined) {
          flashUntil.set(nodeId, now + COMPLETE_FLASH_MS);
        } else if (status.stage === 'error' && prev !== undefined) {
          flashUntil.set(nodeId, now + ERROR_FLASH_MS);
        }
      }
    }

    // Expire flash entries.
    for (const [nodeId, until] of flashUntil) {
      if (now >= until) {
        flashUntil.delete(nodeId);
        lastStage.delete(nodeId);
      }
    }

    const next: OverlayEntry[] = [];

    for (const [nodeId, status] of Object.entries(statuses)) {
      const isFlashing = flashUntil.has(nodeId);
      const isComplete = status.stage === 'complete';
      const isError = status.stage === 'error';
      // Skip completed/errored statuses that aren't in their flash window.
      if ((isComplete || isError) && !isFlashing) {
        // Also clean lastStage if status entry was removed already.
        continue;
      }

      const worldPos = sceneManager.getPlaneWorldPosition(nodeId);
      if (!worldPos) continue; // chunk not mounted / outside render distance
      const screen = sceneManager.projectToScreen(worldPos);
      if (!screen) continue; // behind camera

      next.push({
        nodeId,
        x: screen.x,
        y: screen.y,
        stage: status.stage,
        stageLabel: status.stageLabel,
        color: STAGE_COLOR[status.stage] ?? '#22d3ee',
        flashing: isComplete || isError,
      });
    }

    overlays = next;
  }

  intervalId = setInterval(tick, TICK_MS);

  onDestroy(() => {
    if (intervalId !== null) clearInterval(intervalId);
    flashUntil.clear();
    lastStage.clear();
  });
</script>

<div class="processing-overlay-layer" aria-hidden="true">
  {#each overlays as entry (entry.nodeId)}
    <div
      class="processing-badge"
      class:flashing={entry.flashing}
      class:is-error={entry.stage === 'error'}
      style="--badge-color: {entry.color}; transform: translate({entry.x}px, {entry.y}px);"
      title={entry.stageLabel}
    >
      <span class="processing-ring"></span>
      <span class="processing-core"></span>
    </div>
  {/each}
</div>

<style>
  .processing-overlay-layer {
    position: absolute;
    inset: 0;
    pointer-events: none;
    overflow: hidden;
    z-index: 5;
  }

  .processing-badge {
    position: absolute;
    top: 0;
    left: 0;
    width: 24px;
    height: 24px;
    margin: -12px 0 0 -12px;
    display: grid;
    place-items: center;
    pointer-events: none;
    will-change: transform;
    transition: transform 0.12s linear;
  }

  .processing-ring {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    border: 2px solid var(--badge-color);
    background: color-mix(in oklch, var(--badge-color) 18%, transparent);
    box-shadow:
      0 0 12px color-mix(in oklch, var(--badge-color) 60%, transparent),
      0 0 0 1px color-mix(in oklch, var(--badge-color) 30%, transparent);
    animation: processing-pulse 1.5s ease-in-out infinite;
  }

  .processing-core {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--badge-color);
    box-shadow: 0 0 8px var(--badge-color);
    animation: processing-pulse 1.5s ease-in-out infinite;
  }

  .processing-badge.flashing .processing-ring {
    animation: none;
    opacity: 1;
  }

  .processing-badge.flashing .processing-core {
    animation: processing-flash 0.5s ease-out 1;
  }

  .processing-badge.flashing.is-error .processing-core {
    animation: processing-flash-error 0.5s ease-out 1;
  }

  @keyframes processing-pulse {
    0%,
    100% {
      opacity: 0.4;
    }
    50% {
      opacity: 1;
    }
  }

  @keyframes processing-flash {
    0% {
      transform: scale(1);
      opacity: 1;
    }
    100% {
      transform: scale(1.8);
      opacity: 0;
    }
  }

  @keyframes processing-flash-error {
    0% {
      transform: scale(1);
      opacity: 1;
    }
    100% {
      transform: scale(1.4);
      opacity: 0;
    }
  }
</style>