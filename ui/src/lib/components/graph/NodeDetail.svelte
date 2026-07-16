<script lang="ts">
  import { type KGNode, type KGEdge, API } from '$lib/constants';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { eventBus } from '$lib/stores/event-bus.svelte';
  const ACCENT_COLORS = ['#00d4ff', '#a855f7', '#00ff88', '#ff8c00'];

  function formatCreatedAt(value: unknown): string {
    if (value === null || value === undefined || value === '') return '—';
    let date: Date | null = null;
    if (typeof value === 'number' || (typeof value === 'string' && /^\d+$/.test(value))) {
      const n = Number(value);
      // Treat as seconds if < 10^12 (epoch seconds), otherwise ms
      const ms = n < 1e12 ? n * 1000 : n;
      if (!Number.isNaN(ms)) date = new Date(ms);
    } else if (typeof value === 'string') {
      const parsed = new Date(value);
      if (!Number.isNaN(parsed.getTime())) date = parsed;
    }
    if (!date || Number.isNaN(date.getTime())) {
      return typeof value === 'object' ? JSON.stringify(value) : String(value);
    }
    return date.toLocaleString();
  }

  let {
    node = null,
    neighbors = { nodes: [], edges: [] },
    onclose = () => {},
    onqueryAbout = (_node: KGNode) => {},
    onselectNode = (_node: KGNode) => {}
  }: {
    node?: KGNode | null;
    neighbors?: { nodes: KGNode[]; edges: KGEdge[] };
    onclose?: () => void;
    onqueryAbout?: (node: KGNode) => void;
    onselectNode?: (node: KGNode) => void;
  } = $props();

  // Image loading states
  let personPhotoError = $state(false);
  let imagePreviewUrl = $state<string | null>(null);
  let imagePreviewLoading = $state(false);
  let imagePreviewError = $state(false);
  let neighborPhotoErrors = $state<Set<string>>(new Set());
  let neighborImageUrls = $state<Map<string, string>>(new Map());
  let neighborImageErrors = $state<Set<string>>(new Set());

  // Connected entities grouped by type for the Connected Entities panel
  let groupedNeighbors = $derived.by(() => {
    const nodes = neighbors?.nodes ?? [];
    const persons = nodes.filter((n) => isPersonNode(n));
    const locations = nodes.filter((n) => isLocationNode(n));
    const dates = nodes.filter((n) => isDateNode(n));
    const others = nodes.filter(
      (n) => !isPersonNode(n) && !isLocationNode(n) && !isDateNode(n)
    );
    return { persons, locations, dates, others };
  });

  // Resolve image content URL for image nodes (also handles reset on node change)
  $effect(() => {
    const currentNode = node;
    const currentNodeId = node?.id;
    personPhotoError = false;
    neighborPhotoErrors = new Set();
    neighborImageUrls = new Map();
    neighborImageErrors = new Set();
    // Reset label editing state when the selected node changes
    labelEditing = false;
    labelValue = '';
    labelError = null;
    labelSubmitting = false;
    clearLabelSuccess();

    if (!currentNode || !isImageNode(currentNode)) {
      imagePreviewUrl = null;
      imagePreviewLoading = false;
      imagePreviewError = false;
      return;
    }
    const filePath = currentNode.properties?.file_path as string | undefined;
    if (!filePath) {
      imagePreviewUrl = null;
      imagePreviewLoading = false;
      imagePreviewError = false;
      return;
    }
    imagePreviewLoading = true;
    imagePreviewError = false;
    imagePreviewUrl = null;
    lightragClient.resolveImageContentUrl(filePath).then((url) => {
      if (node?.id !== currentNodeId) return;
      imagePreviewUrl = url;
      imagePreviewLoading = false;
    }).catch((err) => {
      console.error('[NodeDetail] resolveImageContentUrl error:', err);
      if (node?.id !== currentNodeId) return;
      imagePreviewError = true;
      imagePreviewLoading = false;
    });
  });

  // Resolve image thumbnails for neighbor image nodes
  $effect(() => {
    const imageNeighbors = (neighbors?.nodes ?? []).filter(
      (n) => isImageNode(n) && !neighborImageErrors.has(n.id)
    );
    for (const n of imageNeighbors) {
      const filePath = n.properties?.file_path as string | undefined;
      if (!filePath) continue;
      lightragClient.resolveImageContentUrl(filePath).then((url) => {
        if (url) {
          const updated = new Map(neighborImageUrls);
          updated.set(n.id, url);
          neighborImageUrls = updated;
        }
      }).catch(() => {
        const updated = new Set(neighborImageErrors);
        updated.add(n.id);
        neighborImageErrors = updated;
      });
    }
  });

  function hashColor(label: string): string {
    let hash = 0;
    for (let i = 0; i < label.length; i++) {
      hash = (hash * 31 + label.charCodeAt(i)) | 0;
    }
    return ACCENT_COLORS[Math.abs(hash) % ACCENT_COLORS.length];
  }

  function getNodeName(n: KGNode): string {
    return (n.properties?.name as string) ?? (n.properties?.title as string) ?? n.id;
  }

  function isPersonNode(n: KGNode): boolean {
    const et = n.properties?.entity_type;
    if (typeof et === 'string') return et.toLowerCase() === 'person';
    return n.labels.some((l) => l.toLowerCase() === 'person');
  }

  function isLocationNode(n: KGNode): boolean {
    const et = n.properties?.entity_type;
    if (typeof et === 'string') return et.toLowerCase() === 'location';
    return n.labels.some((l) => l.toLowerCase() === 'location');
  }

  function isDateNode(n: KGNode): boolean {
    const et = n.properties?.entity_type;
    if (typeof et === 'string') return et.toLowerCase() === 'date';
    return n.labels.some((l) => l.toLowerCase() === 'date');
  }

  function isImageNode(n: KGNode): boolean {
    const et = n.properties?.entity_type;
    if (typeof et === 'string') return et.toLowerCase() === 'image';
    return n.id.toLowerCase().includes('(image)');
  }

  function isPhotoNode(n: KGNode): boolean {
    const et = n.properties?.entity_type;
    if (typeof et === 'string') return et.toLowerCase() === 'photo';
    return n.id.toLowerCase().includes('(photo)');
  }

  let reprocessing = $state(false);

  function getFileSourceFromPhoto(n: KGNode): string | null {
    const src = n.properties?.source_id as string | undefined;
    if (src) return src;
    const match = n.id.match(/^(.+)\s*\(Photo\)$/i);
    return match ? match[1].trim() : null;
  }

  async function handleReprocess() {
    if (!node || reprocessing) return;
    const fileSource = getFileSourceFromPhoto(node);
    if (!fileSource) return;
    reprocessing = true;
    try {
      const { stream } = kgApiClient.reprocessImageSse(fileSource);
      const nodeId = node.id;
      imageProcessingStore.startProcessing(nodeId, fileSource, '');
      for await (const { data } of stream) {
        try {
          const parsed = JSON.parse(data);
          const eventName: string = parsed.event ?? '';
          const stage = imageProcessingStore.mapEventToStage(eventName);
          if (stage) imageProcessingStore.updateStage(nodeId, stage);
          if (eventName === 'pipeline_complete' || eventName === 'upload_failed') {
            const error = parsed.data?.error ?? parsed.data?.reason;
            if (error) imageProcessingStore.updateStage(nodeId, 'error', String(error));
            break;
          }
        } catch { /* ignore parse errors */ }
      }
    } catch (err) {
      console.error('Reprocess failed:', err);
      if (node) imageProcessingStore.updateStage(node.id, 'error', String(err));
    } finally {
      reprocessing = false;
    }
  }

  function getPersonPhotoUrl(n: KGNode): string {
    return lightragClient.personPhotoUrl(n.id);
  }

  function getPersonFaceCropUrl(n: KGNode): string | null {
    const faceId = n.properties?.face_id as string | undefined;
    if (!faceId) return null;
    return `${KG_API_PROXY_BASE}${API.kg.faceCropById(faceId)}`;
  }

  /** Resolve the best available photo URL for a person node:
   *  prefers face_id-based crop, falls back to name-based URL. */
  function resolvePersonPhotoUrl(n: KGNode): string {
    return getPersonFaceCropUrl(n) ?? getPersonPhotoUrl(n);
  }

  const KG_API_PROXY_BASE = '/api/kg';

  // --- Face labeling state ---
  let labelEditing = $state(false);
  let labelValue = $state('');
  let labelSubmitting = $state(false);
  let labelError = $state<string | null>(null);
  let labelSuccess = $state(false);
  let labelSuccessTimer: ReturnType<typeof setTimeout> | null = null;

  function getFaceId(n: KGNode | null | undefined): string | null {
    if (!n) return null;
    const faceId = n.properties?.face_id as string | undefined;
    return typeof faceId === 'string' && faceId.length > 0 ? faceId : null;
  }

  function startLabelEdit() {
    if (!node) return;
    labelValue = getNodeName(node);
    labelError = null;
    labelEditing = true;
  }

  function cancelLabelEdit() {
    labelEditing = false;
    labelError = null;
    labelValue = '';
  }

  function clearLabelSuccess() {
    if (labelSuccessTimer !== null) {
      clearTimeout(labelSuccessTimer);
      labelSuccessTimer = null;
    }
    labelSuccess = false;
  }

  async function submitLabel() {
    if (!node) return;
    const faceId = getFaceId(node);
    if (!faceId) {
      labelError = 'This person has no associated face crop to label.';
      return;
    }
    const newName = labelValue.trim();
    if (!newName) {
      labelError = 'Name cannot be empty.';
      return;
    }
    if (newName === getNodeName(node)) {
      labelEditing = false;
      return;
    }
    labelSubmitting = true;
    labelError = null;
    try {
      const result = await kgApiClient.labelFace(faceId, newName);
      // Update the local node's name property optimistically
      if (node) {
        const updatedProperties = { ...node.properties, name: result.new_name };
        const updatedNode: KGNode = { ...node, properties: updatedProperties };
        graphStore.upsertNode(node.id, node.labels, updatedProperties);
        // Refresh the selected node reference if the store is driving the panel
        if (graphStore.selectedNode?.id === node.id) {
          graphStore.selectNode(updatedNode);
        }
        // Clear cached person image (old name-based URL is stale)
        delete graphStore.personImages[node.id];
        graphStore.personImages = { ...graphStore.personImages };
      }
      // Reload the graph neighborhood to pick up backend changes
      graphStore.refresh();
      // Reflect success in the UI
      labelEditing = false;
      labelValue = '';
      clearLabelSuccess();
      labelSuccess = true;
      labelSuccessTimer = setTimeout(() => clearLabelSuccess(), 2500);
      eventBus.pushEvent({
        id: crypto.randomUUID(),
        type: 'graph_update',
        title: 'Face labeled',
        description: `Renamed "${result.old_name}" to "${result.new_name}"`,
        timestamp: Date.now(),
        status: 'completed',
        meta: { face_id: faceId, old_name: result.old_name, new_name: result.new_name },
      });
    } catch (err) {
      labelError = err instanceof Error ? err.message : 'Labeling failed.';
    } finally {
      labelSubmitting = false;
    }
  }

  function handleLabelKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter') {
      event.preventDefault();
      submitLabel();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelLabelEdit();
    }
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


</script>

{#if node}
  <div class="absolute inset-y-0 right-0 z-[110] w-80 max-w-[90vw] bg-cyber-surface/95 backdrop-blur-md border-l border-cyber-border animate-slide-in-right flex flex-col overflow-hidden">
    <!-- Header with optional person face crop -->
    <div class="flex items-start justify-between p-4 border-b border-cyber-border">
      <div class="min-w-0 flex-1 flex items-center gap-3">
        {#if isPersonNode(node)}
          <div class="shrink-0">
            {#if !personPhotoError}
              <img
                src={resolvePersonPhotoUrl(node)}
                alt={getNodeName(node)}
                class="w-14 h-14 rounded-full object-cover border-2 border-cyber-cyan/40"
                onerror={() => { personPhotoError = true; }}
              />
            {:else}
              <div
                class="w-14 h-14 rounded-full flex items-center justify-center text-sm font-bold border-2 border-cyber-cyan/30"
                style="background: {hashColor(node.labels?.[0] ?? 'default')}20; color: {hashColor(node.labels?.[0] ?? 'default')};"
              >
                {getInitials(getNodeName(node))}
              </div>
            {/if}
          </div>
        {/if}
        <div class="min-w-0">
          <h2 class="text-lg font-semibold text-cyber-cyan text-glow-cyan truncate">
            {getNodeName(node)}
          </h2>
          <div class="flex flex-wrap gap-1.5 mt-1">
            {#each node.labels as label}
              <span
                class="inline-block px-2 py-0.5 text-[10px] uppercase tracking-wider rounded-full border font-medium"
                style="color: {hashColor(label)}; border-color: {hashColor(label)}40; background: {hashColor(label)}10;"
              >
                {label}
              </span>
            {/each}
          </div>
        </div>
      </div>
      <button
        onclick={onclose}
        aria-label="Close detail panel"
        class="ml-2 w-7 h-7 flex items-center justify-center rounded-lg text-cyber-text-dim hover:text-cyber-cyan hover:bg-cyber-cyan/10 transition-colors shrink-0"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>

    <div class="flex-1 overflow-y-auto p-4 space-y-4">
      <!-- Image preview for image nodes -->
      {#if isImageNode(node)}
        <section>
          {#if imagePreviewLoading}
            <div class="w-full h-48 rounded-lg bg-cyber-surface-2 animate-pulse overflow-hidden relative">
              <div class="absolute inset-0 bg-gradient-to-r from-transparent via-cyber-cyan/5 to-transparent" style="animation: data-flow 3s linear infinite; background-size: 200% 100%;"></div>
            </div>
          {:else if imagePreviewError || !imagePreviewUrl}
            <div class="w-full h-24 rounded-lg bg-cyber-surface-2 border border-cyber-border flex items-center justify-center">
              <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="text-cyber-text-dim"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
            </div>
          {:else}
            <img
              src={imagePreviewUrl}
              alt={getNodeName(node)}
              class="w-full max-h-[200px] object-cover rounded-lg border border-cyber-border"
              onerror={() => { imagePreviewError = true; }}
            />
          {/if}
          {#if node.properties?.description}
            <p class="mt-2 text-xs text-cyber-text leading-relaxed">{node.properties.description}</p>
          {/if}
          <div class="flex gap-3 mt-2 text-[10px] text-cyber-text-dim">
            {#if node.properties?.location}
              <span class="flex items-center gap-1">
                <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>
                {node.properties.location}
              </span>
            {/if}
            {#if node.properties?.date_taken_friendly}
              <span class="flex items-center gap-1">
                <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>
                {node.properties.date_taken_friendly}
              </span>
            {/if}
          </div>
        </section>
      {/if}

      <!-- Person description -->
      {#if isPersonNode(node) && node.properties?.description}
        <section>
          <p class="text-xs text-cyber-text leading-relaxed">{node.properties.description}</p>
        </section>
      {/if}

      <!-- Person face labeling -->
      {#if isPersonNode(node)}
        <section class="space-y-2">
          {#if labelSuccess}
            <div class="flex items-center gap-1.5 text-xs text-cyber-green animate-fade-in">
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
              <span>Labeled</span>
            </div>
          {/if}
          {#if !labelEditing}
            <button
              onclick={startLabelEdit}
              disabled={!getFaceId(node)}
              class="flex items-center gap-1.5 text-xs text-cyber-text-dim hover:text-cyber-cyan transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              title={getFaceId(node) ? 'Rename this person' : 'No face crop associated'}
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg>
              Label
            </button>
          {:else}
            <div class="flex flex-col gap-1.5">
              <div class="flex items-center gap-1.5">
                <input
                  type="text"
                  bind:value={labelValue}
                  onkeydown={handleLabelKeydown}
                  disabled={labelSubmitting}
                  placeholder="New name"
                  class="flex-1 min-w-0 px-2 py-1 text-xs rounded-md bg-cyber-surface-2 border border-cyber-border text-cyber-text placeholder:text-cyber-text-dim focus:outline-none focus:border-cyber-cyan/60 focus:ring-1 focus:ring-cyber-cyan/30 transition-colors"
                />
                <button
                  onclick={submitLabel}
                  disabled={labelSubmitting || !labelValue.trim()}
                  class="shrink-0 px-2 py-1 text-xs rounded-md bg-cyber-cyan/10 text-cyber-cyan border border-cyber-cyan/30 hover:bg-cyber-cyan/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Confirm"
                >
                  {#if labelSubmitting}
                    <svg class="inline h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="31.4 31.4" stroke-linecap="round"/></svg>
                  {:else}
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                  {/if}
                </button>
                <button
                  onclick={cancelLabelEdit}
                  disabled={labelSubmitting}
                  class="shrink-0 px-2 py-1 text-xs rounded-md text-cyber-text-dim hover:text-cyber-text border border-cyber-border hover:border-cyber-text-dim transition-colors disabled:opacity-50"
                  title="Cancel"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
              {#if labelError}
                <p class="text-xs text-cyber-red">{labelError}</p>
              {/if}
            </div>
          {/if}
        </section>
      {/if}

      <section>
        <h3 class="text-xs uppercase tracking-widest text-cyber-text-dim mb-2">Properties</h3>
        <div class="space-y-1.5">
          {#each Object.entries(node.properties ?? {}) as [key, value]}
            {@const isMediaKey = key === 'profile_photo' || key === 'file_path' || key === 'entity_type'}
            {#if !isMediaKey}
              <div class="flex gap-2 text-xs">
                <span class="text-cyber-text-dim shrink-0 min-w-20">{key}</span>
                <span class="font-mono text-cyber-text break-all">
                  {#if key === 'created_at'}
                    {formatCreatedAt(value)}
                  {:else}
                    {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                  {/if}
                </span>
              </div>
            {/if}
          {/each}
        </div>
      </section>

      <section>
        <h3 class="text-xs uppercase tracking-widest text-cyber-text-dim mb-2">
          Connected Entities
          <span class="text-cyber-cyan ml-1">({neighbors.nodes.length})</span>
        </h3>
        {#snippet entityRow(n: KGNode)}
          {@const isPerson = isPersonNode(n)}
          {@const isImage = isImageNode(n)}
          <button
            type="button"
            class="flex items-center gap-2 px-2 py-1 w-full rounded-md hover:bg-cyber-surface-2/50 transition-colors group text-left cursor-pointer"
            onclick={() => onselectNode(n)}
            title={`Open ${getNodeName(n)}`}
          >
            {#if isPerson}
              {#if !neighborPhotoErrors.has(n.id)}
                <img
                  src={resolvePersonPhotoUrl(n)}
                  alt={getNodeName(n)}
                  class="w-6 h-6 rounded-full object-cover shrink-0 border border-cyber-cyan/30"
                  onerror={() => {
                    const updated = new Set(neighborPhotoErrors);
                    updated.add(n.id);
                    neighborPhotoErrors = updated;
                  }}
                />
              {:else}
                <div
                  class="w-6 h-6 rounded-full flex items-center justify-center text-[8px] font-bold shrink-0 border border-cyber-cyan/20"
                  style="background: {hashColor(n.labels?.[0] ?? 'default')}20; color: {hashColor(n.labels?.[0] ?? 'default')};"
                >
                  {getInitials(getNodeName(n))}
                </div>
              {/if}
            {:else if isImage && neighborImageUrls.has(n.id)}
              <img
                src={neighborImageUrls.get(n.id)!}
                alt={getNodeName(n)}
                class="w-6 h-6 rounded object-cover shrink-0 border border-cyber-border"
              />
            {:else}
              <span
                class="w-2 h-2 rounded-full shrink-0"
                style="background: {hashColor(n.labels?.[0] ?? 'default')}"
              ></span>
            {/if}
            <span class="text-xs text-cyber-text truncate flex-1 group-hover:text-cyber-cyan transition-colors">
              {getNodeName(n)}
            </span>
            <span class="text-[9px] text-cyber-text-dim uppercase">{n.labels?.[0] ?? ''}</span>
          </button>
        {/snippet}
        <div class="space-y-3">
          {#if groupedNeighbors.persons.length > 0}
            <div>
              <div class="text-[10px] uppercase tracking-widest text-cyber-cyan/70 mb-1 px-2">
                People ({groupedNeighbors.persons.length})
              </div>
              <div class="space-y-1">
                {#each groupedNeighbors.persons as n}
                  {@render entityRow(n)}
                {/each}
              </div>
            </div>
          {/if}
          {#if groupedNeighbors.locations.length > 0}
            <div>
              <div class="text-[10px] uppercase tracking-widest text-cyber-cyan/70 mb-1 px-2">
                Locations ({groupedNeighbors.locations.length})
              </div>
              <div class="space-y-1">
                {#each groupedNeighbors.locations as n}
                  {@render entityRow(n)}
                {/each}
              </div>
            </div>
          {/if}
          {#if groupedNeighbors.dates.length > 0}
            <div>
              <div class="text-[10px] uppercase tracking-widest text-cyber-cyan/70 mb-1 px-2">
                Dates ({groupedNeighbors.dates.length})
              </div>
              <div class="space-y-1">
                {#each groupedNeighbors.dates as n}
                  {@render entityRow(n)}
                {/each}
              </div>
            </div>
          {/if}
          {#if groupedNeighbors.others.length > 0}
            <div>
              <div class="text-[10px] uppercase tracking-widest text-cyber-text-dim mb-1 px-2">
                Other ({groupedNeighbors.others.length})
              </div>
              <div class="space-y-1">
                {#each groupedNeighbors.others as n}
                  {@render entityRow(n)}
                {/each}
              </div>
            </div>
          {/if}
          {#if neighbors.nodes.length === 0}
            <div class="text-xs text-cyber-text-dim px-2 py-1">No connected entities</div>
          {/if}
        </div>
      </section>


    </div>

    <div class="p-3 border-t border-cyber-border flex gap-2">
      {#if isPhotoNode(node!) && getFileSourceFromPhoto(node!)}
        <button
          onclick={handleReprocess}
          disabled={reprocessing}
          class="flex-1 py-2 rounded-lg text-xs font-medium bg-cyber-green/10 text-cyber-green border border-cyber-green/30 hover:bg-cyber-green/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {#if reprocessing}
            <svg class="inline h-3 w-3 animate-spin mr-1" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="31.4 31.4" stroke-linecap="round"/></svg>
            Reprocessing...
          {:else}
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="inline mr-1"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/></svg>
            Reprocess
          {/if}
        </button>
      {/if}
      <button
        onclick={() => onqueryAbout(node!)}
        class="flex-1 py-2 rounded-lg text-xs font-medium bg-cyber-cyan/10 text-cyber-cyan border border-cyber-cyan/30 hover:bg-cyber-cyan/20 transition-colors"
      >
        Query about this
      </button>
    </div>
  </div>
{:else}
{/if}