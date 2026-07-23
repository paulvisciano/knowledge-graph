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
        return `<pre class="overlay-code"><code class="language-${language}">${text}</code></pre>`;
      },
      paragraph({ text }: { text: string }) {
        return `<p class="overlay-p">${text}</p>`;
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

  function isPersonNamed(n: KGNode): boolean {
    const name = getNodeName(n);
    return name !== n.id && name.length > 0 && !/^[a-f0-9-]{8,}$/i.test(name);
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

  const EXIF_CAMERA_KEYS = ['camera', 'date_taken_friendly', 'lens', 'flash'];
  const EXIF_EXPOSURE_KEYS = ['f_number', 'iso', 'focal_length', 'exposure_time', 'white_balance', 'orientation'];
  const EXIF_IMAGE_KEYS = ['image_width', 'image_height'];

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

  function filterExifGroup(
    rows: { label: string; value: string }[],
    keys: string[],
  ): { label: string; value: string }[] {
    const labels = keys.map((k) => EXIF_DISPLAY_KEYS[k]).filter(Boolean);
    return rows.filter((r) => labels.includes(r.label));
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

  const docContentCache = new Map<string, string>();
  let fetchedDocNodeId = $state<string | null>(null);
  let fetchedDocContent = $state<string | null>(null);
  let docLoading = $state(false);
  let docError = $state<string | null>(null);

  async function fetchDocContentForNode(nodeId: string, fileSource: string) {
    if (docContentCache.has(nodeId)) {
      fetchedDocContent = docContentCache.get(nodeId) ?? null;
      fetchedDocNodeId = nodeId;
      return;
    }
    docLoading = true;
    docError = null;
    try {
      const docId = await lightragClient.resolveDocumentId(fileSource);
      if (!docId) {
        docContentCache.set(nodeId, '');
        fetchedDocContent = null;
        fetchedDocNodeId = nodeId;
        return;
      }
      const data = await lightragClient.getDocumentFullContent(docId);
      const content = data?.content ?? '';
      docContentCache.set(nodeId, content);
      fetchedDocContent = content || null;
      fetchedDocNodeId = nodeId;
    } catch (err) {
      console.error('[NodeOverlay] Failed to fetch document content:', err);
      docError = 'Failed to load description.';
      docContentCache.set(nodeId, '');
      fetchedDocContent = null;
      fetchedDocNodeId = nodeId;
    } finally {
      docLoading = false;
    }
  }

  let locationText = $derived(
    locations.length > 0
      ? getNodeName(locations[0])
      : ((kgNode?.properties?.location as string) ?? (node?.properties?.location as string) ?? null),
  );
  let dateText = $derived.by(() => {
    const raw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
    if (!raw) return null;

    // Already human-friendly: "2024-06-05 at 14:30"
    const friendlyMatch = raw.match(/^(\d{4}-\d{2}-\d{2}) at (\d{2}:\d{2})/);
    if (friendlyMatch) {
      return `${friendlyMatch[1]} · ${friendlyMatch[2]}`;
    }

    // Epoch seconds (from DB DOUBLE PRECISION) — new Date() expects ms
    const asNum = Number(raw);
    if (!isNaN(asNum) && asNum > 0) {
      const ms = asNum > 1e12 ? asNum : asNum * 1000;
      const parsed = new Date(ms);
      if (!isNaN(parsed.getTime())) {
        return parsed.toLocaleString(undefined, {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        });
      }
    }

    // ISO string or other parseable format
    const parsed = new Date(raw);
    if (!isNaN(parsed.getTime())) {
      return parsed.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }

    return raw;
  });

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

  $effect(() => {
    const id = node?.id;
    if (!id) return;
    const fileSource =
      (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string);
    if (fileSource) {
      fetchDocContentForNode(id, fileSource);
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
        // Best-effort cleanup
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

  // Parallax effect
  let parallaxEnabled = $state(true);
  function handleParallax(e: MouseEvent) {
    if (!parallaxEnabled) return;
    const inner = document.querySelector('[data-od-id="scene-inner"]') as HTMLElement;
    if (!inner) return;
    const cx = window.innerWidth / 2;
    const cy = window.innerHeight / 2;
    const tx = ((e.clientX - cx) / cx) * 6;
    const ty = ((e.clientY - cy) / cy) * -4;
    inner.style.transform = `rotateX(${ty.toFixed(2)}deg) rotateY(${tx.toFixed(2)}deg)`;
  }

  $effect(() => {
    const isMobile = window.matchMedia('(max-width: 768px)').matches;
    parallaxEnabled = !isMobile;
  });

  $effect(() => {
    if (!node) return;
    function onKeydown(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      if (fullscreenUrl) {
        fullscreenUrl = null;
      } else {
        handleClose();
      }
    }
    window.addEventListener('keydown', onKeydown);
    return () => window.removeEventListener('keydown', onKeydown);
  });
</script>

{#if node}
  {@const status = imageProcessingStore.statuses[node.id]}
  {@const imageUrl = node.imageUrl ?? graphStore.photoImages[node.id]}
  {@const fullUrl = node.fullUrl ?? (imageUrl ? imageUrl.replace(/([?&]w=)\d+\b/, '$1full') : undefined)}
  {@const exifRows = fetchedExifNodeId === node.id ? fetchedExifRows : []}
  {@const exifCamera = filterExifGroup(exifRows, EXIF_CAMERA_KEYS)}
  {@const exifExposure = filterExifGroup(exifRows, EXIF_EXPOSURE_KEYS)}
  {@const exifImage = filterExifGroup(exifRows, EXIF_IMAGE_KEYS)}
  {@const isProcessing = status && status.stage !== 'complete' && status.stage !== 'error'}
  {@const isComplete = status?.stage === 'complete'}
  {@const isError = status?.stage === 'error'}

  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
  <div class="spatial-scene" data-od-id="spatial-scene" onclick={handleClose}>
    <div class="scene-inner" data-od-id="scene-inner" onmousemove={handleParallax} onclick={(e) => e.stopPropagation()}>

      <!-- Top bar -->
      <div class="topbar" data-od-id="topbar">
        <div class="topbar-left">
          {#if isProcessing || isComplete || isError}
            <div class="status-pill {isProcessing ? 'is-processing' : isComplete ? 'is-complete' : isError ? 'is-error' : ''}" data-od-id="status-pill">
              {#if isProcessing}
                <span class="status-dot is-processing" aria-hidden="true"></span>
                <span>{status?.stageLabel ?? 'Processing...'}</span>
              {:else if isComplete}
                <span class="status-dot" aria-hidden="true"></span>
                <span>Processed</span>
              {:else if isError}
                <span class="status-dot is-error" aria-hidden="true"></span>
                <span>{status?.error ?? 'Failed'}</span>
              {/if}
            </div>
          {/if}
          <span class="filename" data-od-id="filename" title={fileName}>{fileName}</span>
        </div>
        <button class="close-btn" data-od-id="close-btn" aria-label="Close details (Esc)" onclick={handleClose}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>

      <!-- Image — central hero -->
      <div class="image-stage" data-od-id="image-stage">
        <div class="image-frame" data-od-id="image-frame">
          {#if fullUrl ?? imageUrl}
            <!-- svelte-ignore a11y_no_noninteractive_element_interactions, a11y_click_events_have_key_events -->
            <img
              src={fullUrl ?? imageUrl}
              alt={fileName}
              data-od-id="photo-img"
              role="button"
              tabindex="0"
              onclick={() => openFullscreen(fullUrl ?? imageUrl!)}
              onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFullscreen(fullUrl ?? imageUrl!); } }}
            />
          {:else}
            <div class="image-placeholder" data-od-id="image-placeholder">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
            </div>
          {/if}

          <div class="image-tags" data-od-id="image-tags">
            {#if locationText}
              <span class="tag tag-loc">
                <svg viewBox="0 0 24 24" width="9" height="9" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>
                {locationText}
              </span>
            {/if}
            {#if dateText}
              <span class="tag tag-date">
                <svg viewBox="0 0 24 24" width="9" height="9" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>
                {dateText}
              </span>
            {/if}
          </div>

          {#if fullUrl ?? imageUrl}
            <div class="image-expand" data-od-id="image-expand" role="button" tabindex="-1" onclick={() => openFullscreen(fullUrl ?? imageUrl!)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>
            </div>
          {/if}
        </div>
      </div>

      <!-- Description — secondary hero -->
      <section class="description-panel" data-od-id="description-panel">
        <div class="description-label">Description</div>
        {#if docLoading}
          <div class="description-loading" data-od-id="description-loading">
            <span class="spinner" aria-hidden="true"></span>
            <span>Loading description…</span>
          </div>
        {:else if docError}
          <div class="description-error" data-od-id="description-error">{docError}</div>
        {:else if fetchedDocNodeId === node?.id && fetchedDocContent}
          <div class="description-text prose-spatial" data-od-id="description-content">{@html renderMarkdown(fetchedDocContent)}</div>
        {:else if descriptionContent}
          <div class="description-text prose-spatial" data-od-id="description-fallback">{@html renderMarkdown(descriptionContent)}</div>
        {:else}
          <div class="description-empty" data-od-id="description-empty">No description available.</div>
        {/if}
      </section>

      <!-- Data panels — floating, layered -->
      <div class="data-row" data-od-id="data-row">

        {#if persons.length > 0}
          <div class="data-panel" data-od-id="panel-people">
            <div class="panel-label">People <span class="count">{persons.length}</span></div>
            <div class="people-row" data-od-id="people-row">
              {#each persons as person (person.id)}
                <div class="person" title={getNodeName(person)} data-od-id="person-{person.id}">
                  {#if !personPhotoErrors.has(person.id) && isPersonNamed(person)}
                    <img
                      src={resolvePersonThumbUrl(person)}
                      alt={getNodeName(person)}
                      class="person-thumb"
                      data-od-id="thumb-{person.id}"
                      onerror={() => {
                        const updated = new Set(personPhotoErrors);
                        updated.add(person.id);
                        personPhotoErrors = updated;
                      }}
                    />
                  {:else if isPersonNamed(person)}
                    <div class="person-initial" style="background: oklch(28% 0.06 200 / 60%); color: oklch(80% 0.03 200);">
                      {getInitials(getNodeName(person))}
                    </div>
                  {:else}
                    <div class="person-silhouette" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/></svg>
                    </div>
                  {/if}
                  {#if isPersonNamed(person)}
                    <span class="person-name">{getNodeName(person)}</span>
                  {:else}
                    <span class="person-unnamed">Unknown</span>
                  {/if}
                </div>
              {/each}
            </div>
          </div>
        {/if}

        {#if locations.length > 0}
          <div class="data-panel" data-od-id="panel-locations">
            <div class="panel-label">Locations <span class="count">{locations.length}</span></div>
            <div class="chip-row" data-od-id="locations-row">
              {#each locations as loc (loc.id)}
                <span class="chip chip-loc">{getNodeName(loc)}</span>
              {/each}
            </div>
          </div>
        {/if}

        {#if events.length > 0}
          <div class="data-panel" data-od-id="panel-events">
            <div class="panel-label">Events <span class="count">{events.length}</span></div>
            <div class="chip-row" data-od-id="events-row">
              {#each events as ev (ev.id)}
                <span class="chip chip-event">{getNodeName(ev)}</span>
              {/each}
            </div>
          </div>
        {/if}

        {#if others.length > 0}
          <div class="data-panel" data-od-id="panel-entities">
            <div class="panel-label">Entities <span class="count">{others.length}</span></div>
            <div class="chip-row" data-od-id="entities-row">
              {#each others as o (o.id)}
                <span class="chip chip-entity">{getNodeName(o)}</span>
              {/each}
            </div>
          </div>
        {/if}

        {#if exifRows.length > 0}
          <div class="data-panel exif-panel" data-od-id="panel-exif">
            <div class="panel-label">Capture Data</div>
            {#if exifCamera.length > 0}
              <div class="exif-group" data-od-id="exif-camera">
                <div class="exif-group-label">Camera</div>
                <div class="exif-grid">
                  {#each exifCamera as row (row.label)}
                    <div class="exif-row">
                      <span class="exif-label">{row.label}</span>
                      <span class="exif-value">{row.value}</span>
                    </div>
                  {/each}
                </div>
              </div>
            {/if}
            {#if exifExposure.length > 0}
              <div class="exif-group" data-od-id="exif-exposure">
                <div class="exif-group-label">Exposure</div>
                <div class="exif-grid">
                  {#each exifExposure as row (row.label)}
                    <div class="exif-row">
                      <span class="exif-label">{row.label}</span>
                      <span class="exif-value">{row.value}</span>
                    </div>
                  {/each}
                </div>
              </div>
            {/if}
            {#if exifImage.length > 0}
              <div class="exif-group" data-od-id="exif-image">
                <div class="exif-group-label">Image</div>
                <div class="exif-grid">
                  {#each exifImage as row (row.label)}
                    <div class="exif-row">
                      <span class="exif-label">{row.label}</span>
                      <span class="exif-value">{row.value}</span>
                    </div>
                  {/each}
                </div>
              </div>
            {/if}
          </div>
        {/if}
      </div>

      {#if isProcessing && status?.stepper}
        <div class="stepper" data-od-id="stepper">
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

      <!-- Actions -->
      <div class="action-area" data-od-id="action-area">
        <button class="btn-delete" data-od-id="btn-delete" onclick={handleDelete} disabled={deleting}>
          {#if deleting}
            <span class="spinner spinner-sm" aria-hidden="true"></span>
            Deleting...
          {:else}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            Delete
          {/if}
        </button>
      </div>

    </div>
  </div>
{/if}

{#if fullscreenUrl}
  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
  <div class="fullscreen" data-od-id="fullscreen-viewer" onclick={closeFullscreen} role="presentation">
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
    <img src={fullscreenUrl} alt="Full screen image" class="fullscreen-img" onclick={(e) => e.stopPropagation()} />
    <button class="fullscreen-close" data-od-id="fullscreen-close" aria-label="Close" onclick={closeFullscreen}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
{/if}

<style>
  :root {
    --overlay-bg:          oklch(6% 0.02 260);
    --overlay-fg:          oklch(90% 0.005 250);
    --overlay-muted:       oklch(62% 0.02 255);
    --overlay-faint:       oklch(48% 0.02 255);
    --overlay-accent:      oklch(82% 0.14 210);
    --overlay-accent-dim:  oklch(82% 0.14 210 / 18%);
    --overlay-success:     oklch(72% 0.15 150);
    --overlay-danger:      oklch(62% 0.20 18);
    --overlay-glass:       oklch(16% 0.015 255 / 45%);
    --overlay-glass-light: oklch(20% 0.015 255 / 30%);
    --overlay-hairline:    oklch(50% 0.03 255 / 8%);
  }

  .spatial-scene {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    padding: 3vh 2vw 2vh;
    perspective: 1200px;
    perspective-origin: 50% 40%;
    overflow-y: auto;
    overflow-x: hidden;
    background:
      radial-gradient(ellipse 90% 70% at 50% 35%, oklch(14% 0.06 270 / 40%), transparent),
      radial-gradient(ellipse 60% 50% at 30% 80%, oklch(10% 0.04 210 / 30%), transparent),
      radial-gradient(ellipse 50% 40% at 80% 20%, oklch(8% 0.03 180 / 20%), transparent),
      var(--overlay-bg);
    background-attachment: fixed;
    color: var(--overlay-fg);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }

  .scene-inner {
    position: relative;
    width: 100%;
    max-width: 920px;
    transform-style: preserve-3d;
    will-change: transform;
  }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    margin-bottom: 2vh;
    transform: translateZ(20px);
  }

  .topbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    flex: 1;
  }

  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    background: var(--overlay-glass);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border-radius: 100px;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--overlay-muted);
    flex-shrink: 0;
  }
  .status-pill.is-complete { color: var(--overlay-success); }
  .status-pill.is-processing { color: var(--overlay-accent); }
  .status-pill.is-error { color: var(--overlay-danger); }

  .status-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: currentColor;
  }
  .status-dot.is-processing { animation: status-pulse 1.6s ease-in-out infinite; }
  @keyframes status-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

  .filename {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    color: var(--overlay-muted);
    letter-spacing: 0.06em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .close-btn {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: oklch(20% 0.02 255 / 70%);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border-radius: 50%;
    cursor: pointer;
    color: var(--overlay-fg);
    border: 2px solid oklch(60% 0.03 255 / 55%);
    box-shadow: 0 0 0 1px oklch(0% 0 0 / 20%), 0 4px 12px oklch(0% 0 0 / 25%);
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .close-btn:hover {
    color: var(--overlay-danger);
    background: oklch(62% 0.20 18 / 18%);
    border-color: oklch(62% 0.20 18 / 70%);
    box-shadow: 0 0 0 1px oklch(0% 0 0 / 20%), 0 4px 16px oklch(62% 0.20 18 / 25%);
    transform: scale(1.08);
  }
  .close-btn svg { width: 18px; height: 18px; transition: transform 0.2s; }
  .close-btn:hover svg { transform: rotate(90deg); }

  .image-stage {
    position: relative;
    width: 100%;
    display: flex;
    justify-content: center;
    margin-bottom: 1.5vh;
    transform: translateZ(0px);
  }

  .image-frame {
    position: relative;
    max-width: 720px;
    width: 100%;
    border-radius: 16px;
    overflow: hidden;
    box-shadow:
      0 40px 100px oklch(0% 0 0 / 60%),
      0 0 60px oklch(82% 0.14 210 / 8%),
      0 0 0 1px oklch(50% 0.03 255 / 10%);
    cursor: zoom-in;
    transition: transform 0.6s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.4s;
  }
  .image-frame:hover {
    transform: translateY(-4px);
    box-shadow:
      0 50px 120px oklch(0% 0 0 / 70%),
      0 0 80px oklch(82% 0.14 210 / 12%),
      0 0 0 1px oklch(82% 0.14 210 / 20%);
  }

  .image-frame img {
    display: block;
    width: 100%;
    height: auto;
    max-height: 55vh;
    object-fit: cover;
  }

  .image-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 300px;
    color: var(--overlay-faint);
  }
  .image-placeholder svg { width: 60px; height: 60px; }

  .image-tags {
    position: absolute;
    bottom: 14px;
    left: 14px;
    display: flex;
    gap: 6px;
    z-index: 2;
  }

  .tag {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 5px 10px;
    background: oklch(6% 0.01 255 / 60%);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-radius: 100px;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .tag-loc { color: oklch(78% 0.13 155); }
  .tag-date { color: var(--overlay-accent); }

  .image-expand {
    position: absolute;
    top: 14px;
    right: 14px;
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: oklch(6% 0.01 255 / 60%);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-radius: 50%;
    color: var(--overlay-accent);
    z-index: 2;
    opacity: 0.7;
    cursor: pointer;
    transition: opacity 0.2s;
  }
  .image-frame:hover .image-expand { opacity: 1; }
  .image-expand svg { width: 14px; height: 14px; }

  .description-panel {
    position: relative;
    max-width: 920px;
    width: 100%;
    margin: 0 auto 1.5vh;
    padding: 18px 24px;
    background: var(--overlay-glass);
    backdrop-filter: blur(24px) saturate(1.4);
    -webkit-backdrop-filter: blur(24px) saturate(1.4);
    border-radius: 14px;
    box-shadow:
      0 20px 60px oklch(0% 0 0 / 30%),
      0 0 0 1px oklch(50% 0.03 255 / 6%);
    transform: translateZ(10px);
  }

  .description-label {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: var(--overlay-accent);
    margin-bottom: 8px;
    opacity: 0.95;
  }

  .description-text {
    font-size: 15px;
    line-height: 1.75;
    color: oklch(86% 0.004 250);
    max-height: 240px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--overlay-accent-dim) transparent;
  }
  .description-text::-webkit-scrollbar { width: 2px; }
  .description-text::-webkit-scrollbar-thumb { background: var(--overlay-accent-dim); }

  .description-text :global(.overlay-p) { margin: 0.5em 0; }
  .description-text :global(.overlay-p:first-child) { margin-top: 0; }
  .description-text :global(.overlay-p:last-child) { margin-bottom: 0; }
  .description-text :global(h1),
  .description-text :global(h2),
  .description-text :global(h3),
  .description-text :global(h4) {
    margin: 1em 0 0.5em;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    color: var(--overlay-accent);
    line-height: 1.3;
    font-size: 13px;
  }
  .description-text :global(strong) { color: var(--overlay-fg); font-weight: 600; }
  .description-text :global(em) { color: var(--overlay-muted); }
  .description-text :global(.overlay-code) {
    margin: 0.6em 0;
    overflow-x: auto;
    padding: 8px 10px;
    background: oklch(8% 0.01 255);
    border-radius: 6px;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    line-height: 1.5;
    color: var(--overlay-fg);
  }
  .description-text :global(a) { color: var(--overlay-accent); text-decoration: underline; }
  .description-text :global(ul),
  .description-text :global(ol) { padding-left: 1.5em; margin: 0.5em 0; }
  .description-text :global(li) { margin: 0.25em 0; }
  .description-text :global(blockquote) {
    margin: 0.6em 0;
    padding-left: 12px;
    border-left: 2px solid var(--overlay-accent-dim);
    color: var(--overlay-muted);
  }

  .description-loading,
  .description-error,
  .description-empty {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 0;
    font-size: 12px;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    color: var(--overlay-faint);
  }
  .description-error { color: var(--overlay-danger); }

  .data-row {
    display: flex;
    gap: 12px;
    max-width: 920px;
    width: 100%;
    margin: 0 auto;
    flex-wrap: wrap;
    transform: translateZ(5px);
  }

  .data-panel {
    flex: 1;
    min-width: 200px;
    padding: 14px 18px;
    background: var(--overlay-glass-light);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border-radius: 12px;
    box-shadow: 0 0 0 1px oklch(50% 0.03 255 / 5%);
  }
  .exif-panel {
    flex-basis: 100%;
    min-width: 100%;
  }

  .panel-label {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--overlay-accent);
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--overlay-accent-dim);
  }
  .panel-label .count {
    color: var(--overlay-fg);
    font-weight: 700;
    margin-left: 6px;
  }

  .people-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .person {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 4px 8px 4px 4px;
    border-radius: 100px;
    transition: background 0.2s;
    cursor: pointer;
  }
  .person:hover { background: oklch(20% 0.02 255 / 30%); }

  .person-thumb {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    object-fit: cover;
    flex-shrink: 0;
    filter: saturate(0.8);
  }

  .person-initials {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 700;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
  }

  .person-silhouette {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: oklch(22% 0.02 255 / 50%);
    color: var(--overlay-faint);
  }
  .person-silhouette svg { width: 12px; height: 12px; }

  .person-name {
    font-size: 13px;
    color: var(--overlay-fg);
    white-space: nowrap;
  }
  .person-unnamed {
    font-size: 11px;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    color: var(--overlay-muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    border-radius: 100px;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    cursor: pointer;
    transition: filter 0.15s;
    letter-spacing: 0.02em;
  }
  .chip:hover { filter: brightness(1.25); }
  .chip-loc {
    color: oklch(78% 0.13 155);
    background: oklch(72% 0.13 155 / 8%);
  }
  .chip-event {
    color: oklch(80% 0.13 70);
    background: oklch(74% 0.13 70 / 8%);
  }
  .chip-entity {
    color: var(--overlay-muted);
    background: oklch(20% 0.02 255 / 25%);
  }
  .chip-entity:hover { color: var(--overlay-fg); }

  .exif-panel {
    padding: 16px 20px;
  }

  .exif-group {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .exif-group + .exif-group {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--overlay-hairline);
  }
  .exif-group-label {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--overlay-accent);
    margin-bottom: 6px;
  }
  .exif-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 0;
  }
  .exif-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 5px 8px;
    gap: 12px;
    border-radius: 6px;
    transition: background 0.15s;
  }
  .exif-row:hover {
    background: oklch(50% 0.03 255 / 6%);
  }
  .exif-label {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    color: var(--overlay-muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .exif-value {
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 12px;
    color: var(--overlay-fg);
    text-align: right;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }

  .stepper {
    display: flex;
    flex-wrap: wrap;
    gap: 3px 14px;
    padding: 8px 12px;
    background: var(--overlay-glass);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-radius: 12px;
    margin: 1vh auto 0;
    max-width: 720px;
    transform: translateZ(8px);
  }
  .step {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 11px;
    color: var(--overlay-muted);
  }
  .step.done { color: var(--overlay-success); }
  .step.current { color: var(--overlay-accent); }
  .step-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    border: 1px solid currentColor;
  }
  .step.done .step-dot { background: currentColor; }
  .step.current .step-dot { animation: status-pulse 1.6s ease-in-out infinite; }
  .step-dot svg { width: 8px; height: 8px; color: oklch(8% 0.02 260); }

  .action-area {
    display: flex;
    justify-content: center;
    margin-top: 1.5vh;
    transform: translateZ(15px);
  }
  .btn-delete {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 10px 22px;
    background: oklch(62% 0.20 18 / 10%);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border-radius: 100px;
    border: 1px solid oklch(62% 0.20 18 / 20%);
    font-family: ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    cursor: pointer;
    color: oklch(62% 0.20 18 / 80%);
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .btn-delete:hover:not(:disabled) {
    color: oklch(62% 0.20 18);
    background: oklch(62% 0.20 18 / 18%);
    border-color: oklch(62% 0.20 18 / 50%);
    transform: translateY(-1px);
    box-shadow: 0 8px 24px oklch(62% 0.20 18 / 15%);
  }
  .btn-delete:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-delete svg { width: 13px; height: 13px; opacity: 0.8; transition: opacity 0.2s; }
  .btn-delete:hover svg { opacity: 1; }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid var(--overlay-accent-dim);
    border-top-color: var(--overlay-accent);
    border-radius: 50%;
    animation: overlay-spin 0.8s linear infinite;
    flex-shrink: 0;
  }
  .spinner-sm { width: 10px; height: 10px; }
  @keyframes overlay-spin { to { transform: rotate(360deg); } }

  .fullscreen {
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: oklch(0% 0 0 / 97%);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
    animation: fs-in 0.3s ease-out;
  }
  @keyframes fs-in { from { opacity: 0; } to { opacity: 1; } }
  .fullscreen-img {
    max-width: 94vw;
    max-height: 94vh;
    object-fit: contain;
    border-radius: 8px;
    cursor: default;
  }
  .fullscreen-close {
    position: fixed;
    top: 20px;
    right: 20px;
    width: 40px;
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: oklch(100% 0 0 / 5%);
    border-radius: 50%;
    border: none;
    cursor: pointer;
    color: oklch(100% 0 0 / 50%);
    transition: background 0.2s, color 0.2s;
  }
  .fullscreen-close:hover {
    background: oklch(100% 0 0 / 10%);
    color: oklch(100% 0 0 / 90%);
  }

  .scene-inner > * {
    animation: float-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
  }
  .topbar { animation-delay: 0s; }
  .image-stage { animation-delay: 0.08s; }
  .description-panel { animation-delay: 0.16s; }
  .data-row { animation-delay: 0.24s; }
  .stepper { animation-delay: 0.28s; }
  .action-area { animation-delay: 0.32s; }
  @keyframes float-in {
    from { opacity: 0; transform: translateY(20px) translateZ(-30px); }
    to { opacity: 1; }
  }

  @media (max-width: 768px) {
    .spatial-scene { padding: 2vh 4vw; perspective: none; }
    .scene-inner { transform: none !important; }
    .image-frame { border-radius: 12px; }
    .image-frame img { max-height: 40vh; }
    .description-panel { padding: 14px 18px; border-radius: 12px; }
    .description-text { font-size: 13px; line-height: 1.65; }
    .data-row { flex-direction: column; gap: 8px; }
    .data-panel { min-width: 100%; border-radius: 10px; }
    .topbar { margin-bottom: 1.5vh; }
    .image-stage,
    .description-panel,
    .data-row,
    .stepper,
    .action-area {
      transform: none !important;
    }
  }

  @media (max-width: 480px) {
    .spatial-scene { padding: 1.5vh 3vw; }
    .image-frame img { max-height: 35vh; }
    .description-panel { padding: 12px 14px; }
    .description-text { font-size: 12px; }
    .people-row { gap: 6px; }
    .person-name { font-size: 10px; }
    .filename { display: none; }
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
