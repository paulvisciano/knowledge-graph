<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fade } from 'svelte/transition';
  import type { KGNode } from '$lib/constants';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { LightragClient } from '$lib/services/lightrag-client';
  import { textureCache } from '$lib/services/TextureCache';
  import { SceneManager } from './renderer/SceneManager';
  import { TIME_BUCKET_SPACING } from './renderer/constants';
  import { buildCanvasLayout, buildTimeIndex } from './Layout';
  import type { TimeIndex } from './Layout';
  import NodeOverlay from './NodeOverlay.svelte';
  import type { CanvasNode } from './renderer/types';

  const client = new LightragClient();
  let loadError = $state<string | null>(null);
  let loaded = $state(false);

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

  let selectedNodeId = $state<string | null>(null);
  let selectedCanvasNode = $state<CanvasNode | null>(null);
  let selectedKgNode = $derived<KGNode | null>(
    selectedNodeId ? graphStore.nodes.find((n) => n.id === selectedNodeId) ?? null : null,
  );

  let hoveredNodeId = $state<string | null>(null);
  let tooltipX = $state(0);
  let tooltipY = $state(0);

  let timeIndex: TimeIndex | null = null;
  let dateLabel = $state<string | null>(null);

  function clearSelection(): void {
    selectedNodeId = null;
    selectedCanvasNode = null;
  }

  const THROTTLE_MS = 200;

  function wireSceneManager(sm: SceneManager): void {
    sm.onSelectNode = (nodeId) => {
      if (nodeId) {
        const cn = sm.getCanvasNode(nodeId);
        selectedNodeId = nodeId;
        selectedCanvasNode = cn ?? null;
      } else {
        clearSelection();
      }
    };
    sm.onHoverNode = (nodeId) => {
      hoveredNodeId = nodeId;
    };
    sm.onChunkChange = (_cx, _cy, cz) => {
      updateDateLabel(cz);
    };
  }

  function updateDateLabel(camChunkZ: number): void {
    if (!timeIndex || timeIndex.indexToLabel.length === 0) {
      dateLabel = null;
      return;
    }
    const labels = timeIndex.indexToLabel;
    // Time buckets are spaced TIME_BUCKET_SPACING chunks apart, so divide the
    // camera chunk Z by the spacing to recover the dense bucket index.
    const bucketIdx = Math.round(camChunkZ / TIME_BUCKET_SPACING);
    if (bucketIdx < 0) {
      dateLabel = labels[0];
      return;
    }
    if (bucketIdx >= labels.length) {
      dateLabel = labels[labels.length - 1];
      return;
    }
    dateLabel = labels[bucketIdx];
  }

  function rebuildLayout(): void {
    if (!sceneManager) return;
    const nodes = buildCanvasLayout(
      graphStore.nodes,
      graphStore.edges,
      graphStore.photoImages,
      graphStore.personImages,
    );
    timeIndex = buildTimeIndex(graphStore.nodes, graphStore.edges);
    sceneManager.setNodes(nodes);
    lastAppliedAt = Date.now();
    if (timeIndex.indexToLabel.length > 0) {
      const startZ = Math.floor(sceneManager.camera.position.z / 160);
      updateDateLabel(startZ);
    }
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

  function onContainerPointerMove(e: PointerEvent): void {
    if (!containerEl) return;
    const rect = containerEl.getBoundingClientRect();
    tooltipX = e.clientX - rect.left;
    tooltipY = e.clientY - rect.top;
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
    } finally {
      loaded = true;
    }
  }

  onMount(() => {
    mounted = true;
    if (containerEl && graphStore.nodes.length > 0) {
        sceneManager = new SceneManager(containerEl);
        wireSceneManager(sceneManager);
        rebuildLayout();
        sceneManager.start();
        firstLayoutApplied = true;
    } else {
        // No data yet — kick off the initial load. The $effect below will mount
        // the renderer once graphStore.nodes populates.
        loadGraph();
    }
    containerEl?.addEventListener('pointermove', onContainerPointerMove);
  });

  onDestroy(() => {
    if (pendingTimer) {
      clearTimeout(pendingTimer);
      pendingTimer = null;
    }
    containerEl?.removeEventListener('pointermove', onContainerPointerMove);
    sceneManager?.stop();
    sceneManager?.dispose();
    sceneManager = undefined;
    // Cancel any in-flight texture image fetches so their browser connection
    // slots are released immediately — otherwise pending face-crop / photo
    // fetches can block other tabs' API requests for tens of seconds.
    textureCache.abortInFlight();
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
        wireSceneManager(sceneManager);
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
    {:else if !loaded}
      <div class="empty-title">Loading graph…</div>
      <div class="empty-sub">Fetching nodes from the knowledge graph.</div>
    {:else}
      <div class="empty-icon" aria-hidden="true">
        <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      </div>
      <div class="empty-title">No knowledge graph yet</div>
      <div class="empty-sub">Attach an image to the chat below to start building your knowledge graph. Extracted entities and relationships will appear here.</div>
    {/if}
  </div>
{/if}

<div class="phase-badge">
  <span class="badge-label">Canvas view (Phase 2)</span>
  <a class="badge-link" href="?view=graph">← back to force graph</a>
</div>

{#if hoveredNodeId && !selectedNodeId}
  <div class="hover-tooltip" style="left: {tooltipX + 14}px; top: {tooltipY + 14}px;">
    Click to view details
  </div>
{/if}

{#if dateLabel}
  <div class="date-indicator" aria-live="polite">
    <span class="date-indicator-icon" aria-hidden="true">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
    </span>
    {#key dateLabel}
      <span class="date-indicator-label" in:fade={{ duration: 220 }}>{dateLabel}</span>
    {/key}
  </div>
{/if}

<NodeOverlay node={selectedCanvasNode} kgNode={selectedKgNode} onClose={clearSelection} />

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
    max-width: 22rem;
    text-align: center;
  }
  .empty-icon {
    width: 3.5rem;
    height: 3.5rem;
    border-radius: 1rem;
    background: rgba(0, 212, 255, 0.1);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #00d4ff;
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

  .hover-tooltip {
    position: absolute;
    z-index: 20;
    padding: 0.3rem 0.6rem;
    background: rgba(10, 14, 23, 0.92);
    border: 1px solid rgba(0, 212, 255, 0.4);
    border-radius: 6px;
    color: #00d4ff;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 0.72rem;
    white-space: nowrap;
    pointer-events: none;
    backdrop-filter: blur(4px);
    transition: opacity 0.12s ease;
  }

  .date-indicator {
    position: absolute;
    right: 14px;
    bottom: 14px;
    z-index: 20;
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.45rem 0.75rem;
    background: rgba(10, 14, 23, 0.88);
    border: 1px solid rgba(0, 212, 255, 0.35);
    border-radius: 8px;
    color: #00d4ff;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 0.78rem;
    letter-spacing: 0.03em;
    white-space: nowrap;
    pointer-events: none;
    backdrop-filter: blur(6px);
    box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35);
    transition: opacity 0.18s ease;
  }
  .date-indicator-icon {
    display: flex;
    align-items: center;
    opacity: 0.85;
  }
  .date-indicator-label {
    font-variant-numeric: tabular-nums;
  }
</style>