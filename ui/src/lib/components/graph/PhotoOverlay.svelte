<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';

  let {
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
    complete: boolean;
    error: boolean;
    errorMsg: string;
    exifRows: { label: string; value: string }[];
    showExif: boolean;
  };

  let cards = $state<PhotoCard[]>([]);

  $effect(() => {
    const photos = graphStore.photoImages;
    const statuses = imageProcessingStore.statuses;
    const newCards: PhotoCard[] = [];

    for (const [nodeId, dataUrl] of Object.entries(photos)) {
      const procStatus = statuses[nodeId];
      const stage = procStatus?.stage ?? 'extracting_metadata';
      const stageLabel = procStatus?.stageLabel ?? 'Analyzing image...';
      const errorMsg = procStatus?.error ?? '';
      const exifRows = imageProcessingStore.getExifSummary(nodeId);
      const showExif = stage !== 'extracting_metadata' && exifRows.length > 0;

      newCards.push({
        nodeId,
        dataUrl,
        status: stage,
        stageLabel,
        complete: stage === 'complete',
        error: stage === 'error',
        errorMsg,
        exifRows,
        showExif,
      });
    }

    cards = newCards;
  });
</script>

{#each cards as card (card.nodeId)}
  <div class="photo-overlay" class:complete={card.complete} class:error={card.error}>
    <div class="photo-image-wrapper">
      <img src={card.dataUrl} alt={card.nodeId} />
      {#if !card.complete && !card.error}
        <div class="progress-bar"><div class="progress-bar-inner"></div></div>
      {/if}
      {#if card.complete}
        <div class="photo-badge-check">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
      {/if}
      {#if card.error}
        <div class="photo-badge-error">!</div>
      {/if}
    </div>
    <div class="photo-info">
      <div class="photo-filename">{card.nodeId.replace(' (Photo)', '')}</div>
      <div class="photo-status" class:status-done={card.complete} class:status-err={card.error}>
        {#if !card.complete && !card.error}
          <span class="spinner"></span>
        {/if}
        {card.stageLabel}
      </div>
      {#if card.error && card.errorMsg}
        <div class="photo-error-msg">{card.errorMsg}</div>
      {/if}
      {#if card.showExif}
        <div class="photo-exif">
          {#each card.exifRows as row}
            <div class="exif-row">
              <span class="exif-label">{row.label}</span>
              <span class="exif-value">{row.value}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/each}

<style>
  .photo-overlay {
    position: fixed;
    top: 45%;
    left: 50%;
    transform: translate(-50%, -50%);
    z-index: 50;
    display: flex;
    flex-direction: column;
    gap: 16px;
    background: #1e1e2e;
    border: 1px solid #3a3a4e;
    border-radius: 16px;
    padding: 16px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4), 0 4px 16px rgba(0, 0, 0, 0.3);
    min-width: 380px;
    max-width: 480px;
    animation: overlay-in 0.25s ease-out;
    pointer-events: none;
  }

  @keyframes overlay-in {
    from {
      opacity: 0;
      transform: translate(-50%, -50%) scale(0.95);
    }
    to {
      opacity: 1;
      transform: translate(-50%, -50%) scale(1);
    }
  }

  .photo-overlay.complete {
    border-color: #4ade80;
  }

  .photo-overlay.error {
    border-color: #f87171;
  }

  .photo-image-wrapper {
    position: relative;
    width: 100%;
    aspect-ratio: 4 / 3;
    border-radius: 10px;
    overflow: hidden;
    background: #2a2a3a;
  }

  .photo-image-wrapper img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }

  .progress-bar {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: #3a3a4e;
  }

  .progress-bar-inner {
    height: 100%;
    background: #60a5fa;
    animation: progress-slide 1.5s ease-in-out infinite;
    width: 40%;
    border-radius: 0 2px 2px 0;
  }

  @keyframes progress-slide {
    0% { transform: translateX(-100%); }
    50% { width: 60%; }
    100% { transform: translateX(350%); }
  }

  .photo-badge-check {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(74, 222, 128, 0.2);
  }

  .photo-badge-check svg {
    width: 48px;
    height: 48px;
    color: #4ade80;
  }

  .photo-badge-error {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(248, 113, 113, 0.2);
    color: #f87171;
    font-size: 48px;
    font-weight: 700;
  }

  .photo-info {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }

  .photo-filename {
    font-size: 15px;
    font-weight: 600;
    color: #f0f0f5;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .photo-status {
    font-size: 13px;
    font-weight: 500;
    color: #60a5fa;
    display: flex;
    align-items: center;
    gap: 8px;
  }

    .photo-status.status-done {
    color: #4ade80;
  }

  .photo-status.status-err {
    color: #f87171;
  }

  .photo-status.status-err {
    color: #f87171;
  }

  .spinner {
    width: 14px;
    height: 14px;
    border: 2px solid #3a3a4e;
    border-top-color: #60a5fa;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }

  .status-done .spinner {
    border-color: #4a6e4a;
    border-top-color: #4ade80;
  }

  .status-err .spinner {
    display: none;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .photo-error-msg {
    font-size: 12px;
    color: #f87171;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .photo-exif {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 4px;
    padding: 12px;
    background: #262637;
    border: 1px solid #3a3a4e;
    border-radius: 8px;
  }

  .exif-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    font-size: 13px;
    line-height: 1.5;
  }

  .exif-label {
    color: #8888a0;
    flex-shrink: 0;
    min-width: 80px;
    font-weight: 500;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .exif-value {
    color: #e0e0f0;
    font-weight: 600;
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>