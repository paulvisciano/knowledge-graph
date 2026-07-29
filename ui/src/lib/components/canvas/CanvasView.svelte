<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { untrack } from 'svelte';
  import { fade } from 'svelte/transition';
  import type { KGNode } from '$lib/constants';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { syncClient } from '$lib/services/sync-client.svelte';
  import { LightragClient } from '$lib/services/lightrag-client';
  import { textureCache } from '$lib/services/TextureCache';
  import { SceneManager } from './renderer/SceneManager';
  import { TIME_BUCKET_SPACING, CHUNK_SIZE, INITIAL_CAMERA_Z } from './renderer/constants';
  import { buildCanvasLayout, buildTimeIndex } from './Layout';
  import type { TimeIndex } from './Layout';
  import { configStore } from '$lib/stores/config.svelte';
  import { isMobile } from '$lib/composables/use-breakpoint';
  import NodeOverlay from './NodeOverlay.svelte';
  import ProcessingOverlay from './ProcessingOverlay.svelte';
  import type { CanvasNode } from './renderer/types';

  const client = new LightragClient();
  let loadError = $state<string | null>(null);
  let loaded = $state(false);

  let {
    onqueryAbout = (_node: KGNode) => {},
    onselectconversation = (_id: string) => {}
  }: {
    onqueryAbout?: (node: KGNode) => void;
    onselectconversation?: (id: string) => void;
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

  let timeIndex = $state<TimeIndex | null>(null);
  let dateLabel = $state<string | null>(null);
  let timelineOpen = $state(false);
  let currentBucketIdx = $state(-1);
  let doubleTapPhase = $state(0);
  let doubleTapReturnZ = $state(0);
  let doubleTapOriginIdx = -1;

  let timelineEntries = $derived.by(() => {
    if (!timeIndex || timeIndex.indexToLabel.length === 0) return [];
    // timeIndex is oldest→newest; reverse so newest appears first in the dropdown.
    const labels = timeIndex.indexToLabel;
    return labels.map((label, idx) => ({ idx, label })).reverse();
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
    sm.setPinchSensitivity(configStore.pinchZoomSensitivity);
    sm.onSelectNode = (nodeId) => {
      if (nodeId) {
        const cn = sm.getCanvasNode(nodeId);
        const kg = graphStore.nodes.find((n) => n.id === nodeId);
        const isConversation = cn?.kind === 'conversation'
          || kg?.properties?.entity_type === 'Conversation';
        if (isConversation) {
          onselectconversation(nodeId);
          return;
        }
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
    sm.onDoubleTap = (x, y) => {
      handleDoubleTap(x, y);
    };
  }

  function handleDoubleTap(x: number, y: number): void {
    if (!sceneManager || !timeIndex || currentBucketIdx < 0) return;
    const sm = sceneManager;
    const bucketZ = (idx: number) => idx * TIME_BUCKET_SPACING * CHUNK_SIZE + INITIAL_CAMERA_Z;

    if (doubleTapPhase === 0) {
      doubleTapReturnZ = sm.basePosZ;
      doubleTapOriginIdx = currentBucketIdx;
      const withinZ = Math.max(sm.minCameraZ, bucketZ(currentBucketIdx) + CHUNK_SIZE * 0.5);
      sm.flyToXYZ(sm.basePosX, sm.basePosY, withinZ, 500);
      doubleTapPhase = 1;
      return;
    }

    const stepBack = doubleTapOriginIdx - doubleTapPhase;
    if (stepBack < 0) {
      sm.flyToXYZ(sm.basePosX, sm.basePosY, doubleTapReturnZ, 600);
      doubleTapPhase = 0;
      return;
    }
    sm.flyToXYZ(sm.basePosX, sm.basePosY, bucketZ(stepBack), 600);
    doubleTapPhase++;
  }

  function updateDateLabel(camChunkZ: number): void {
    if (!timeIndex || timeIndex.indexToLabel.length === 0) {
      currentBucketIdx = -1;
      dateLabel = null;
      updatePinchBounds(-1);
      return;
    }
    const labels = timeIndex.indexToLabel;
    const bucketIdx = Math.round(camChunkZ / TIME_BUCKET_SPACING);
    if (bucketIdx < 0) {
      currentBucketIdx = 0;
      dateLabel = labels[0];
      updatePinchBounds(0);
      return;
    }
    if (bucketIdx >= labels.length) {
      currentBucketIdx = labels.length - 1;
      dateLabel = labels[labels.length - 1];
      updatePinchBounds(labels.length - 1);
      return;
    }
    currentBucketIdx = bucketIdx;
    dateLabel = labels[bucketIdx];
    updatePinchBounds(bucketIdx);
  }

  function updatePinchBounds(bucketIdx: number): void {
    if (!sceneManager || bucketIdx < 0) {
      sceneManager?.setPinchZoomBounds(null, null);
      return;
    }
    const bucketStartZ = bucketIdx * TIME_BUCKET_SPACING * CHUNK_SIZE + INITIAL_CAMERA_Z;
    sceneManager.setPinchZoomBounds(
      Math.max(sceneManager.minCameraZ, bucketStartZ + CHUNK_SIZE * 0.25),
      bucketStartZ + TIME_BUCKET_SPACING * CHUNK_SIZE,
    );
  }

  function flyToBucket(bucketIdx: number): void {
    if (!sceneManager || !timeIndex) return;
    const n = timeIndex.indexToLabel.length;
    if (bucketIdx < 0 || bucketIdx >= n) return;
    const targetZ = bucketIdx * TIME_BUCKET_SPACING * CHUNK_SIZE + INITIAL_CAMERA_Z;
    sceneManager.flyTo(targetZ);
    timelineOpen = false;
    doubleTapPhase = 0;
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
      undefined,
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

  /** Initial graph fetch — mirrors GraphView's popular-labels fallback.
   *  Also seeds `graphStore` with one node per `syncClient.conversations`
   *  entry (chat-conversation nodes) so the canvas shows conversations even
   *  when LightRAG has no photos. */
  async function loadGraph(): Promise<void> {
    loadError = null;
    try {
      try {
        await syncClient.init();
      } catch {
        // Sync backend optional — continue without conversations.
      }
      let label: string | undefined;
      try {
        const popular = await client.getPopularLabels(1);
        label = popular?.[0];
      } catch {
        const labels = await client.getLabels();
        label = labels?.[0];
      }
      await graphStore.loadGraph(label);
      graphStore.loadConversations();
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
    if (activeFlyTimer) {
      clearTimeout(activeFlyTimer);
      activeFlyTimer = null;
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

  // Push pinch sensitivity changes from configStore into the live SceneManager.
  $effect(() => {
    const s = configStore.pinchZoomSensitivity;
    sceneManager?.setPinchSensitivity(s);
  });

  // Separately track syncClient.conversations and re-merge conversation nodes
  // whenever the sync list changes (new conversation saved / existing one
  // deleted). `untrack` wraps the merge so the write to `graphStore.nodes`
  // inside `loadConversations` does NOT create a transitive reactive loop
  // with the graphStore.nodes effect above (Svelte 5 would otherwise detect
  // the cycle and tear down both effects, freezing `loaded`'s DOM update).
  $effect(() => {
    void syncClient.conversations;
    if (!mounted) return;
    untrack(() => graphStore.loadConversations());
  });

  // Fly the camera to the active conversation node whenever the active id
  // changes. The layout rebuild is throttled, so the node may not be mounted
  // in the chunk manager on the first frame — retry on the next frame until
  // the plane is found or a short timeout elapses.
  let activeFlyTimer: ReturnType<typeof setTimeout> | null = null;
  let firstActiveSeen = false;
  $effect(() => {
    const activeId = graphStore.activeConversationId;
    if (!mounted || !sceneManager || !activeId) return;
    if (!firstActiveSeen) {
      firstActiveSeen = true;
      return;
    }
    if (activeFlyTimer) {
      clearTimeout(activeFlyTimer);
      activeFlyTimer = null;
    }
    const sm = sceneManager;
    let attempts = 0;
    const tryFly = () => {
      activeFlyTimer = null;
      if (sm.getCanvasNode(activeId)) {
        sm.flyToNode(activeId);
        return;
      }
      if (attempts++ < 20) {
        activeFlyTimer = setTimeout(tryFly, 100);
      }
    };
    activeFlyTimer = setTimeout(tryFly, 220);
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
  <div class="hover-tooltip show" style="left: {tooltipX + 14}px; top: {tooltipY + 14}px;" data-od-id="hover-tooltip">
    Click to view details
  </div>
{/if}

{#if dateLabel}
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

  <div
    class="timeline-bar"
    class:collapsed={!timelineOpen}
    data-od-id="timeline-bar"
    data-testid="timeline-bar"
  >
    <div
      class="timeline-header"
      role="button"
      tabindex="0"
      aria-label="Toggle timeline"
      aria-expanded={timelineOpen}
      onclick={toggleTimeline}
      onkeydown={(e) => (e.key === 'Enter' || e.key === ' ' ? toggleTimeline() : null)}
      data-od-id="timeline-header"
    >
      <span class="timeline-header-label" data-od-id="timeline-header-label">
        {#key dateLabel}
          <span in:fade={{ duration: 220 }}>{dateLabel}</span>
        {/key}
      </span>
      <span class="timeline-header-chevron" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
      </span>
    </div>

    <div class="timeline-track" role="listbox" aria-label="Photo timeline" data-od-id="timeline-track" data-testid="timeline-dropdown">
      {#each timelineEntries as entry, i (entry.idx)}
        <button
          type="button"
          class="timeline-tick has-content"
          class:active={entry.idx === currentBucketIdx}
          onclick={() => flyToBucket(entry.idx)}
          aria-current={entry.idx === currentBucketIdx ? 'true' : undefined}
          data-od-id="timeline-tick"
        >
          <span class="timeline-tick-dot" aria-hidden="true"></span>
          <span class="timeline-tick-label">{entry.label}</span>
        </button>
      {/each}
    </div>
  </div>
{/if}

{#if loaded && !isEmpty && !loadError && !$isMobile}
  <div class="zoom-hint" data-od-id="zoom-hint">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
    <span>Scroll to zoom through time · Drag to pan</span>
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
    --canvas-accent:           oklch(82% 0.14 210);
    --canvas-accent-dim:       oklch(82% 0.14 210 / 18%);
    --canvas-accent-purple:    oklch(72% 0.16 295);
    --canvas-accent-purple-dim: oklch(72% 0.16 295 / 18%);
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
    opacity: 0;
    transition: opacity 0.12s ease;
  }
  .hover-tooltip.show { opacity: 1; }

  /* ── Timeline backdrop (click-outside / escape) ── */
  .timeline-backdrop {
    position: fixed;
    inset: 0;
    z-index: 19;
    cursor: default;
  }

  /* ── Timeline bar — right-side vertical collapsible glass panel ── */
  .timeline-bar {
    position: absolute;
    right: 24px;
    top: 24px;
    z-index: 20;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 0;
    padding: 0;
    width: 180px;
    background: oklch(12% 0.015 255 / 75%);
    backdrop-filter: blur(24px) saturate(1.5);
    -webkit-backdrop-filter: blur(24px) saturate(1.5);
    border-radius: 16px;
    border: 1px solid oklch(50% 0.03 255 / 25%);
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 10%),
      0 12px 40px oklch(0% 0 0 / 40%);
    pointer-events: auto;
    transition: opacity 0.3s, transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    overflow: hidden;
  }

  .timeline-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    cursor: pointer;
    border-bottom: 1px solid var(--canvas-hairline);
    flex-shrink: 0;
  }
  .timeline-header:focus-visible {
    outline: 2px solid var(--canvas-accent);
    outline-offset: -2px;
    border-radius: inherit;
  }

  .timeline-header-label {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 14px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--canvas-accent);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .timeline-header-chevron {
    display: flex;
    align-items: center;
    color: var(--canvas-muted);
    transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .timeline-header-chevron svg { width: 16px; height: 16px; }
  .timeline-bar:not(.collapsed) .timeline-header-chevron { transform: rotate(180deg); }

  .timeline-track {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 1px;
    position: relative;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--canvas-accent-dim) transparent;
    max-height: 0;
    transition: max-height 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .timeline-track::-webkit-scrollbar { width: 3px; }
  .timeline-track::-webkit-scrollbar-thumb {
    background: var(--canvas-accent-dim);
    border-radius: 2px;
  }
  .timeline-bar:not(.collapsed) .timeline-track { max-height: 520px; }

  /* On mobile, line up with the fixed menu trigger (top: 12px) on the right edge */
  @media (max-width: 768px) {
    .timeline-bar {
      right: 12px;
      top: 12px;
    }
  }

  .timeline-tick {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: 12px;
    padding: 11px 16px;
    border: none;
    background: transparent;
    border-radius: 0;
    cursor: pointer;
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    position: relative;
    flex-shrink: 0;
    text-align: left;
    min-height: 44px;
  }
  .timeline-tick:focus-visible {
    outline: 2px solid var(--canvas-accent);
    outline-offset: -2px;
    border-radius: inherit;
  }

  .timeline-tick-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: oklch(50% 0.03 255 / 30%);
    transition: all 0.25s;
    flex-shrink: 0;
  }

  .timeline-tick-label {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 13px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--canvas-faint);
    white-space: nowrap;
    transition: color 0.25s;
    font-variant-numeric: tabular-nums;
  }

  .timeline-tick:hover .timeline-tick-dot {
    background: var(--canvas-accent);
    box-shadow: 0 0 6px oklch(82% 0.14 210 / 40%);
  }
  .timeline-tick:hover .timeline-tick-label { color: var(--canvas-fg); }

  .timeline-tick.active { background: oklch(82% 0.14 210 / 10%); }
  .timeline-tick.active .timeline-tick-dot {
    background: var(--canvas-accent);
    box-shadow: 0 0 8px oklch(82% 0.14 210 / 50%);
    width: 8px;
    height: 8px;
  }
  .timeline-tick.active .timeline-tick-label {
    color: var(--canvas-accent);
    font-weight: 600;
  }

  .timeline-tick.has-content .timeline-tick-dot {
    background: oklch(50% 0.03 255 / 50%);
  }

  .timeline-divider {
    width: 100%;
    height: 1px;
    background: oklch(50% 0.03 255 / 12%);
    flex-shrink: 0;
    margin: 2px 0;
  }

  /* ── Zoom hint — glass pill (bottom-left) ── */
  .zoom-hint {
    position: absolute;
    left: 16px;
    bottom: 16px;
    z-index: 20;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: var(--canvas-glass);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border-radius: 100px;
    box-shadow: 0 0 0 1px oklch(50% 0.03 255 / 8%);
    color: var(--canvas-faint);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    letter-spacing: 0.04em;
    pointer-events: none;
    animation: float-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.5s both;
    transition: opacity 0.4s;
  }
  .zoom-hint svg { color: var(--canvas-muted); }

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