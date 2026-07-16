<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import { API, type KGNode } from '$lib/constants';
  import type GraphCanvas from './GraphCanvas.svelte';

  const KG_API_PROXY_BASE = '/api/kg';

  function isPersonNode(n: KGNode): boolean {
    const et = n.properties?.entity_type;
    if (typeof et === 'string') return et.toLowerCase() === 'person';
    return n.labels?.some((l) => l.toLowerCase() === 'person') ?? false;
  }

  function getNodeName(n: KGNode): string {
    return (n.properties?.name as string) ?? (n.properties?.title as string) ?? n.id;
  }

  function personFaceCropUrl(n: KGNode): string | null {
    const faceId = n.properties?.face_id as string | undefined;
    if (!faceId) return null;
    return `${KG_API_PROXY_BASE}${API.kg.faceCropById(faceId)}`;
  }

  function personFallbackUrl(n: KGNode): string {
    const base = '/api/kg';
    return `${base}${API.kg.faceCrop(n.id)}`;
  }

  function resolvePersonThumbUrl(n: KGNode): string {
    return personFaceCropUrl(n) ?? personFallbackUrl(n);
  }

  /** Person nodes connected to the current photo card via graph edges. */
  let cardPersons = $derived.by<KGNode[]>(() => {
    if (!cardNodeId) return [];
    const nbrEdges = graphStore.edges.filter(
      (e) => e.source === cardNodeId || e.target === cardNodeId
    );
    const nbrIds = new Set(
      nbrEdges.flatMap((e) => [e.source, e.target]).filter((id) => id !== cardNodeId)
    );
    return graphStore.nodes.filter((n) => nbrIds.has(n.id) && isPersonNode(n));
  });

  let {
    graphCanvas,
    containerEl,
    fullscreenUrl = $bindable(null as string | null),
  }: {
    graphCanvas?: GraphCanvas;
    containerEl?: HTMLDivElement;
    fullscreenUrl: string | null;
  } = $props();

  let cardX = $state(0);
  let cardY = $state(0);
  let cardVisible = $state(false);
  let cardScale = $state(1);
  let cardNodeId = $state<string | null>(null);
  let rafId: number | null = null;

  function openFullscreen(url: string) {
    fullscreenUrl = url;
  }

  function closeFullscreen() {
    fullscreenUrl = null;
  }

  /** Show the info card for a photo node. Called from GraphView on hover/select. */
  export function showPhotoCard(nodeId: string) {
    cardNodeId = nodeId;
    cardVisible = true;
    startTracking();
  }

  /** Hide the info card. */
  export function hidePhotoCard() {
    cardNodeId = null;
    cardVisible = false;
    stopTracking();
  }

  function startTracking() {
    stopTracking();
    tick();
  }

  function stopTracking() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  function tick() {
    if (!cardVisible || !cardNodeId || !graphCanvas || !containerEl) {
      stopTracking();
      return;
    }
    const graph = graphCanvas.getGraph();
    if (!graph) { rafId = requestAnimationFrame(tick); return; }

    const data = graph.graphData();
    const node = (data?.nodes as { id: string | number; x?: number; y?: number; z?: number }[] | undefined)
      ?.find(n => String(n.id) === cardNodeId);
    if (!node || node.x === undefined || node.y === undefined || node.z === undefined) {
      rafId = requestAnimationFrame(tick);
      return;
    }

    try {
      const rect = containerEl.getBoundingClientRect();
      const coords = graph.graph2ScreenCoords(node.x, node.y, node.z);
      // Scale: project a point 1 unit above to get pixels-per-world-unit
      const coordsUp = graph.graph2ScreenCoords(node.x, node.y, node.z + 1);
      const pixelsPerUnit = Math.abs(coordsUp.y - coords.y);
      const basePxPerUnit = 12;
      cardScale = Math.max(0.4, Math.min(pixelsPerUnit / basePxPerUnit, 3));

      cardX = coords.x - rect.left + 20;
      cardY = coords.y - rect.top - 80;
    } catch {
      // camera not ready
    }

    rafId = requestAnimationFrame(tick);
  }

  $effect(() => {
    return () => stopTracking();
  });

  const EXIF_DISPLAY_KEYS: Record<string, string> = {
    camera: 'Camera',
    date_taken_friendly: 'Date',
    location: 'Location',
    lens: 'Lens',
    f_number: 'f/',
    iso: 'ISO',
    focal_length: 'Focal Length',
    exposure_time: 'Exposure',
    image_width: 'Width',
    image_height: 'Height',
    flash: 'Flash',
    white_balance: 'White Balance',
    orientation: 'Orientation',
  };

  const exifCache = new Map<string, { label: string; value: string }[] | null>();
  let fetchedExifNodeId = $state<string | null>(null);
  let fetchedExifRows = $state<{ label: string; value: string }[]>([]);

  function formatExifRows(exif: Record<string, unknown>): { label: string; value: string }[] {
    const rows: { label: string; value: string }[] = [];
    for (const [key, displayLabel] of Object.entries(EXIF_DISPLAY_KEYS)) {
      const val = exif[key];
      if (val != null && val !== '') {
        const strVal = String(val);
        if (key === 'f_number') {
          rows.push({ label: displayLabel, value: `f/${strVal}` });
        } else {
          rows.push({ label: displayLabel, value: strVal });
        }
      }
    }
    return rows;
  }

  async function fetchExifForNode(nodeId: string) {
    if (exifCache.has(nodeId)) {
      fetchedExifRows = exifCache.get(nodeId) ?? [];
      fetchedExifNodeId = nodeId;
      return;
    }

    let fileSource: string | undefined;
    if (graphCanvas) {
      const graph = graphCanvas.getGraph();
      const data = graph?.graphData();
      const node = (data?.nodes as { id: string | number; properties?: Record<string, unknown> }[] | undefined)
        ?.find(n => String(n.id) === nodeId);
      fileSource = (node?.properties?.source_id as string) ?? (node?.properties?.file_path as string);
    }
    if (!fileSource) return;

    try {
      const resp = await fetch(`/api/kg${API.kg.photoExif(fileSource)}`);
      if (!resp.ok) {
        exifCache.set(nodeId, null);
        return;
      }
      const exif = await resp.json() as Record<string, unknown>;
      const rows = formatExifRows(exif);
      exifCache.set(nodeId, rows);
      fetchedExifRows = rows;
      fetchedExifNodeId = nodeId;
    } catch {
      exifCache.set(nodeId, null);
    }
  }

  $effect(() => {
    if (cardVisible && cardNodeId) {
      const live = imageProcessingStore.getExifSummary(cardNodeId);
      if (live.length > 0) {
        fetchedExifNodeId = cardNodeId;
        fetchedExifRows = live;
      } else {
        fetchExifForNode(cardNodeId);
      }
    }
  });
</script>

{#if cardVisible && cardNodeId}
  {@const status = imageProcessingStore.statuses[cardNodeId]}
  {@const imageUrl = graphStore.photoImages[cardNodeId]}
  {@const exifRows = fetchedExifNodeId === cardNodeId ? fetchedExifRows : []}
  {@const fileName = status?.fileName ?? cardNodeId.replace(' (Photo)', '') ?? 'Photo'}
  {@const isProcessing = status && status.stage !== 'complete' && status.stage !== 'error'}
  {@const isComplete = status?.stage === 'complete'}
  {@const isError = status?.stage === 'error'}

  <div
    class="photo-card"
    style="left: {cardX}px; top: {cardY}px; transform: scale({cardScale.toFixed(3)}); transform-origin: left top;"
  >
    <!-- Thumbnail -->
    <div class="photo-card-thumb" style="pointer-events: auto;">
      {#if imageUrl}
        <button class="photo-card-thumb-btn" onclick={() => openFullscreen(imageUrl)} aria-label="View full image">
          <img src={imageUrl} alt={fileName} />
        </button>
      {:else}
        <div class="photo-card-thumb-placeholder">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <circle cx="8.5" cy="8.5" r="1.5"/>
            <path d="m21 15-5-5L5 21"/>
          </svg>
        </div>
      {/if}
    </div>

    <!-- Info panel -->
    <div class="photo-card-info" style="pointer-events: auto;">
      <div class="photo-card-filename">{fileName}</div>

      {#if isProcessing}
        <div class="photo-card-status processing">
          <span class="photo-card-spinner"></span>
          <span>{status?.stageLabel ?? 'Processing...'}</span>
        </div>
        {#if status?.stepper}
          <div class="photo-card-stepper">
            {#each status.stepper.filter((s) => s.stage !== 'complete') as step (step.stage)}
              <div class="photo-card-step {step.state}">
                <span class="photo-card-step-dot">
                  {#if step.state === 'done'}
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                  {/if}
                </span>
                <span class="photo-card-step-label">{step.label.replace('...', '')}</span>
              </div>
            {/each}
          </div>
        {/if}
      {:else if isComplete}
        <div class="photo-card-status complete">
          <svg class="photo-card-status-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
          <span>Complete</span>
        </div>
      {:else if isError}
        <div class="photo-card-status error">
          <svg class="photo-card-status-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          <span>{status?.error ?? 'Failed'}</span>
        </div>
      {/if}

      {#if exifRows.length > 0}
        <div class="photo-card-exif">
          {#each exifRows as row (row.label)}
            <div class="photo-card-exif-row">
              <span class="photo-card-exif-label">{row.label}</span>
              <span class="photo-card-exif-value">{row.value}</span>
            </div>
          {/each}
        </div>
      {/if}

      {#if cardPersons.length > 0}
        <div class="photo-card-faces">
          {#each cardPersons as person (person.id)}
            <div class="photo-card-face" title={getNodeName(person)}>
              <img
                src={resolvePersonThumbUrl(person)}
                alt={getNodeName(person)}
                class="photo-card-face-img"
                onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
              />
              <span class="photo-card-face-name">{getNodeName(person)}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}

{#if fullscreenUrl}
  <div class="fullscreen-overlay" onclick={closeFullscreen} role="dialog" aria-label="Full screen image">
    <img src={fullscreenUrl} alt="Full screen image" class="fullscreen-img" onclick={(e) => e.stopPropagation()} />
    <button class="fullscreen-close" onclick={closeFullscreen} aria-label="Close">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
{/if}

<style>
  .photo-card {
    position: absolute;
    z-index: 100;
    display: flex;
    gap: 2px;
    pointer-events: none;
    animation: card-in 0.25s cubic-bezier(0.16, 1, 0.3, 1) both;
    will-change: transform, left, top;
    transition: left 0.06s ease-out, top 0.06s ease-out;
  }

  @keyframes card-in {
    from { opacity: 0; transform: scale(0.9); }
    to { opacity: 1; transform: scale(1); }
  }

  .photo-card-thumb {
    display: flex;
    flex-shrink: 0;
    align-items: stretch;
  }

  .photo-card-thumb-btn {
    padding: 0;
    margin: 0;
    border: 1.5px solid rgba(0, 212, 255, 0.3);
    border-radius: 8px 0 0 8px;
    overflow: hidden;
    background: rgba(10, 14, 23, 0.9);
    cursor: pointer;
    transition: border-color 0.2s, box-shadow 0.2s;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .photo-card-thumb-btn:hover {
    border-color: rgba(0, 212, 255, 0.8);
    box-shadow: 0 0 20px rgba(0, 212, 255, 0.3);
  }

  .photo-card-thumb-btn img {
    display: block;
    width: 140px;
    height: 105px;
    object-fit: contain;
  }

  .photo-card-thumb-placeholder {
    width: 100px;
    height: 80px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(10, 14, 23, 0.9);
    border: 1.5px solid rgba(0, 212, 255, 0.3);
    border-radius: 8px 0 0 8px;
    color: rgba(148, 163, 184, 0.5);
  }

  .photo-card-thumb-placeholder svg {
    width: 28px;
    height: 28px;
  }

  .photo-card-info {
    min-width: 160px;
    max-width: 220px;
    padding: 10px 12px;
    background: rgba(15, 23, 42, 0.92);
    border: 1.5px solid rgba(0, 212, 255, 0.25);
    border-left: none;
    border-radius: 0 8px 8px 0;
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    color: #c8d6e5;
    font-size: 12px;
    line-height: 1.4;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .photo-card-filename {
    font-weight: 700;
    font-size: 13px;
    color: #00d4ff;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .photo-card-status {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    color: #94a3b8;
  }

  .photo-card-status.complete { color: #4ade80; }
  .photo-card-status.error { color: #f87171; }

  .photo-card-status-icon {
    width: 13px;
    height: 13px;
    flex-shrink: 0;
  }

  .photo-card-spinner {
    width: 13px;
    height: 13px;
    border: 2px solid rgba(0, 212, 255, 0.2);
    border-top-color: #00d4ff;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .photo-card-stepper {
    display: flex;
    flex-direction: column;
    gap: 3px;
    border-top: 1px solid rgba(0, 212, 255, 0.12);
    padding-top: 5px;
  }

  .photo-card-step {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 10px;
    color: #475569;
    transition: color 0.2s;
  }

  .photo-card-step.done { color: #4ade80; }

  .photo-card-step.current {
    color: #00d4ff;
  }

  .photo-card-step.current .photo-card-step-dot {
    border-color: #00d4ff;
    box-shadow: 0 0 6px rgba(0, 212, 255, 0.5);
  }

  .photo-card-step.current .photo-card-step-label {
    font-weight: 600;
  }

  .photo-card-step-dot {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    border: 1.5px solid currentColor;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: border-color 0.2s, box-shadow 0.2s;
  }

  .photo-card-step.done .photo-card-step-dot {
    background: #4ade80;
    border-color: #4ade80;
  }

  .photo-card-step-dot svg {
    width: 7px;
    height: 7px;
    color: #0f172a;
  }

  .photo-card-step-label {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .photo-card-exif {
    display: flex;
    flex-direction: column;
    gap: 2px;
    border-top: 1px solid rgba(0, 212, 255, 0.12);
    padding-top: 4px;
  }

  .photo-card-exif-row {
    display: flex;
    justify-content: space-between;
    gap: 6px;
    align-items: baseline;
  }

  .photo-card-exif-label {
    color: #94a3b8;
    font-size: 10px;
    flex-shrink: 0;
  }

  .photo-card-exif-value {
    color: #c8d6e5;
    font-size: 11px;
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .photo-card-faces {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    border-top: 1px solid rgba(0, 212, 255, 0.12);
    padding-top: 5px;
  }

  .photo-card-face {
    display: flex;
    align-items: center;
    gap: 3px;
    max-width: 100%;
  }

  .photo-card-face-img {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    object-fit: cover;
    border: 1px solid rgba(0, 212, 255, 0.3);
    flex-shrink: 0;
  }

  .photo-card-face-name {
    font-size: 10px;
    color: #c8d6e5;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Fullscreen */
  .fullscreen-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: rgba(0, 0, 0, 0.92);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
    animation: fs-in 0.2s ease-out;
  }

  @keyframes fs-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .fullscreen-img {
    max-width: 95vw;
    max-height: 95vh;
    object-fit: contain;
    border-radius: 4px;
    cursor: default;
  }

  .fullscreen-close {
    position: fixed;
    top: 16px;
    right: 16px;
    width: 40px;
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 50%;
    cursor: pointer;
    color: #fff;
    transition: background 0.2s;
  }

  .fullscreen-close:hover {
    background: rgba(255, 255, 255, 0.2);
  }

  .fullscreen-close svg {
    width: 20px;
    height: 20px;
  }
</style>