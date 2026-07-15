<script lang="ts">
  import { type KGNode, type KGEdge } from '$lib/constants';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  const ACCENT_COLORS = ['#00d4ff', '#a855f7', '#00ff88', '#ff8c00'];

  let {
    node = null,
    neighbors = { nodes: [], edges: [] },
    onclose = () => {},
    onqueryAbout = (_node: KGNode) => {},
    onexpandNeighbors = (_node: KGNode) => {}
  }: {
    node?: KGNode | null;
    neighbors?: { nodes: KGNode[]; edges: KGEdge[] };
    onclose?: () => void;
    onqueryAbout?: (node: KGNode) => void;
    onexpandNeighbors?: (node: KGNode) => void;
  } = $props();

  // Image loading states
  let personPhotoError = $state(false);
  let imagePreviewUrl = $state<string | null>(null);
  let imagePreviewLoading = $state(false);
  let imagePreviewError = $state(false);
  let neighborPhotoErrors = $state<Set<string>>(new Set());
  let neighborImageUrls = $state<Map<string, string>>(new Map());
  let neighborImageErrors = $state<Set<string>>(new Set());

  // Resolve image content URL for image nodes (also handles reset on node change)
  $effect(() => {
    const currentNode = node;
    const currentNodeId = node?.id;
    personPhotoError = false;
    neighborPhotoErrors = new Set();
    neighborImageUrls = new Map();
    neighborImageErrors = new Set();

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

  function getInitials(name: string): string {
    return name
      .split(/[\s_]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase();
  }

  const directionIcon = (edge: KGEdge, nodeId: string): string => {
    if (edge.source === nodeId) return '→';
    if (edge.target === nodeId) return '←';
    return '↔';
  };
</script>

{#if node}
  <div class="absolute inset-y-0 right-0 z-30 w-80 max-w-[90vw] bg-cyber-surface/95 backdrop-blur-md border-l border-cyber-border animate-slide-in-right flex flex-col overflow-hidden">
    <!-- Header with optional person face crop -->
    <div class="flex items-start justify-between p-4 border-b border-cyber-border">
      <div class="min-w-0 flex-1 flex items-center gap-3">
        {#if isPersonNode(node)}
          <div class="shrink-0">
            {#if !personPhotoError}
              <img
                src={getPersonPhotoUrl(node)}
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

      <section>
        <h3 class="text-xs uppercase tracking-widest text-cyber-text-dim mb-2">Properties</h3>
        <div class="space-y-1.5">
          {#each Object.entries(node.properties ?? {}) as [key, value]}
            {@const isMediaKey = key === 'profile_photo' || key === 'file_path' || key === 'entity_type'}
            {#if !isMediaKey}
              <div class="flex gap-2 text-xs">
                <span class="text-cyber-text-dim shrink-0 min-w-20">{key}</span>
                <span class="font-mono text-cyber-text break-all">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>
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
        <div class="space-y-1">
          {#each neighbors.nodes as n}
            {@const isPerson = isPersonNode(n)}
            {@const isImage = isImageNode(n)}
            <div class="flex items-center gap-2 px-2 py-1 rounded-md hover:bg-cyber-surface-2/50 transition-colors group">
              {#if isPerson}
                {#if !neighborPhotoErrors.has(n.id)}
                  <img
                    src={getPersonPhotoUrl(n)}
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
            </div>
          {/each}
        </div>
      </section>

      <section>
        <h3 class="text-xs uppercase tracking-widest text-cyber-text-dim mb-2">
          Relationships
          <span class="text-cyber-cyan ml-1">({neighbors.edges.length})</span>
        </h3>
        <div class="space-y-1">
          {#each neighbors.edges as edge}
            {@const dir = directionIcon(edge, node!.id)}
            <div class="flex items-center gap-1.5 px-2 py-1 rounded-md hover:bg-cyber-surface-2/50 transition-colors">
              <span class="text-cyber-cyan text-sm">{dir}</span>
              <span class="text-xs text-cyber-text-dim font-mono truncate">{edge.type}</span>
              <span class="text-[10px] text-cyber-text-dim truncate">
                {dir === '→' ? (edge.target === node!.id ? edge.source : edge.target) : (dir === '←' ? edge.source : edge.target)}
              </span>
            </div>
          {/each}
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
      <button
        onclick={() => onexpandNeighbors(node!)}
        class="flex-1 py-2 rounded-lg text-xs font-medium bg-cyber-purple/10 text-cyber-purple border border-cyber-purple/30 hover:bg-cyber-purple/20 transition-colors"
      >
        Expand neighbors
      </button>
    </div>
  </div>
{:else}
{/if}