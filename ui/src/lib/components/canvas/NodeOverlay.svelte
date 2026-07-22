<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import { API, type KGNode } from '$lib/constants';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
  import type { CanvasNode } from './renderer/types';
  import { classifyKind } from './Layout';

  const KG_API_PROXY_BASE = '/api/kg';

  function getNodeName(n: KGNode): string {
    return (n.properties?.name as string) ?? (n.properties?.title as string) ?? n.id;
  }

  function personFaceCropUrl(n: KGNode): string | null {
    const faceId = n.properties?.face_id as string | undefined;
    if (!faceId) return null;
    return `${KG_API_PROXY_BASE}${API.kg.faceCropById(faceId)}`;
  }

  function personFallbackUrl(n: KGNode): string {
    return `${KG_API_PROXY_BASE}${API.kg.faceCrop(n.id)}`;
  }

  function resolvePersonThumbUrl(n: KGNode): string {
    return personFaceCropUrl(n) ?? personFallbackUrl(n);
  }

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

  let {
    node,
    kgNode,
    onClose,
  }: {
    node: CanvasNode | null;
    kgNode: KGNode | null;
    onClose: () => void;
  } = $props();

  const exifCache = new Map<string, { label: string; value: string }[] | null>();
  let fetchedExifNodeId = $state<string | null>(null);
  let fetchedExifRows = $state<{ label: string; value: string }[]>([]);
  let fullscreenUrl = $state<string | null>(null);

  async function fetchExifForNode(nodeId: string, fileSource: string) {
    if (exifCache.has(nodeId)) {
      fetchedExifRows = exifCache.get(nodeId) ?? [];
      fetchedExifNodeId = nodeId;
      return;
    }
    try {
      const resp = await fetch(`/api/kg${API.kg.photoExif(fileSource)}`);
      if (!resp.ok) {
        exifCache.set(nodeId, null);
        return;
      }
      const exif = (await resp.json()) as Record<string, unknown>;
      const rows = formatExifRows(exif);
      exifCache.set(nodeId, rows);
      fetchedExifRows = rows;
      fetchedExifNodeId = nodeId;
    } catch {
      exifCache.set(nodeId, null);
    }
  }

  let neighborNodes = $derived.by<KGNode[]>(() => {
    const id = node?.id;
    if (!id) return [];
    const nbrEdges = graphStore.edges.filter(
      (e) => e.source === id || e.target === id,
    );
    const nbrIds = new Set(
      nbrEdges.flatMap((e) => [e.source, e.target]).filter((nid) => nid !== id),
    );
    return graphStore.nodes.filter((n) => nbrIds.has(n.id));
  });

  let persons = $derived(neighborNodes.filter((n) => classifyKind(n) === 'person'));
  let locations = $derived(neighborNodes.filter((n) => classifyKind(n) === 'location'));
  let events = $derived(neighborNodes.filter((n) => classifyKind(n) === 'event'));

  let fileName = $derived(
    (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string) ??
      node?.id ??
      'Photo',
  );

  $effect(() => {
    const id = node?.id;
    if (!id) return;
    const live = imageProcessingStore.getExifSummary(id);
    if (live.length > 0) {
      fetchedExifNodeId = id;
      fetchedExifRows = live;
      return;
    }
    const fileSource =
      (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string);
    if (fileSource) {
      fetchExifForNode(id, fileSource);
    }
  });

  function openFullscreen(url: string) {
    fullscreenUrl = url;
  }

  function closeFullscreen() {
    fullscreenUrl = null;
  }

  function handleClose() {
    fullscreenUrl = null;
    onClose();
  }

  let deleting = $state(false);

  async function handleDelete() {
    const fileSource =
      (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string) ??
      node?.id;
    if (!fileSource || deleting) return;
    deleting = true;
    try {
      const docId = await lightragClient.resolveDocumentId(fileSource);
      if (docId) {
        await lightragClient.deleteDocument(docId);
      }
      try {
        await kgApiClient.deletePhotoEntities(fileSource);
      } catch {
        // Best-effort cleanup — entity deletion may fail if already gone
      }
      graphStore.refresh();
      fullscreenUrl = null;
      onClose();
    } catch (err) {
      console.error('[NodeOverlay] Delete failed:', err);
    } finally {
      deleting = false;
    }
  }
</script>

{#if node}
  {@const status = imageProcessingStore.statuses[node.id]}
  {@const imageUrl = node.imageUrl ?? graphStore.photoImages[node.id]}
  {@const fullUrl = node.fullUrl ?? (imageUrl ? imageUrl.replace(/([?&]w=)\d+\b/, '$1full') : undefined)}
  {@const exifRows = fetchedExifNodeId === node.id ? fetchedExifRows : []}
  {@const isProcessing = status && status.stage !== 'complete' && status.stage !== 'error'}
  {@const isComplete = status?.stage === 'complete'}
  {@const isError = status?.stage === 'error'}

  <div class="node-overlay" role="dialog" aria-label="Photo details">
    <button class="overlay-close" onclick={handleClose} aria-label="Close">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>

    <div class="overlay-thumb">
      {#if imageUrl}
        <img src={imageUrl} alt={fileName} />
      {:else}
        <div class="overlay-thumb-placeholder">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <circle cx="8.5" cy="8.5" r="1.5"/>
            <path d="m21 15-5-5L5 21"/>
          </svg>
        </div>
      {/if}
    </div>

    {#if fullUrl}
      <button class="full-btn" onclick={() => openFullscreen(fullUrl)}>View full image</button>
    {/if}
    <button class="delete-btn" onclick={handleDelete} disabled={deleting}>
      {#if deleting}
        <span class="spinner"></span>
        Deleting...
      {:else}
        Delete
      {/if}
    </button>

    <div class="overlay-body">
      <div class="overlay-filename">{fileName}</div>

      {#if isProcessing}
        <div class="status processing">
          <span class="spinner"></span>
          <span>{status?.stageLabel ?? 'Processing...'}</span>
        </div>
        {#if status?.stepper}
          <div class="stepper">
            {#each status.stepper.filter((s) => s.stage !== 'complete') as step (step.stage)}
              <div class="step {step.state}">
                <span class="step-dot">
                  {#if step.state === 'done'}
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                  {/if}
                </span>
                <span class="step-label">{step.label.replace('...', '')}</span>
              </div>
            {/each}
          </div>
        {/if}
      {:else if isComplete}
        <div class="status complete">
          <svg class="status-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
          <span>Complete</span>
        </div>
      {:else if isError}
        <div class="status error">
          <svg class="status-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          <span>{status?.error ?? 'Failed'}</span>
        </div>
      {/if}

      {#if exifRows.length > 0}
        <div class="section-title">EXIF</div>
        <div class="exif">
          {#each exifRows as row (row.label)}
            <div class="exif-row">
              <span class="exif-label">{row.label}</span>
              <span class="exif-value">{row.value}</span>
            </div>
          {/each}
        </div>
      {/if}

      {#if persons.length > 0}
        <div class="section-title">People ({persons.length})</div>
        <div class="faces">
          {#each persons as person (person.id)}
            <div class="face" title={getNodeName(person)}>
              <img
                src={resolvePersonThumbUrl(person)}
                alt={getNodeName(person)}
                class="face-img"
                onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
              />
              <span class="face-name">{getNodeName(person)}</span>
            </div>
          {/each}
        </div>
      {/if}

      {#if locations.length > 0}
        <div class="section-title">Locations ({locations.length})</div>
        <div class="chips">
          {#each locations as loc (loc.id)}
            <span class="chip location">{getNodeName(loc)}</span>
          {/each}
        </div>
      {/if}

      {#if events.length > 0}
        <div class="section-title">Events ({events.length})</div>
        <div class="chips">
          {#each events as ev (ev.id)}
            <span class="chip event">{getNodeName(ev)}</span>
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
  .node-overlay {
    position: absolute;
    top: 16px;
    right: 16px;
    width: 300px;
    max-height: calc(100% - 32px);
    overflow-y: auto;
    z-index: 100;
    display: flex;
    flex-direction: column;
    background: rgba(15, 23, 42, 0.94);
    border: 1.5px solid rgba(0, 212, 255, 0.3);
    border-radius: 10px;
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    color: #c8d6e5;
    font-size: 12px;
    line-height: 1.4;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5), 0 0 24px rgba(0, 212, 255, 0.12);
    animation: card-in 0.25s cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  @keyframes card-in {
    from { opacity: 0; transform: translateX(16px); }
    to { opacity: 1; transform: translateX(0); }
  }

  .overlay-close {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 26px;
    height: 26px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 212, 255, 0.1);
    border: 1px solid rgba(0, 212, 255, 0.3);
    border-radius: 50%;
    cursor: pointer;
    color: #00d4ff;
    transition: background 0.15s ease, border-color 0.15s ease;
    z-index: 2;
  }

  .overlay-close:hover {
    background: rgba(0, 212, 255, 0.22);
    border-color: rgba(0, 212, 255, 0.7);
  }

  .overlay-close svg {
    width: 14px;
    height: 14px;
  }

  .overlay-thumb {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(10, 14, 23, 0.9);
    border-bottom: 1px solid rgba(0, 212, 255, 0.18);
    overflow: hidden;
  }

  .overlay-thumb img {
    display: block;
    width: 100%;
    max-height: 200px;
    object-fit: contain;
  }

  .overlay-thumb-placeholder {
    width: 100%;
    height: 120px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: rgba(148, 163, 184, 0.5);
  }

  .overlay-thumb-placeholder svg {
    width: 40px;
    height: 40px;
  }

  .full-btn {
    margin: 8px;
    padding: 6px 10px;
    background: rgba(0, 212, 255, 0.12);
    border: 1px solid rgba(0, 212, 255, 0.4);
    border-radius: 6px;
    color: #00d4ff;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 11px;
    cursor: pointer;
    transition: background 0.15s ease, border-color 0.15s ease;
  }

  .full-btn:hover {
    background: rgba(0, 212, 255, 0.22);
    border-color: rgba(0, 212, 255, 0.7);
  }

  .delete-btn {
    margin: 0 8px 8px;
    padding: 6px 10px;
    background: rgba(248, 113, 113, 0.12);
    border: 1px solid rgba(248, 113, 113, 0.4);
    border-radius: 6px;
    color: #f87171;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 11px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    transition: background 0.15s ease, border-color 0.15s ease;
  }

  .delete-btn:hover:not(:disabled) {
    background: rgba(248, 113, 113, 0.22);
    border-color: rgba(248, 113, 113, 0.7);
  }

  .delete-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .overlay-body {
    padding: 10px 12px 12px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .overlay-filename {
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-weight: 700;
    font-size: 12px;
    color: #00d4ff;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .section-title {
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: rgba(0, 212, 255, 0.7);
    border-top: 1px solid rgba(0, 212, 255, 0.14);
    padding-top: 6px;
    margin-top: 2px;
  }

  .status {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    color: #94a3b8;
  }

  .status.complete { color: #4ade80; }
  .status.error { color: #f87171; }

  .status-icon {
    width: 13px;
    height: 13px;
    flex-shrink: 0;
  }

  .spinner {
    width: 13px;
    height: 13px;
    border: 2px solid rgba(0, 212, 255, 0.2);
    border-top-color: #00d4ff;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .stepper {
    display: flex;
    flex-direction: column;
    gap: 3px;
    border-top: 1px solid rgba(0, 212, 255, 0.12);
    padding-top: 5px;
  }

  .step {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 10px;
    color: #475569;
    transition: color 0.2s;
  }

  .step.done { color: #4ade80; }

  .step.current {
    color: #00d4ff;
  }

  .step.current .step-dot {
    border-color: #00d4ff;
    box-shadow: 0 0 6px rgba(0, 212, 255, 0.5);
  }

  .step.current .step-label {
    font-weight: 600;
  }

  .step-dot {
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

  .step.done .step-dot {
    background: #4ade80;
    border-color: #4ade80;
  }

  .step-dot svg {
    width: 7px;
    height: 7px;
    color: #0f172a;
  }

  .step-label {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .exif {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .exif-row {
    display: flex;
    justify-content: space-between;
    gap: 6px;
    align-items: baseline;
  }

  .exif-label {
    color: #94a3b8;
    font-size: 10px;
    flex-shrink: 0;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  }

  .exif-value {
    color: #c8d6e5;
    font-size: 11px;
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .faces {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .face {
    display: flex;
    align-items: center;
    gap: 4px;
    max-width: 100%;
  }

  .face-img {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    object-fit: cover;
    border: 1px solid rgba(0, 212, 255, 0.3);
    flex-shrink: 0;
  }

  .face-name {
    font-size: 10px;
    color: #c8d6e5;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .chip {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 10px;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    border: 1px solid;
  }

  .chip.location {
    color: #4ade80;
    border-color: rgba(74, 222, 128, 0.35);
    background: rgba(74, 222, 128, 0.08);
  }

  .chip.event {
    color: #fbbf24;
    border-color: rgba(251, 191, 36, 0.35);
    background: rgba(251, 191, 36, 0.08);
  }

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