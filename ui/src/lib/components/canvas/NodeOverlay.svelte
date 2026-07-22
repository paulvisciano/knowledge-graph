<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import { API, type KGNode } from '$lib/constants';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
  import type { CanvasNode } from './renderer/types';
  import { classifyKind } from './Layout';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  const KG_API_PROXY_BASE = '/api/kg';

  marked.use({
    renderer: {
      code({ text, lang }: { text: string; lang?: string }) {
        const language = lang || 'plaintext';
        return `<pre class="hud-code"><code class="language-${language}">${text}</code></pre>`;
      },
      paragraph({ text }: { text: string }) {
        return `<p class="hud-p">${text}</p>`;
      },
    },
  });

  function renderMarkdown(text: string): string {
    const raw = marked.parse(text, { async: false }) as string;
    return DOMPurify.sanitize(raw);
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
    return `${KG_API_PROXY_BASE}${API.kg.faceCrop(n.id)}`;
  }

  function resolvePersonThumbUrl(n: KGNode): string {
    return personFaceCropUrl(n) ?? personFallbackUrl(n);
  }

  function getInitials(name: string): string {
    return name
      .split(/[\s_]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase();
  }

  const ACCENT_COLORS = ['#00d4ff', '#a855f7', '#00ff88', '#ff8c00'];
  function hashColor(label: string): string {
    let hash = 0;
    for (let i = 0; i < label.length; i++) {
      hash = (hash * 31 + label.charCodeAt(i)) | 0;
    }
    return ACCENT_COLORS[Math.abs(hash) % ACCENT_COLORS.length];
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
  let deleting = $state(false);
  let personPhotoErrors = $state(new Set<string>());

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
  let others = $derived(
    neighborNodes.filter(
      (n) => !['person', 'location', 'event'].includes(classifyKind(n)),
    ),
  );

  let fileName = $derived(
    (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string) ??
      node?.id ??
      'Photo',
  );

  let descriptionContent = $derived(
    (kgNode?.properties?.description as string) ??
      (kgNode?.properties?.summary as string) ??
      null,
  );

  let locationText = $derived(
    locations.length > 0
      ? getNodeName(locations[0])
      : ((kgNode?.properties?.location as string) ?? (node?.properties?.location as string) ?? null),
  );
  let dateText = $derived(
    (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null,
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

  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events, a11y_interactive_supports_focus -->
  <div class="hud-panel" role="dialog" aria-label="Image details" tabindex="-1" onclick={(e) => e.stopPropagation()}>
    <span class="hud-corner hud-corner-tl" aria-hidden="true"></span>
    <span class="hud-corner hud-corner-tr" aria-hidden="true"></span>
    <span class="hud-corner hud-corner-bl" aria-hidden="true"></span>
    <span class="hud-corner hud-corner-br" aria-hidden="true"></span>
    <div class="hud-scan" aria-hidden="true"></div>

    <div class="hud-photo-panel">
      <span class="hud-corner hud-corner-tl" aria-hidden="true"></span>
      <span class="hud-corner hud-corner-tr" aria-hidden="true"></span>
      <span class="hud-corner hud-corner-bl" aria-hidden="true"></span>
      <span class="hud-corner hud-corner-br" aria-hidden="true"></span>

      <div class="hud-photo-stage">
        {#if fullUrl ?? imageUrl}
          <img src={fullUrl ?? imageUrl} alt={fileName} class="hud-photo-img" />
        {:else}
          <div class="hud-photo-placeholder">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <circle cx="8.5" cy="8.5" r="1.5"/>
              <path d="m21 15-5-5L5 21"/>
            </svg>
          </div>
        {/if}
        <div class="hud-photo-overlay" aria-hidden="true"></div>

        <div class="hud-photo-tags">
          {#if locationText}
            <span class="hud-tag hud-tag-loc">
              <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>
              {locationText}
            </span>
          {/if}
          {#if dateText}
            <span class="hud-tag hud-tag-date">
              <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>
              {dateText}
            </span>
          {/if}
        </div>

        {#if fullUrl}
          <button class="hud-photo-fullscreen" onclick={() => openFullscreen(fullUrl)} aria-label="View full screen">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>
          </button>
        {/if}

        {#if isProcessing || isComplete || isError}
          <div class="hud-photo-status">
            {#if isProcessing}
              <span class="hud-spinner" aria-hidden="true"></span>
              <span>{status?.stageLabel ?? 'Processing...'}</span>
            {:else if isComplete}
              <svg class="hud-status-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
              <span>Complete</span>
            {:else if isError}
              <svg class="hud-status-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              <span>{status?.error ?? 'Failed'}</span>
            {/if}
          </div>
          {#if isProcessing && status?.stepper}
            <div class="hud-stepper">
              {#each status.stepper.filter((s) => s.stage !== 'complete') as step (step.stage)}
                <div class="hud-step {step.state}">
                  <span class="hud-step-dot">
                    {#if step.state === 'done'}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                    {/if}
                  </span>
                  <span class="hud-step-label">{step.label.replace('...', '')}</span>
                </div>
              {/each}
            </div>
          {/if}
        {/if}
      </div>
    </div>

    <div class="hud-info-panel">
      <span class="hud-corner hud-corner-tl" aria-hidden="true"></span>
      <span class="hud-corner hud-corner-tr" aria-hidden="true"></span>
      <span class="hud-corner hud-corner-bl" aria-hidden="true"></span>
      <span class="hud-corner hud-corner-br" aria-hidden="true"></span>

      <header class="hud-header">
        <div class="hud-title">
          <span class="hud-pip" aria-hidden="true"></span>
          <span class="hud-title-text" title={fileName}>{fileName}</span>
        </div>
        <button class="hud-close" onclick={handleClose} aria-label="Close details (Esc)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </header>

      <div class="hud-info-body">
        <section class="hud-section">
          <h3 class="hud-h3">[ DESCRIPTION ]</h3>
          {#if descriptionContent}
            <div class="hud-summary prose-cyber">{@html renderMarkdown(descriptionContent)}</div>
          {:else}
            <div class="hud-summary-empty">No description available.</div>
          {/if}
        </section>

        {#if persons.length > 0}
          <section class="hud-section">
            <h3 class="hud-h3">[ PEOPLE <span class="hud-count">{persons.length}</span> ]</h3>
            <div class="hud-faces">
              {#each persons as person (person.id)}
                <div class="hud-face" title={getNodeName(person)}>
                  {#if !personPhotoErrors.has(person.id)}
                    <img
                      src={resolvePersonThumbUrl(person)}
                      alt={getNodeName(person)}
                      class="hud-face-img"
                      onerror={() => {
                        const updated = new Set(personPhotoErrors);
                        updated.add(person.id);
                        personPhotoErrors = updated;
                      }}
                    />
                  {:else}
                    <div
                      class="hud-face-fallback"
                      style="background: {hashColor(person.labels?.[0] ?? 'P')}20; color: {hashColor(person.labels?.[0] ?? 'P')};"
                    >
                      {getInitials(getNodeName(person))}
                    </div>
                  {/if}
                  <span class="hud-face-name">{getNodeName(person)}</span>
                </div>
              {/each}
            </div>
          </section>
        {/if}

        {#if locations.length > 0}
          <section class="hud-section">
            <h3 class="hud-h3">[ LOCATIONS <span class="hud-count">{locations.length}</span> ]</h3>
            <div class="hud-chips">
              {#each locations as loc (loc.id)}
                <span class="hud-chip hud-chip-loc">
                  <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>
                  {getNodeName(loc)}
                </span>
              {/each}
            </div>
          </section>
        {/if}

        {#if events.length > 0}
          <section class="hud-section">
            <h3 class="hud-h3">[ EVENTS <span class="hud-count">{events.length}</span> ]</h3>
            <div class="hud-chips">
              {#each events as ev (ev.id)}
                <span class="hud-chip hud-chip-event">{getNodeName(ev)}</span>
              {/each}
            </div>
          </section>
        {/if}

        {#if others.length > 0}
          <section class="hud-section">
            <h3 class="hud-h3">[ ENTITIES <span class="hud-count">{others.length}</span> ]</h3>
            <div class="hud-chips">
              {#each others as o (o.id)}
                <span class="hud-chip hud-chip-other" style="color: {hashColor(o.labels?.[0] ?? 'X')}; border-color: {hashColor(o.labels?.[0] ?? 'X')}55; background: {hashColor(o.labels?.[0] ?? 'X')}10;">
                  {getNodeName(o)}
                </span>
              {/each}
            </div>
          </section>
        {/if}

        {#if exifRows.length > 0}
          <section class="hud-section">
            <h3 class="hud-h3">[ EXIF DATA ]</h3>
            <div class="hud-exif">
              {#each exifRows as row (row.label)}
                <div class="hud-exif-row">
                  <span class="hud-exif-label">{row.label}</span>
                  <span class="hud-exif-value">{row.value}</span>
                </div>
              {/each}
            </div>
          </section>
        {/if}
      </div>

      <footer class="hud-actions">
        {#if fullUrl}
          <button class="hud-btn hud-btn-cyan" onclick={() => openFullscreen(fullUrl)}>
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>
            View Full
          </button>
        {/if}
        <button class="hud-btn hud-btn-red" onclick={handleDelete} disabled={deleting}>
          {#if deleting}
            <span class="hud-spinner hud-spinner-sm" aria-hidden="true"></span>
            Deleting...
          {:else}
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            Delete
          {/if}
        </button>
      </footer>
    </div>
  </div>
{/if}

{#if fullscreenUrl}
  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions, a11y_img_redundant_alt -->
  <div class="hud-fullscreen" onclick={closeFullscreen} role="presentation">
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
    <img src={fullscreenUrl} alt="Full screen image" class="hud-fullscreen-img" onclick={(e) => e.stopPropagation()} />
    <button class="hud-fullscreen-close" onclick={closeFullscreen} aria-label="Close">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
{/if}

<style>
  .hud-panel {
    position: absolute;
    top: 16px;
    right: 16px;
    width: min(960px, 72vw);
    max-width: calc(100vw - 32px);
    height: calc(100% - 104px);
    z-index: 100;
    display: flex;
    flex-direction: row;
    gap: 0;
    overflow: hidden;
    background: linear-gradient(180deg, rgba(13, 20, 35, 0.97) 0%, rgba(10, 14, 23, 0.98) 100%);
    border: 1px solid rgba(0, 212, 255, 0.35);
    border-radius: 4px;
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    color: #c8d6e5;
    font-size: 12px;
    line-height: 1.45;
    box-shadow:
      0 12px 40px rgba(0, 0, 0, 0.6),
      0 0 28px rgba(0, 212, 255, 0.18),
      inset 0 0 0 1px rgba(0, 212, 255, 0.06);
    animation: hud-in 0.28s cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  @keyframes hud-in {
    from { opacity: 0; transform: translateX(18px) scale(0.99); }
    to { opacity: 1; transform: translateX(0) scale(1); }
  }

  .hud-corner {
    position: absolute;
    width: 14px;
    height: 14px;
    border-color: #00d4ff;
    border-style: solid;
    pointer-events: none;
    z-index: 3;
    opacity: 0.85;
  }
  .hud-corner-tl { top: -1px; left: -1px; border-width: 2px 0 0 2px; }
  .hud-corner-tr { top: -1px; right: -1px; border-width: 2px 2px 0 0; }
  .hud-corner-bl { bottom: -1px; left: -1px; border-width: 0 0 2px 2px; }
  .hud-corner-br { bottom: -1px; right: -1px; border-width: 0 2px 2px 0; }

  .hud-scan {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 1;
    background: linear-gradient(to bottom, transparent 0%, rgba(0, 212, 255, 0.05) 50%, transparent 100%);
    background-size: 100% 8px;
    opacity: 0.3;
    mix-blend-mode: screen;
    animation: hud-scan-move 6s linear infinite;
  }
  @keyframes hud-scan-move {
    0% { background-position: 0 0; }
    100% { background-position: 0 100%; }
  }

  /* PHOTO PANEL — hero, ~60% width */
  .hud-photo-panel {
    position: relative;
    width: 60%;
    background: #0a0e17;
    overflow: hidden;
    flex-shrink: 0;
    box-shadow: inset 0 0 60px rgba(0, 212, 255, 0.06);
  }
  .hud-photo-panel .hud-corner {
    width: 10px;
    height: 10px;
  }

  .hud-photo-stage {
    position: relative;
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 0;
    padding: 14px;
  }
  .hud-photo-img {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    display: block;
  }
  .hud-photo-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    color: rgba(0, 212, 255, 0.3);
  }
  .hud-photo-placeholder svg { width: 80px; height: 80px; }

  .hud-photo-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: linear-gradient(to top, rgba(10, 14, 23, 0.8) 0%, transparent 30%);
  }

  .hud-photo-tags {
    position: absolute;
    bottom: 12px;
    left: 12px;
    right: 12px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    z-index: 2;
  }

  .hud-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 10px;
    border-radius: 2px;
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    border: 1px solid;
  }
  .hud-tag-loc {
    color: #4ade80;
    background: rgba(74, 222, 128, 0.12);
    border-color: rgba(74, 222, 128, 0.4);
  }
  .hud-tag-date {
    color: #00d4ff;
    background: rgba(0, 212, 255, 0.12);
    border-color: rgba(0, 212, 255, 0.4);
  }

  .hud-photo-fullscreen {
    position: absolute;
    top: 12px;
    right: 12px;
    width: 32px;
    height: 32px;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 212, 255, 0.1);
    border: 1px solid rgba(0, 212, 255, 0.4);
    border-radius: 50%;
    color: #00d4ff;
    cursor: pointer;
    backdrop-filter: blur(4px);
    transition: all 0.2s ease;
  }
  .hud-photo-fullscreen:hover {
    background: rgba(0, 212, 255, 0.25);
    border-color: #00d4ff;
    box-shadow: 0 0 12px rgba(0, 212, 255, 0.5);
  }

  .hud-photo-status {
    position: absolute;
    top: 12px;
    left: 12px;
    z-index: 2;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    background: rgba(10, 14, 23, 0.7);
    border: 1px solid rgba(0, 212, 255, 0.3);
    border-radius: 12px;
    backdrop-filter: blur(8px);
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 11px;
    color: #94a3b8;
  }
  .hud-photo-status .hud-spinner { width: 12px; height: 12px; }
  .hud-photo-status .hud-status-icon { width: 14px; height: 14px; }

  /* INFO PANEL — ~40% width */
  .hud-info-panel {
    position: relative;
    width: 40%;
    display: flex;
    flex-direction: column;
    border-left: 1px solid rgba(0, 212, 255, 0.3);
    background: rgba(13, 20, 35, 0.4);
  }
  .hud-info-panel .hud-corner {
    width: 10px;
    height: 10px;
  }

  .hud-header {
    position: relative;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px;
    border-bottom: 1px solid rgba(0, 212, 255, 0.2);
    background: rgba(0, 212, 255, 0.05);
  }
  .hud-title {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    flex: 1;
  }
  .hud-pip {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #00d4ff;
    box-shadow: 0 0 6px rgba(0, 212, 255, 0.8);
    animation: hud-pip 2s ease-in-out infinite;
  }
  @keyframes hud-pip {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .hud-title-text {
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-weight: 700;
    font-size: 13px;
    color: #00d4ff;
    text-shadow: 0 0 8px rgba(0, 212, 255, 0.4);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .hud-close {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 212, 255, 0.1);
    border: 1px solid rgba(0, 212, 255, 0.4);
    border-radius: 50%;
    cursor: pointer;
    color: #00d4ff;
    transition: all 0.15s;
  }
  .hud-close:hover {
    background: rgba(0, 212, 255, 0.2);
    border-color: #00d4ff;
  }
  .hud-close svg { width: 12px; height: 12px; }

  .hud-info-body {
    position: relative;
    z-index: 2;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    scrollbar-width: thin;
    scrollbar-color: rgba(0, 212, 255, 0.2) transparent;
  }
  .hud-info-body::-webkit-scrollbar { width: 4px; }
  .hud-info-body::-webkit-scrollbar-thumb {
    background: rgba(0, 212, 255, 0.2);
    border-radius: 2px;
  }

  .hud-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .hud-h3 {
    margin: 0;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: rgba(0, 212, 255, 0.8);
    text-shadow: 0 0 8px rgba(0, 212, 255, 0.3);
    padding-bottom: 4px;
    border-bottom: 1px solid rgba(0, 212, 255, 0.15);
  }
  .hud-count {
    color: #00d4ff;
    font-weight: 700;
  }

  /* Faces */
  .hud-faces {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .hud-face {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 8px 4px 4px;
    background: rgba(168, 85, 247, 0.06);
    border: 1px solid rgba(168, 85, 247, 0.25);
    border-radius: 2px;
    transition: all 0.15s;
  }
  .hud-face:hover {
    border-color: rgba(168, 85, 247, 0.6);
    background: rgba(168, 85, 247, 0.12);
    box-shadow: 0 0 8px rgba(168, 85, 247, 0.2);
  }
  .hud-face-img {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    object-fit: cover;
    border: 1px solid rgba(0, 212, 255, 0.5);
    box-shadow: 0 0 4px rgba(0, 212, 255, 0.3);
    flex-shrink: 0;
  }
  .hud-face-fallback {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 700;
    border: 1px solid rgba(0, 212, 255, 0.3);
    flex-shrink: 0;
  }
  .hud-face-name {
    font-size: 11px;
    color: #e2e8f0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Chips */
  .hud-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .hud-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 8px;
    border-radius: 2px;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 10px;
    border: 1px solid;
    transition: all 0.15s;
    cursor: pointer;
  }
  .hud-chip:hover {
    filter: brightness(1.2);
    box-shadow: 0 0 6px currentColor;
  }
  .hud-chip-loc {
    color: #4ade80;
    border-color: rgba(74, 222, 128, 0.4);
    background: rgba(74, 222, 128, 0.08);
  }
  .hud-chip-event {
    color: #fbbf24;
    border-color: rgba(251, 191, 36, 0.4);
    background: rgba(251, 191, 36, 0.08);
  }
  .hud-chip-other {
    border-radius: 2px;
  }

  /* Summary / Description */
  .hud-summary {
    font-size: 12px;
    line-height: 1.6;
    color: #c8d6e5;
    padding: 8px 12px;
    background: rgba(0, 0, 0, 0.3);
    border: 1px solid rgba(0, 212, 255, 0.1);
    border-radius: 2px;
    max-height: 300px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: rgba(0, 212, 255, 0.2) transparent;
  }
  .hud-summary::-webkit-scrollbar { width: 4px; }
  .hud-summary::-webkit-scrollbar-thumb {
    background: rgba(0, 212, 255, 0.2);
    border-radius: 2px;
  }
  .hud-summary-empty {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    font-size: 11px;
    color: #64748b;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  }

  /* Markdown overrides */
  .hud-summary :global(.hud-p) { margin: 0.5em 0; }
  .hud-summary :global(.hud-code) {
    margin: 0.5em 0;
    overflow-x: auto;
    padding: 6px;
    background: rgba(0, 0, 0, 0.5);
    border: 1px solid rgba(0, 212, 255, 0.2);
    border-radius: 2px;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 11px;
    color: #c8d6e5;
  }
  .hud-summary :global(strong) { color: #e2e8f0; font-weight: 600; }
  .hud-summary :global(em) { color: #94a3b8; }
  .hud-summary :global(ul),
  .hud-summary :global(ol) { padding-left: 1.4em; margin: 0.4em 0; }
  .hud-summary :global(li) { margin: 0.2em 0; }
  .hud-summary :global(a) { color: #00d4ff; text-decoration: underline; }

  /* Processing status */
  .hud-status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    color: #94a3b8;
  }
  .hud-status-processing { color: #00d4ff; }
  .hud-status-complete { color: #4ade80; }
  .hud-status-error { color: #f87171; }
  .hud-status-icon { width: 14px; height: 14px; flex-shrink: 0; }

  .hud-spinner {
    width: 14px;
    height: 14px;
    border: 2px solid rgba(0, 212, 255, 0.2);
    border-top-color: #00d4ff;
    border-radius: 50%;
    animation: hud-spin 0.7s linear infinite;
    flex-shrink: 0;
  }
  .hud-spinner-sm { width: 11px; height: 11px; border-width: 2px; }
  @keyframes hud-spin { to { transform: rotate(360deg); } }

  .hud-stepper {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid rgba(0, 212, 255, 0.12);
  }
  .hud-step {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    color: #475569;
    transition: color 0.2s;
  }
  .hud-step.done { color: #4ade80; }
  .hud-step.current { color: #00d4ff; }
  .hud-step.current .hud-step-dot {
    border-color: #00d4ff;
    box-shadow: 0 0 6px rgba(0, 212, 255, 0.5);
  }
  .hud-step.current .hud-step-label { font-weight: 600; }
  .hud-step-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 1.5px solid currentColor;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .hud-step.done .hud-step-dot {
    background: #4ade80;
    border-color: #4ade80;
  }
  .hud-step-dot svg { width: 8px; height: 8px; color: #0f172a; }
  .hud-step-label {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* EXIF grid */
  .hud-exif {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px 12px;
  }
  .hud-exif-row {
    display: flex;
    justify-content: space-between;
    gap: 6px;
    align-items: baseline;
    padding: 4px 6px;
    background: rgba(0, 212, 255, 0.03);
    border: 1px solid rgba(0, 212, 255, 0.1);
    border-radius: 2px;
  }
  .hud-exif-label {
    color: #94a3b8;
    font-size: 10px;
    flex-shrink: 0;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .hud-exif-value {
    color: #c8d6e5;
    font-size: 11px;
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  }

  /* Action bar */
  .hud-actions {
    position: relative;
    z-index: 2;
    display: flex;
    gap: 8px;
    padding: 12px;
    border-top: 1px solid rgba(0, 212, 255, 0.22);
    background: rgba(0, 212, 255, 0.05);
  }
  .hud-btn {
    flex: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 8px 12px;
    border-radius: 2px;
    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid;
    transition: all 0.15s;
  }
  .hud-btn-cyan {
    color: #00d4ff;
    border-color: rgba(0, 212, 255, 0.4);
    background: rgba(0, 212, 255, 0.12);
  }
  .hud-btn-cyan:hover {
    background: rgba(0, 212, 255, 0.22);
    border-color: #00d4ff;
    box-shadow: 0 0 10px rgba(0, 212, 255, 0.3);
  }
  .hud-btn-red {
    color: #f87171;
    border-color: rgba(248, 113, 113, 0.4);
    background: rgba(248, 113, 113, 0.12);
  }
  .hud-btn-red:hover:not(:disabled) {
    background: rgba(248, 113, 113, 0.22);
    border-color: #f87171;
    box-shadow: 0 0 10px rgba(248, 113, 113, 0.3);
  }
  .hud-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  /* Fullscreen image viewer */
  .hud-fullscreen {
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: rgba(0, 0, 0, 0.92);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
    animation: hud-fs-in 0.2s ease-out;
  }
  @keyframes hud-fs-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  .hud-fullscreen-img {
    max-width: 95vw;
    max-height: 95vh;
    object-fit: contain;
    border-radius: 4px;
    cursor: default;
  }
  .hud-fullscreen-close {
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
  .hud-fullscreen-close:hover {
    background: rgba(255, 255, 255, 0.2);
  }

  @media (max-width: 768px) {
    .hud-panel {
      flex-direction: column;
      width: min(580px, 92vw);
      right: 4%;
      left: 4%;
      margin: 0 auto;
    }
    .hud-photo-panel {
      width: 100%;
      height: 42vh;
    }
    .hud-info-panel {
      width: 100%;
      border-left: none;
      border-top: 1px solid rgba(0, 212, 255, 0.3);
    }
    .hud-exif {
      grid-template-columns: 1fr;
    }
  }
</style>