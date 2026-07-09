<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import type { KGNode } from '$lib/constants';

  let {
    graphCanvas,
    containerEl,
  }: {
    graphCanvas: any;
    containerEl: HTMLDivElement;
  } = $props();

  type PhotoCard = {
    nodeId: string;
    dataUrl: string;
    status: string;
    stageLabel: string;
    x: number;
    y: number;
    visible: boolean;
    complete: boolean;
    error: boolean;
    errorMsg: string;
  };

  const STAGE_ICONS: Record<string, string> = {
    attaching: '📷',
    extracting_metadata: '📋',
    creating_entities: '🔷',
    uploading: '☁️',
    processing_ai: '🧠',
    linking_entities: '🔗',
    complete: '✅',
    error: '❌',
  };

  let cards = $state<PhotoCard[]>([]);
  let rafId = 0;

  function updateCards() {
    const graph = graphCanvas?.getGraph?.();
    const rect = containerEl?.getBoundingClientRect();
    if (!graph || !rect) {
      rafId = requestAnimationFrame(updateCards);
      return;
    }

    const photos = graphStore.photoImages;
    const statuses = imageProcessingStore.statuses;
    const newCards: PhotoCard[] = [];

    for (const [nodeId, dataUrl] of Object.entries(photos)) {
      const procStatus = statuses[nodeId];
      const stage = procStatus?.stage ?? 'attaching';
      const stageLabel = procStatus?.stageLabel ?? 'Attaching image...';
      const errorMsg = procStatus?.error ?? '';

      let x = 0;
      let y = 0;
      let visible = false;

      try {
        const data = graph.graphData();
        const node = data?.nodes?.find((n: KGNode) => String(n.id) === nodeId);
        if (node && node.x !== undefined) {
          const coords = graph.graph2ScreenCoords(node.x, node.y, node.z);
          x = coords.x - rect.left;
          y = coords.y - rect.top;
          visible = true;
        }
      } catch {}

      newCards.push({
        nodeId,
        dataUrl,
        status: stage,
        stageLabel,
        x,
        y,
        visible,
        complete: stage === 'complete',
        error: stage === 'error',
        errorMsg,
      });
    }

    cards = newCards;
    rafId = requestAnimationFrame(updateCards);
  }

  onMount(() => {
    rafId = requestAnimationFrame(updateCards);
    return () => cancelAnimationFrame(rafId);
  });

  import { onMount } from 'svelte';
</script>

{#each cards as card (card.nodeId)}
  {#if card.visible}
    <div
      class="photo-card"
      style="left:{card.x + 20}px;top:{card.y - 120}px;"
      class:complete={card.complete}
      class:error={card.error}
    >
      <div class="photo-card-image">
        <img src={card.dataUrl} alt={card.nodeId} />
        {#if !card.complete && !card.error}
          <div class="scan-line"></div>
        {/if}
        {#if card.complete}
          <div class="photo-card-check">✓</div>
        {/if}
        {#if card.error}
          <div class="photo-card-error-badge">!</div>
        {/if}
      </div>
      <div class="photo-card-info">
        <div class="photo-card-filename">{card.nodeId.replace(' (Photo)', '')}</div>
        <div class="photo-card-status" class:status-complete={card.complete} class:status-error={card.error}>
          {#if !card.complete && !card.error}
            <span class="status-spinner"></span>
          {/if}
          {STAGE_ICONS[card.status] ?? '📷'} {card.stageLabel}
        </div>
        {#if card.error && card.errorMsg}
          <div class="photo-card-error-msg">{card.errorMsg}</div>
        {/if}
      </div>
    </div>
  {/if}
{/each}

<style>
  .photo-card {
    position: absolute;
    z-index: 40;
    pointer-events: none;
    display: flex;
    flex-direction: column;
    gap: 8px;
    background: rgba(17, 24, 39, 0.92);
    border: 1px solid rgba(0, 255, 255, 0.3);
    border-radius: 12px;
    padding: 10px;
    backdrop-filter: blur(12px);
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.6), 0 0 12px rgba(0, 255, 255, 0.1);
    transition: border-color 0.4s, box-shadow 0.4s;
    min-width: 240px;
    max-width: 320px;
  }

  .photo-card.complete {
    border-color: rgba(34, 197, 94, 0.5);
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.6), 0 0 12px rgba(34, 197, 94, 0.15);
  }

  .photo-card.error {
    border-color: rgba(255, 51, 102, 0.5);
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.6), 0 0 12px rgba(255, 51, 102, 0.15);
  }

  .photo-card-image {
    position: relative;
    width: 100%;
    aspect-ratio: 4/3;
    flex-shrink: 0;
    border-radius: 8px;
    overflow: hidden;
  }

  .photo-card-image img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    border-radius: 8px;
  }

  .scan-line {
    position: absolute;
    left: 0;
    width: 100%;
    height: 2px;
    background: linear-gradient(90deg, transparent, rgba(0, 255, 255, 0.8), transparent);
    box-shadow: 0 0 8px rgba(0, 255, 255, 0.6);
    animation: scan 1.8s ease-in-out infinite;
  }

  @keyframes scan {
    0% { top: 0; }
    50% { top: calc(100% - 2px); }
    100% { top: 0; }
  }

  .photo-card-check {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(34, 197, 94, 0.25);
    color: #22c55e;
    font-size: 36px;
    font-weight: 700;
    border-radius: 8px;
  }

  .photo-card-error-badge {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(255, 51, 102, 0.25);
    color: #ff3366;
    font-size: 36px;
    font-weight: 700;
    border-radius: 8px;
  }

  .photo-card-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .photo-card-filename {
    font-size: 12px;
    font-weight: 600;
    color: #e2e8f0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 280px;
  }

  .photo-card-status {
    font-size: 11px;
    color: #00ffff;
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .photo-card-status.status-complete {
    color: #22c55e;
  }

  .photo-card-status.status-error {
    color: #ff3366;
  }

  .status-spinner {
    width: 12px;
    height: 12px;
    border: 1.5px solid rgba(0, 255, 255, 0.3);
    border-top-color: #00ffff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }

  .status-complete .status-spinner {
    border-color: rgba(34, 197, 94, 0.3);
    border-top-color: #22c55e;
  }

  .status-error .status-spinner {
    display: none;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .photo-card-error-msg {
    font-size: 10px;
    color: #ff3366;
    max-width: 280px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>