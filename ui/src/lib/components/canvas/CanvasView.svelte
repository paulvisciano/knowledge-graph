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
  import ProcessingOverlay from './ProcessingOverlay.svelte';
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

  function navigateToNode(nodeId: string): void {
    const cn = sceneManager?.getCanvasNode(nodeId);
    selectedNodeId = nodeId;
    selectedCanvasNode = cn ?? null;
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

  /** Expose the SceneManager on window for E2E / browser-harness testing. */
  function exposeSceneManager(sm: SceneManager): void {
    if (typeof window !== 'undefined') {
      (window as any).__sceneManager = sm;
    }
  }

  onMount(() => {
    mounted = true;
    if (containerEl && graphStore.nodes.length > 0) {
        sceneManager = new SceneManager(containerEl);
        wireSceneManager(sceneManager);
        exposeSceneManager(sceneManager);
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
    if (typeof window !== 'undefined') {
      delete (window as any).__sceneManager;
    }
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
        exposeSceneManager(sceneManager);
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
 
 <div bind:this={containerEl} class="canvas-container" data-testid="graph-canvas"></div>
 
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

<NodeOverlay node={selectedCanvasNode} kgNode={selectedKgNode} onClose={clearSelection} onNavigate={navigateToNode} />

{#if sceneManager}
  <ProcessingOverlay {sceneManager} />
{/if}

<style>
  /* ── Tokens — match NodeOverlay's --overlay-* visual system ── */
  :root {
    --canvas-bg:          oklch(6% 0.02 260);
    --canvas-fg:          oklch(90% 0.005 250);
    --canvas-muted:       oklch(65% 0.02 255);
    --canvas-faint:       oklch(55% 0.02 255);
    --canvas-accent:      oklch(82% 0.14 210);
    --canvas-accent-dim:  oklch(82% 0.14 210 / 18%);
    --canvas-success:     oklch(72% 0.15 150);
    --canvas-danger:      oklch(62% 0.20 18);
    --canvas-glass:       oklch(16% 0.015 255 / 45%);
    --canvas-glass-light: oklch(20% 0.015 255 / 30%);
    --canvas-hairline:    oklch(50% 0.03 255 / 8%);
  }

  .canvas-container {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }

  /* ── Empty state — glassmorphism, floating spatial ── */
  .empty-state {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    pointer-events: none;
    color: var(--canvas-accent);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    animation: float-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  .empty-title {
    font-size: 1.25rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--canvas-fg);
    text-shadow: 0 0 24px oklch(82% 0.14 210 / 30%);
  }

  .empty-sub {
    font-size: 0.85rem;
    line-height: 1.65;
    color: var(--canvas-muted);
    max-width: 22rem;
    text-align: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  }

  .empty-icon {
    width: 4rem;
    height: 4rem;
    border-radius: 50%;
    background: var(--canvas-glass);
    backdrop-filter: blur(24px) saturate(1.4);
    -webkit-backdrop-filter: blur(24px) saturate(1.4);
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 10%),
      0 20px 60px oklch(0% 0 0 / 40%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--canvas-accent);
  }

  .retry-btn {
    margin-top: 6px;
    padding: 8px 18px;
    background: oklch(62% 0.20 18 / 10%);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border-radius: 100px;
    border: 1px solid oklch(62% 0.20 18 / 20%);
    color: oklch(62% 0.20 18 / 80%);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    cursor: pointer;
    pointer-events: auto;
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .retry-btn:hover {
    color: oklch(62% 0.20 18);
    background: oklch(62% 0.20 18 / 18%);
    border-color: oklch(62% 0.20 18 / 50%);
    transform: translateY(-1px);
    box-shadow: 0 8px 24px oklch(62% 0.20 18 / 15%);
  }

  /* ── Hover tooltip — glass pill ── */
  .hover-tooltip {
    position: absolute;
    z-index: 20;
    padding: 5px 12px;
    background: var(--canvas-glass);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border-radius: 100px;
    box-shadow: 0 0 0 1px oklch(50% 0.03 255 / 8%);
    color: var(--canvas-muted);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    white-space: nowrap;
    pointer-events: none;
    transition: opacity 0.12s ease;
  }

  /* ── Date indicator — floating glass panel ── */
  .date-indicator {
    position: absolute;
    right: 16px;
    bottom: 16px;
    z-index: 20;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    background: var(--canvas-glass);
    backdrop-filter: blur(24px) saturate(1.4);
    -webkit-backdrop-filter: blur(24px) saturate(1.4);
    border-radius: 100px;
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 8%),
      0 8px 32px oklch(0% 0 0 / 30%);
    color: var(--canvas-accent);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    letter-spacing: 0.06em;
    white-space: nowrap;
    pointer-events: none;
    transition: opacity 0.18s ease;
  }

  .date-indicator-icon {
    display: flex;
    align-items: center;
    color: var(--canvas-muted);
  }

  .date-indicator-label {
    font-variant-numeric: tabular-nums;
    color: var(--canvas-fg);
  }

  /* ── Shared animations ── */
  @keyframes float-in {
    from { opacity: 0; transform: translateY(20px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
</style>