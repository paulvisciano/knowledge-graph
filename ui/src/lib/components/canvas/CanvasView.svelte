<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { KGNode } from '$lib/constants';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { LightragClient } from '$lib/services/lightrag-client';
  import { SceneManager } from './renderer/SceneManager';
  import { buildCanvasLayout } from './Layout';

  const client = new LightragClient();
  let loadError = $state<string | null>(null);

  let {
    onqueryAbout = (_node: KGNode) => {}
  }: {
    onqueryAbout?: (node: KGNode) => void;
  } = $props();

  let containerEl: HTMLDivElement | undefined = $state();
  let sceneManager: SceneManager | undefined = $state();
  let mounted = false;
  let firstLayoutApplied = false;
  let pendingTimer: ReturnType<typeof setTimeout> | null = null;
  let lastAppliedAt = 0;

  const THROTTLE_MS = 200;

  function rebuildLayout(): void {
    if (!sceneManager) return;
    const nodes = buildCanvasLayout(
      graphStore.nodes,
      graphStore.edges,
      graphStore.photoImages,
      graphStore.personImages,
    );
    sceneManager.setNodes(nodes);
    lastAppliedAt = Date.now();
  }

  function scheduleRebuild(): void {
    if (!mounted || !sceneManager) return;
    if (pendingTimer) return;
    const elapsed = Date.now() - lastAppliedAt;
    const delay = Math.max(0, THROTTLE_MS - elapsed);
    pendingTimer = setTimeout(() => {
      pendingTimer = null;
      rebuildLayout();
    }, delay);
  }

  /** Initial graph fetch — mirrors GraphView's popular-labels fallback. */
  async function loadGraph(): Promise<void> {
    loadError = null;
    try {
      let label: string | undefined;
      try {
        const popular = await client.getPopularLabels(1);
        label = popular?.[0];
      } catch {
        const labels = await client.getLabels();
        label = labels?.[0];
      }
      await graphStore.loadGraph(label);
    } catch (e) {
      loadError = e instanceof Error ? e.message : 'Failed to load graph';
    }
  }

  onMount(() => {
    mounted = true;
    if (containerEl && graphStore.nodes.length > 0) {
      sceneManager = new SceneManager(containerEl);
      sceneManager.onChunkChange = (cx, cy, cz) => {
        console.debug('[CanvasView] chunk change', { cx, cy, cz });
      };
      rebuildLayout();
      sceneManager.start();
      firstLayoutApplied = true;
    } else {
      // No data yet — kick off the initial load. The $effect below will mount
      // the renderer once graphStore.nodes populates.
      loadGraph();
    }
  });

  onDestroy(() => {
    if (pendingTimer) {
      clearTimeout(pendingTimer);
      pendingTimer = null;
    }
    sceneManager?.stop();
    sceneManager?.dispose();
    sceneManager = undefined;
    mounted = false;
  });

  // React to graphStore changes: mount the renderer once data arrives, then
  // throttle-rebuild on subsequent streaming updates. Skips the very first
  // emission (handled by onMount above) to avoid a double setNodes.
  $effect(() => {
    void graphStore.nodes;
    void graphStore.edges;
    void graphStore.photoImages;

    if (!mounted) return;
    if (!firstLayoutApplied) {
      if (containerEl && !sceneManager && graphStore.nodes.length > 0) {
        sceneManager = new SceneManager(containerEl);
        sceneManager.onChunkChange = (cx, cy, cz) => {
          console.debug('[CanvasView] chunk change', { cx, cy, cz });
        };
        rebuildLayout();
        sceneManager.start();
        firstLayoutApplied = true;
      }
      return;
    }
    scheduleRebuild();
  });

  let isEmpty = $derived(graphStore.nodes.length === 0);
 </script>
 
 <div bind:this={containerEl} class="canvas-container"></div>
 
 {#if isEmpty}
   <div class="empty-state">
     {#if loadError}
       <div class="empty-title">Failed to load graph</div>
       <div class="empty-sub">{loadError}</div>
       <button class="retry-btn" onclick={() => loadGraph()}>Retry</button>
     {:else}
       <div class="empty-title">Loading graph…</div>
       <div class="empty-sub">Fetching nodes from the knowledge graph.</div>
     {/if}
   </div>
{/if}

<div class="phase-badge">
  <span class="badge-label">Canvas view (Phase 1)</span>
  <a class="badge-link" href="?view=graph">← back to force graph</a>
</div>

<style>
  .canvas-container {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }

  .empty-state {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    pointer-events: none;
    color: #00d4ff;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  }
  .empty-title {
    font-size: 1.25rem;
    letter-spacing: 0.05em;
    text-shadow: 0 0 12px rgba(0, 212, 255, 0.4);
  }
  .empty-sub {
    font-size: 0.85rem;
    color: rgba(0, 212, 255, 0.6);
  }
  .retry-btn {
    margin-top: 0.5rem;
    padding: 0.4rem 0.9rem;
    background: rgba(0, 212, 255, 0.12);
    border: 1px solid rgba(0, 212, 255, 0.4);
    border-radius: 6px;
    color: #00d4ff;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 0.78rem;
    cursor: pointer;
    pointer-events: auto;
    transition: background 0.15s ease, border-color 0.15s ease;
  }
  .retry-btn:hover {
    background: rgba(0, 212, 255, 0.22);
    border-color: rgba(0, 212, 255, 0.7);
  }

  .phase-badge {
    position: absolute;
    top: 12px;
    left: 12px;
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.35rem 0.6rem;
    background: rgba(10, 14, 23, 0.85);
    border: 1px solid rgba(0, 212, 255, 0.35);
    border-radius: 6px;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 0.72rem;
    color: #00d4ff;
    z-index: 10;
    pointer-events: auto;
    backdrop-filter: blur(4px);
  }
  .badge-label {
    opacity: 0.85;
  }
  .badge-link {
    color: #00d4ff;
    text-decoration: none;
    opacity: 0.7;
    transition: opacity 0.15s ease;
  }
  .badge-link:hover {
    opacity: 1;
    text-decoration: underline;
  }
</style>