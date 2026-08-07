<script lang="ts">
  import { imageProcessingStore, type ImageStage } from '$lib/stores/image-processing.svelte';
  import type { SceneManager } from './renderer/SceneManager';

  let { sceneManager }: { sceneManager: SceneManager } = $props();

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

  const CIRCUMFERENCE = 2 * Math.PI * 21; // ≈ 131.95, rounded to 132 in prototype

  let collapsed = $state(false);
  let dockMobileOpen = $state(false);
  let visible = $state(true);
  let hideTimer: ReturnType<typeof setTimeout> | null = null;
  let completedNodeIds = $state<Set<string>>(new Set());

  const entries = $derived.by(() => {
    return Object.values(imageProcessingStore.statuses);
  });

  const total = $derived(entries.length);

  const completedCount = $derived(
    entries.filter((e) => e.stage === 'complete').length,
  );

  const allDone = $derived(
    total > 0 && entries.every((e) => e.stage === 'complete' || e.stage === 'error'),
  );

  const progressPercent = $derived(
    total === 0 ? 0 : Math.round((completedCount / total) * 100),
  );

  const countLabel = $derived(
    total === 0 ? 'All done' : `${total} image${total !== 1 ? 's' : ''}`,
  );

  const footerLabel = $derived(
    total === 0 ? 'All done' : `${completedCount} of ${total} complete`,
  );

  function getProgressRingOffset(stepper: { state: string }[]): number {
    const done = stepper.filter((s) => s.state === 'done').length;
    const total = stepper.length || 1;
    const progress = done / total;
    return CIRCUMFERENCE * (1 - progress);
  }

  function getStageDotClass(stage: ImageStage): string {
    if (stage === 'complete') return 'complete';
    if (stage === 'error') return 'error';
    return 'active';
  }

  function getThumbClass(stage: ImageStage): string {
    if (stage === 'complete') return 'complete';
    if (stage === 'error') return '';
    return 'processing';
  }

  function getItemClass(stage: ImageStage, nodeId: string): string {
    const classes: string[] = [];
    if (stage === 'complete' && !completedNodeIds.has(nodeId)) {
      classes.push('completed');
    }
    if (stage === 'error') {
      classes.push('error');
    }
    return classes.join(' ');
  }

  function cancelItem(nodeId: string): void {
    imageProcessingStore.remove(nodeId);
  }

  function toggleCollapse(): void {
    collapsed = !collapsed;
    if (collapsed) {
      dockMobileOpen = false;
    }
  }

  function toggleMobileDock() {
    dockMobileOpen = !dockMobileOpen;
    if (dockMobileOpen) {
      collapsed = false;
    }
  }

  function openFromIndicator() {
    dockMobileOpen = true;
    collapsed = false;
  }

  function handleHeaderKeydown(e: KeyboardEvent): void {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggleCollapse();
    }
  }

  // Track newly completed items for fade-out animation
  $effect(() => {
    for (const entry of entries) {
      if (entry.stage === 'complete' && !completedNodeIds.has(entry.nodeId)) {
        // Mark as completed (will trigger fade animation via CSS)
        const id = entry.nodeId;
        setTimeout(() => {
          completedNodeIds = new Set([...completedNodeIds, id]);
        }, 3500); // match CSS animation-delay of 3s + 0.5s duration
      }
    }
  });

  // Auto-hide when all items are done
  $effect(() => {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    if (total === 0) {
      // No items at all — hide immediately
      visible = false;
    } else if (allDone) {
      // All complete/error — hide after 4 seconds
      hideTimer = setTimeout(() => {
        visible = false;
      }, 4000);
    } else {
      // Active processing — ensure visible
      visible = true;
    }
  });

  // Re-show dock when new items appear
  $effect(() => {
    if (total > 0 && !allDone) {
      visible = true;
    }
  });
</script>

{#if visible && total > 0 && collapsed}
  <button
    class="dock-indicator"
    role="button"
    tabindex="0"
    aria-label="Show processing queue"
    onclick={openFromIndicator}
  >
    <div class="dock-indicator-icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48 2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48 2.83-2.83" /></svg>
      <span class="dock-indicator-pulse"></span>
    </div>
    <span class="dock-indicator-count">{countLabel}</span>
  </button>
{/if}

{#if visible && total > 0}
  <div class="processing-dock" class:collapsed class:dock-mobile-open={dockMobileOpen}>
    <div
      class="dock-header"
      role="button"
      tabindex="0"
      aria-label="Toggle processing queue"
      onclick={toggleCollapse}
      onkeydown={handleHeaderKeydown}
    >
      <div class="dock-header-left">
        <div class="dock-header-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48 2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48 2.83-2.83"/></svg>
        </div>
        <span class="dock-header-title">Processing</span>
      </div>
      <div class="dock-header-right">
        <span class="dock-count">{countLabel}</span>
        <span class="dock-chevron">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
        </span>
      </div>
    </div>

    <div class="dock-list">
      {#each entries as entry, i (entry.nodeId)}
        {@const stepper = entry.stepper}
        {@const ringOffset = getProgressRingOffset(stepper)}
        {@const thumbClass = getThumbClass(entry.stage)}
        {@const itemClass = getItemClass(entry.stage, entry.nodeId)}
        {@const stageColor = STAGE_COLOR[entry.stage] ?? '#22d3ee'}

        <div class="dock-item {itemClass}" style="animation-delay: {i * 50}ms">
          <div class="dock-thumb {thumbClass}" style="background-image: url({entry.dataUrl})">
            <svg class="dock-thumb-ring" viewBox="0 0 46 46">
              <circle class="track" cx="23" cy="23" r="21" />
              <circle
                class="fill"
                cx="23" cy="23" r="21"
                stroke-dasharray={CIRCUMFERENCE}
                stroke-dashoffset={entry.stage === 'complete' ? 0 : ringOffset}
                transform="rotate(-90 23 23)"
              />
            </svg>
            {#if entry.stage === 'complete'}
              <div class="dock-thumb-icon check">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
              </div>
            {:else if entry.stage === 'error'}
              <div class="dock-thumb-icon warn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" /></svg>
              </div>
            {/if}
          </div>
          <div class="dock-info">
            <span class="dock-filename">{entry.fileName}</span>
            <div class="dock-stage">
              <span class="dock-stage-dot {getStageDotClass(entry.stage)}" style="background: {stageColor}"></span>
              <span>{entry.stageLabel}</span>
            </div>
            <div class="dock-pipeline">
              {#each stepper as step}
                <div class="dock-pipeline-segment {step.state === 'done' ? 'done' : step.state === 'current' ? 'active' : ''}"></div>
              {/each}
            </div>
            <span class="dock-time">
              {#if entry.stage === 'complete'}
                Done
              {:else if entry.stage === 'error'}
                Failed
              {:else if stepper.filter(s => s.state === 'done').length === 0}
                Waiting…
              {:else}
                ~{Math.max(1, Math.round((stepper.filter(s => s.state === 'pending').length + 0.5) * 3))}s remaining
              {/if}
            </span>
          </div>
          <div class="dock-actions">
            <button class="dock-action-btn" aria-label="Cancel processing" onclick={() => cancelItem(entry.nodeId)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
            </button>
          </div>
        </div>
      {/each}
    </div>

    <div class="dock-footer">
      <span class="dock-footer-text">{footerLabel}</span>
      <div class="dock-footer-bar">
        <div class="dock-footer-bar-fill" style="width: {progressPercent}%"></div>
      </div>
    </div>
  </div>
{/if}

<style>
  /* ── Design tokens ── */
  :global(:root) {
    --dock-bg: oklch(12% 0.015 255 / 75%);
    --dock-glass-blur: blur(24px) saturate(1.5);
    --dock-border: oklch(50% 0.03 255 / 10%);
    --dock-shadow-1: oklch(50% 0.03 255 / 6%);
    --dock-shadow-2: oklch(0% 0 0 / 40%);
    --dock-accent: oklch(82% 0.14 210);
    --dock-accent-dim: oklch(82% 0.14 210 / 18%);
    --dock-purple: oklch(72% 0.16 295);
    --dock-success: oklch(72% 0.15 150);
    --dock-danger: oklch(62% 0.20 18);
    --dock-fg: oklch(90% 0.005 250);
    --dock-muted: oklch(65% 0.02 255);
    --dock-faint: oklch(55% 0.02 255);
    --dock-hairline: oklch(50% 0.03 255 / 8%);
    --dock-mono: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
  }

  .processing-dock {
    position: absolute;
    left: 24px;
    bottom: 24px;
    z-index: 30;
    width: 320px;
    display: flex;
    flex-direction: column;
    gap: 0;
    background: var(--dock-bg);
    backdrop-filter: var(--dock-glass-blur);
    -webkit-backdrop-filter: var(--dock-glass-blur);
    border-radius: 16px;
    border: 1px solid var(--dock-border);
    box-shadow:
      0 0 0 1px var(--dock-shadow-1),
      0 12px 40px var(--dock-shadow-2);
    pointer-events: auto;
    animation: dock-float-in 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
    overflow: hidden;
  }

  /* ── Collapsed state ── */
  .processing-dock.collapsed .dock-list,
  .processing-dock.collapsed .dock-footer {
    max-height: 0;
    opacity: 0;
    overflow: hidden;
    padding: 0;
    margin: 0;
    border: none;
    transition: max-height 0.35s cubic-bezier(0.16, 1, 0.3, 1),
                opacity 0.25s ease;
  }

  .processing-dock:not(.collapsed) .dock-list {
    max-height: 260px;
    opacity: 1;
    transition: max-height 0.35s cubic-bezier(0.16, 1, 0.3, 1),
                opacity 0.25s ease;
  }

  .processing-dock:not(.collapsed) .dock-footer {
    max-height: 40px;
    opacity: 1;
    transition: max-height 0.35s cubic-bezier(0.16, 1, 0.3, 1),
                opacity 0.25s ease;
  }

  /* ── Dock indicator (mobile tap target when dock is collapsed) ── */
  .dock-indicator {
    display: none;
    position: absolute;
    right: 24px;
    bottom: 72px;
    z-index: 31;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    background: oklch(12% 0.015 255 / 75%);
    backdrop-filter: blur(24px) saturate(1.5);
    -webkit-backdrop-filter: blur(24px) saturate(1.5);
    border-radius: 100px;
    border: 1px solid oklch(50% 0.03 255 / 10%);
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 6%),
      0 0 24px oklch(82% 0.14 210 / 15%),
      0 12px 40px oklch(0% 0 0 / 40%);
    color: var(--dock-fg);
    font-family: var(--dock-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    user-select: none;
    transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    animation: dock-float-in 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
  }
  .dock-indicator:hover {
    background: oklch(16% 0.02 255 / 85%);
    box-shadow:
      0 0 0 1px oklch(82% 0.14 210 / 20%),
      0 0 32px oklch(82% 0.14 210 / 25%),
      0 12px 40px oklch(0% 0 0 / 50%);
    transform: scale(1.04);
  }
  .dock-indicator:active {
    transform: scale(0.97);
  }
  .dock-indicator:focus-visible {
    outline: 2px solid var(--dock-accent);
    outline-offset: 2px;
    border-radius: inherit;
  }
  .dock-indicator-icon {
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--dock-accent);
    position: relative;
  }
  .dock-indicator-icon svg { width: 16px; height: 16px; }
  .dock-indicator-pulse {
    position: absolute;
    inset: -2px;
    border-radius: 50%;
    border: 1.5px solid var(--dock-accent);
    opacity: 0;
    animation: indicator-pulse 2s ease-in-out infinite;
  }
  @keyframes indicator-pulse {
    0% { opacity: 0.6; transform: scale(1); }
    100% { opacity: 0; transform: scale(1.8); }
  }
  .dock-indicator-count {
    font-variant-numeric: tabular-nums;
  }

  .processing-dock.collapsed .dock-header {
    border-bottom: none;
    border-radius: 16px;
  }

  /* ── Float-in animation ── */
  @keyframes dock-float-in {
    from { opacity: 0; transform: translateY(24px) scale(0.96); }
    to { opacity: 1; transform: translateY(0) scale(1); }
  }

  /* ── Header ── */
  .dock-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 14px;
    border-bottom: 1px solid var(--dock-hairline);
    flex-shrink: 0;
    cursor: pointer;
    user-select: none;
    transition: background 0.2s;
  }

  .dock-header:hover {
    background: oklch(50% 0.03 255 / 5%);
  }

  .dock-header:focus-visible {
    outline: 2px solid var(--dock-accent);
    outline-offset: -2px;
    border-radius: 16px 16px 0 0;
  }

  .dock-header-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .dock-header-icon {
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--dock-accent);
  }

  .dock-header-icon svg {
    width: 16px;
    height: 16px;
  }

  .dock-header-title {
    font-family: var(--dock-mono);
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--dock-fg);
    font-weight: 600;
  }

  .dock-header-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .dock-count {
    font-family: var(--dock-mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dock-muted);
    background: var(--dock-accent-dim);
    padding: 2px 8px;
    border-radius: 100px;
    font-weight: 500;
  }

  .dock-chevron {
    color: var(--dock-muted);
    transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    display: flex;
    align-items: center;
  }

  .dock-chevron svg {
    width: 16px;
    height: 16px;
  }

  .processing-dock.collapsed .dock-chevron {
    transform: rotate(180deg);
  }

  /* ── Scrollable list ── */
  .dock-list {
    max-height: 260px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--dock-accent-dim) transparent;
    transition: max-height 0.35s cubic-bezier(0.16, 1, 0.3, 1),
                opacity 0.25s ease;
  }

  .dock-list::-webkit-scrollbar {
    width: 3px;
  }

  .dock-list::-webkit-scrollbar-thumb {
    background: var(--dock-accent-dim);
    border-radius: 2px;
  }

  /* ── Item ── */
  .dock-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--dock-hairline);
    transition: background 0.2s;
    animation: item-slide-in 0.35s cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  .dock-item:last-child {
    border-bottom: none;
  }

  .dock-item:hover {
    background: oklch(50% 0.03 255 / 5%);
  }

  @keyframes item-slide-in {
    from { opacity: 0; transform: translateX(-12px); }
    to { opacity: 1; transform: translateX(0); }
  }

  .dock-item.completed {
    animation: item-complete-fade 0.5s ease 3s forwards;
  }

  @keyframes item-complete-fade {
    to {
      opacity: 0;
      transform: translateX(8px);
      max-height: 0;
      padding-top: 0;
      padding-bottom: 0;
      margin: 0;
      border-bottom-width: 0;
      overflow: hidden;
    }
  }

  .dock-item.error .dock-filename {
    color: oklch(72% 0.16 295);
  }

  .dock-item.error .dock-stage {
    color: oklch(62% 0.20 18 / 70%);
  }

  /* ── Thumbnail ── */
  .dock-thumb {
    width: 40px;
    height: 40px;
    border-radius: 8px;
    background-size: cover;
    background-position: center;
    flex-shrink: 0;
    position: relative;
    overflow: hidden;
    box-shadow: 0 0 0 1px oklch(50% 0.03 255 / 10%);
  }

  .dock-thumb::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 8px;
    box-shadow: inset 0 0 0 1px oklch(100% 0 0 / 8%);
  }

  /* Shimmer overlay on processing thumbnails */
  .dock-thumb.processing::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 8px;
    background: linear-gradient(
      105deg,
      oklch(0% 0 0 / 0%) 0%,
      oklch(82% 0.14 210 / 12%) 45%,
      oklch(82% 0.14 210 / 20%) 50%,
      oklch(82% 0.14 210 / 12%) 55%,
      oklch(0% 0 0 / 0%) 100%
    );
    animation: shimmer-sweep 2.2s ease-in-out infinite;
    z-index: 1;
  }

  @keyframes shimmer-sweep {
    0% { transform: translateX(-120%); }
    100% { transform: translateX(120%); }
  }

  /* Complete state — checkmark overlay */
  .dock-thumb.complete::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 8px;
    background: oklch(72% 0.15 150 / 25%);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    z-index: 1;
  }

  .dock-thumb-icon {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 2;
  }

  .dock-thumb-icon svg {
    width: 16px;
    height: 16px;
  }

  .dock-thumb-icon.check svg {
    color: var(--dock-success);
    filter: drop-shadow(0 0 6px oklch(72% 0.15 150 / 50%));
  }

  .dock-thumb-icon.warn svg {
    color: oklch(72% 0.16 85);
    filter: drop-shadow(0 0 6px oklch(72% 0.16 85 / 50%));
  }

  /* Progress ring on thumbnail */
  .dock-thumb-ring {
    position: absolute;
    inset: -3px;
    border-radius: 11px;
  }

  .dock-thumb-ring circle.track {
    fill: none;
    stroke: oklch(50% 0.03 255 / 15%);
    stroke-width: 2;
  }

  .dock-thumb-ring circle.fill {
    fill: none;
    stroke: var(--dock-accent);
    stroke-width: 2;
    stroke-linecap: round;
    transition: stroke-dashoffset 0.6s cubic-bezier(0.16, 1, 0.3, 1);
    filter: drop-shadow(0 0 4px oklch(82% 0.14 210 / 40%));
  }

  /* ── Info ── */
  .dock-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .dock-filename {
    font-family: var(--dock-mono);
    font-size: 11px;
    font-weight: 500;
    color: var(--dock-fg);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    letter-spacing: 0.02em;
  }

  .dock-stage {
    font-family: var(--dock-mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dock-muted);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .dock-stage-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    flex-shrink: 0;
    animation: stage-pulse 1.6s ease-in-out infinite;
  }

  .dock-stage-dot.active {
    background: var(--dock-accent);
  }

  .dock-stage-dot.complete {
    background: var(--dock-success);
    animation: none;
  }

  .dock-stage-dot.error {
    background: var(--dock-danger);
    animation: none;
  }

  @keyframes stage-pulse {
    0%, 100% { opacity: 0.4; transform: scale(0.85); }
    50% { opacity: 1; transform: scale(1); }
  }

  /* ── Pipeline stepper ── */
  .dock-pipeline {
    display: flex;
    align-items: center;
    gap: 3px;
    margin-top: 2px;
  }

  .dock-pipeline-segment {
    flex: 1;
    height: 2px;
    border-radius: 100px;
    background: oklch(50% 0.03 255 / 12%);
    position: relative;
    overflow: hidden;
  }

  .dock-pipeline-segment.done {
    background: var(--dock-accent);
  }

  .dock-pipeline-segment.active {
    background: oklch(50% 0.03 255 / 12%);
  }

  .dock-pipeline-segment.active::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 100px;
    background: var(--dock-accent);
    animation: pipeline-extend 2s ease-in-out infinite;
  }

  @keyframes pipeline-extend {
    0% { transform: scaleX(0); transform-origin: left; }
    50% { transform: scaleX(1); transform-origin: left; }
    51% { transform: scaleX(1); transform-origin: right; }
    100% { transform: scaleX(0); transform-origin: right; }
  }

  /* ── Time estimate ── */
  .dock-time {
    font-family: var(--dock-mono);
    font-size: 9px;
    color: var(--dock-faint);
    letter-spacing: 0.04em;
  }

  /* ── Actions ── */
  .dock-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
    margin-top: 2px;
  }

  .dock-action-btn {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: oklch(50% 0.03 255 / 8%);
    border-radius: 6px;
    color: var(--dock-faint);
    cursor: pointer;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .dock-action-btn:hover {
    background: oklch(50% 0.03 255 / 15%);
    color: var(--dock-fg);
  }

  .dock-action-btn:focus-visible {
    outline: 2px solid var(--dock-accent);
    outline-offset: 2px;
  }

  .dock-action-btn svg {
    width: 12px;
    height: 12px;
  }

  /* ── Footer ── */
  .dock-footer {
    padding: 8px 14px;
    border-top: 1px solid var(--dock-hairline);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
  }

  .dock-footer-text {
    font-family: var(--dock-mono);
    font-size: 10px;
    letter-spacing: 0.06em;
    color: var(--dock-faint);
    text-transform: uppercase;
  }

  .dock-footer-bar {
    flex: 1;
    max-width: 80px;
    height: 2px;
    border-radius: 100px;
    background: oklch(50% 0.03 255 / 10%);
    overflow: hidden;
    margin-left: 10px;
  }

  .dock-footer-bar-fill {
    height: 100%;
    border-radius: 100px;
    background: var(--dock-accent);
    transition: width 0.6s cubic-bezier(0.16, 1, 0.3, 1);
  }

  @media (max-width: 768px) {
    .dock-indicator {
      display: flex;
      right: 16px;
      bottom: 56px;
      padding: 8px 14px;
      font-size: 10px;
    }
    .processing-dock {
      left: 0;
      right: 0;
      bottom: 0;
      width: 100%;
      border-radius: 16px 16px 0 0;
      max-height: 60vh;
    }
    .processing-dock .dock-list {
      max-height: 180px;
    }
    .processing-dock .dock-item {
      padding: 8px 10px;
      gap: 8px;
    }
    .processing-dock .dock-thumb {
      width: 36px;
      height: 36px;
    }
    .processing-dock .dock-thumb-ring {
      inset: -2px;
      border-radius: 10px;
    }
    .processing-dock .dock-filename {
      font-size: 10px;
    }
    .processing-dock .dock-stage {
      font-size: 9px;
    }
    .processing-dock .dock-time {
      font-size: 8px;
    }
    .processing-dock .dock-pipeline {
      gap: 2px;
    }
    .processing-dock .dock-pipeline-segment {
      height: 1.5px;
    }
    .processing-dock .dock-action-btn {
      width: 32px;
      height: 32px;
    }
    .processing-dock .dock-header {
      padding: 10px 12px;
    }
    .processing-dock .dock-footer {
      padding: 6px 12px;
    }
    .processing-dock.collapsed .dock-header {
      border-radius: 16px 16px 0 0;
    }

    .processing-dock.collapsed:not(.dock-mobile-open) {
      display: none;
    }
    .processing-dock.dock-mobile-open {
      display: flex;
      animation: mobile-dock-slide-up 0.35s cubic-bezier(0.16, 1, 0.3, 1) both;
    }
  }

  @keyframes mobile-dock-slide-up {
    from { opacity: 0; transform: translateY(100%); }
    to { opacity: 1; transform: translateY(0); }
  }
</style>