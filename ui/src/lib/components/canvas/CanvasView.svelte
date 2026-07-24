<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fade } from 'svelte/transition';
  import type { KGNode } from '$lib/constants';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { LightragClient } from '$lib/services/lightrag-client';
  import { textureCache } from '$lib/services/TextureCache';
  import { SceneManager } from './renderer/SceneManager';
  import { TIME_BUCKET_SPACING, CHUNK_SIZE, INITIAL_CAMERA_Z } from './renderer/constants';
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
  let timelineOpen = $state(false);
  let currentBucketIdx = $state(-1);

  let timelineEntries = $derived.by(() => {
    if (!timeIndex || timeIndex.indexToLabel.length === 0) return [];
    const labels = timeIndex.indexToLabel;
    return labels.map((label, idx) => ({ idx, label }));
  });

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
      currentBucketIdx = -1;
      dateLabel = null;
      return;
    }
    const labels = timeIndex.indexToLabel;
    // Time buckets are spaced TIME_BUCKET_SPACING chunks apart, so divide the
    // camera chunk Z by the spacing to recover the dense bucket index.
    const bucketIdx = Math.round(camChunkZ / TIME_BUCKET_SPACING);
    if (bucketIdx < 0) {
      currentBucketIdx = 0;
      dateLabel = labels[0];
      return;
    }
    if (bucketIdx >= labels.length) {
      currentBucketIdx = labels.length - 1;
      dateLabel = labels[labels.length - 1];
      return;
    }
    currentBucketIdx = bucketIdx;
    dateLabel = labels[bucketIdx];
  }

  function flyToBucket(bucketIdx: number): void {
    if (!sceneManager || !timeIndex) return;
    const n = timeIndex.indexToLabel.length;
    if (bucketIdx < 0 || bucketIdx >= n) return;
    const targetZ = bucketIdx * TIME_BUCKET_SPACING * CHUNK_SIZE + INITIAL_CAMERA_Z;
    sceneManager.flyTo(targetZ);
    timelineOpen = false;
  }

  function toggleTimeline(): void {
    timelineOpen = !timelineOpen;
  }

  function closeTimeline(): void {
    timelineOpen = false;
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
  <!-- Click-outside / escape backdrop for the timeline dropdown -->
  {#if timelineOpen}
    <div
      class="timeline-backdrop"
      role="button"
      tabindex="-1"
      aria-label="Close timeline"
      onclick={closeTimeline}
      onkeydown={(e) => (e.key === 'Escape' ? closeTimeline() : null)}
    ></div>
  {/if}

  <div class="date-indicator-wrap">
    <button
      type="button"
      class="date-indicator"
      class:is-open={timelineOpen}
      onclick={toggleTimeline}
      aria-haspopup="listbox"
      aria-expanded={timelineOpen}
      aria-label="Open photo timeline"
      data-testid="date-pill"
    >
      <span class="date-indicator-icon" aria-hidden="true">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
      </span>
      {#key dateLabel}
        <span class="date-indicator-label" in:fade={{ duration: 220 }}>{dateLabel}</span>
      {/key}
      <span class="date-indicator-chevron" class:is-open={timelineOpen} aria-hidden="true">
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
      </span>
    </button>

    {#if timelineOpen}
      <div class="timeline-dropdown" role="listbox" aria-label="Photo timeline months" data-testid="timeline-dropdown">
        <ul class="timeline-list">
          {#each timelineEntries as entry (entry.idx)}
            <li>
              <button
                type="button"
                class="timeline-item"
                class:is-current={entry.idx === currentBucketIdx}
                onclick={() => flyToBucket(entry.idx)}
                aria-current={entry.idx === currentBucketIdx ? 'true' : undefined}
              >
                <span class="timeline-dot" class:is-current={entry.idx === currentBucketIdx} aria-hidden="true"></span>
                <span class="timeline-label">{entry.label}</span>
              </button>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
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

  /* ── Date indicator — floating glass pill (clickable) ── */
  .date-indicator-wrap {
    position: absolute;
    right: 16px;
    bottom: 16px;
    z-index: 20;
  }

  .timeline-backdrop {
    position: fixed;
    inset: 0;
    z-index: 19;
    cursor: default;
  }

  .date-indicator {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    background: var(--canvas-glass);
    backdrop-filter: blur(24px) saturate(1.4);
    -webkit-backdrop-filter: blur(24px) saturate(1.4);
    border: 1px solid transparent;
    border-radius: 100px;
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 8%),
      0 8px 32px oklch(0% 0 0 / 30%);
    color: var(--canvas-accent);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    letter-spacing: 0.06em;
    white-space: nowrap;
    cursor: pointer;
    pointer-events: auto;
    transition:
      border-color 0.18s ease,
      box-shadow 0.18s ease,
      transform 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .date-indicator:hover {
    transform: translateY(-1px);
    box-shadow:
      0 0 0 1px oklch(82% 0.14 210 / 35%),
      0 12px 36px oklch(0% 0 0 / 40%);
  }

  .date-indicator.is-open {
    border-color: oklch(82% 0.14 210 / 40%);
    box-shadow:
      0 0 0 1px oklch(82% 0.14 210 / 45%),
      0 12px 36px oklch(0% 0 0 / 45%);
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

  .date-indicator-chevron {
    display: flex;
    align-items: center;
    color: var(--canvas-muted);
    transition: transform 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .date-indicator-chevron.is-open {
    transform: rotate(180deg);
  }

  /* ── Timeline dropdown ── */
  .timeline-dropdown {
    position: absolute;
    right: 0;
    bottom: calc(100% + 10px);
    padding: 10px 12px 10px 10px;
    background: oklch(14% 0.015 255 / 72%);
    backdrop-filter: blur(28px) saturate(1.5);
    -webkit-backdrop-filter: blur(28px) saturate(1.5);
    border-radius: 18px;
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 10%),
      0 18px 56px oklch(0% 0 0 / 50%);
    min-width: 150px;
    max-height: min(56vh, 420px);
    pointer-events: auto;
    transform-origin: bottom right;
    animation: timeline-pop 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  }

  @keyframes timeline-pop {
    from { opacity: 0; transform: translateY(8px) scale(0.96); }
    to   { opacity: 1; transform: translateY(0) scale(1); }
  }

  .timeline-list {
    list-style: none;
    margin: 0;
    padding: 0;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: oklch(50% 0.03 255 / 20%) transparent;
    position: relative;
  }

  .timeline-list > li {
    position: relative;
  }

  /* Continuous connector line through the dot column. Top is inset to the
     first dot's center (padding-top 7 + half-dot 4 = 11px); bottom likewise. */
  .timeline-list::before {
    content: '';
    position: absolute;
    left: 12px;
    top: 11px;
    bottom: 11px;
    width: 1px;
    background: oklch(50% 0.03 255 / 18%);
    transform: translateX(-50%);
    pointer-events: none;
  }

  .timeline-list::-webkit-scrollbar { width: 5px; }
  .timeline-list::-webkit-scrollbar-thumb {
    background: oklch(50% 0.03 255 / 20%);
    border-radius: 3px;
  }

  .timeline-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 7px 12px 7px 8px;
    border: none;
    background: transparent;
    border-radius: 8px;
    color: var(--canvas-muted);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    letter-spacing: 0.04em;
    text-align: left;
    white-space: nowrap;
    cursor: pointer;
    transition: background 0.15s ease, color 0.15s ease;
  }

  .timeline-item:hover {
    background: oklch(82% 0.14 210 / 14%);
    color: var(--canvas-fg);
  }

  .timeline-dot {
    position: relative;
    z-index: 1;
    flex-shrink: 0;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: oklch(40% 0.03 255 / 100%);
    transition: background 0.15s ease, box-shadow 0.15s ease;
  }

  .timeline-dot.is-current {
    background: var(--canvas-accent);
    box-shadow: 0 0 8px oklch(82% 0.14 210 / 60%);
  }

  .timeline-label {
    flex: 1 1 auto;
  }

  .timeline-item.is-current {
    color: var(--canvas-accent);
    font-weight: 600;
    background: oklch(82% 0.14 210 / 10%);
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