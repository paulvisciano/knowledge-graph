<script lang="ts">
  import { lightragClient } from '$lib/services/lightrag-client';
  import { isMobile } from '$lib/composables/use-breakpoint';
  import type { KGNode } from '$lib/constants';

  interface EnrichedResult {
    label: string;
    entityType: string;
    description: string;
    profilePhoto?: string;
    filePath?: string;
    related?: boolean;
  }

  const ENTITY_TYPE_COLORS: Record<string, { bg: string; text: string; border: string }> = {
    person: { bg: 'rgba(0, 212, 255, 0.1)', text: '#00d4ff', border: 'rgba(0, 212, 255, 0.3)' },
    image: { bg: 'rgba(168, 85, 247, 0.1)', text: '#a855f7', border: 'rgba(168, 85, 247, 0.3)' },
    location: { bg: 'rgba(0, 255, 136, 0.1)', text: '#00ff88', border: 'rgba(0, 255, 136, 0.3)' },
    event: { bg: 'rgba(255, 214, 0, 0.1)', text: '#ffd600', border: 'rgba(255, 214, 0, 0.3)' },
    organization: { bg: 'rgba(255, 140, 0, 0.1)', text: '#ff8c00', border: 'rgba(255, 140, 0, 0.3)' },
    concept: { bg: 'rgba(168, 85, 247, 0.1)', text: '#a855f7', border: 'rgba(168, 85, 247, 0.3)' },
  };

  const DEFAULT_TYPE_COLOR = { bg: 'rgba(90, 107, 128, 0.1)', text: '#5a6b80', border: 'rgba(90, 107, 128, 0.3)' };

  const PAGE_SIZE = $derived($isMobile ? 20 : 50);
  const MAX_RELATED = 200;

  /** Sort priority for entity types: people → events → locations → images → other */
  const ENTITY_TYPE_PRIORITY: Record<string, number> = {
    person: 0,
    PERSON: 0,
    event: 1,
    Event: 1,
    location: 2,
    Location: 2,
    organization: 3,
    Organization: 3,
    concept: 4,
    Concept: 4,
    artifact: 5,
    content: 6,
    Content: 6,
    image: 7,
    Image: 7,
  };

  function entityTypeSortPriority(entityType: string): number {
    return ENTITY_TYPE_PRIORITY[entityType] ?? 5;
  }

  /** Human-readable group labels for entity type sections */
  const ENTITY_TYPE_GROUP_LABELS: Record<string, string> = {
    person: 'People',
    PERSON: 'People',
    event: 'Events',
    Event: 'Events',
    location: 'Locations',
    Location: 'Locations',
    organization: 'Organizations',
    Organization: 'Organizations',
    concept: 'Concepts',
    Concept: 'Concepts',
    artifact: 'Artifacts',
    content: 'Content',
    Content: 'Content',
    image: 'Images',
    Image: 'Images',
  };

  function entityTypeGroupLabel(entityType: string): string {
    return ENTITY_TYPE_GROUP_LABELS[entityType] ?? 'Other';
  }

  let {
    onselectLabel = (_label: string) => {},
    onsearch = (_query: string) => {}
  }: {
    onselectLabel: (label: string) => void;
    onsearch: (query: string) => void;
  } = $props();

  let query = $state('');
  let isOpen = $state(false);
  let isLoading = $state(false);
  let isLoadingMore = $state(false);
  let results = $state<EnrichedResult[]>([]);
  let allLabels = $state<string[]>([]);
  let currentOffset = 0;
  let hasMore = $state(true);
  let currentQuery = '';
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let inputRef: HTMLInputElement | undefined = $state();
  let nodeCache = new Map<string, EnrichedResult>();
  let resolvedImageUrls = $state<Map<string, string>>(new Map());
  let sentinelRef: HTMLDivElement | undefined = $state();

  function getTypeColor(entityType: string) {
    const key = entityType.toLowerCase();
    return ENTITY_TYPE_COLORS[key] ?? DEFAULT_TYPE_COLOR;
  }

  function getTypeBadgeLabel(entityType: string): string {
    return entityType.toUpperCase();
  }

  function truncate(str: string, maxLen: number): string {
    if (!str) return '';
    return str.length > maxLen ? str.slice(0, maxLen) + '…' : str;
  }

  function strProp(props: Record<string, unknown>, key: string, fallback: string = ''): string {
    const val = props[key];
    return typeof val === 'string' ? val : fallback;
  }

  function extractNodeData(label: string, node: KGNode): EnrichedResult {
    const props = node.properties ?? {};
    const entityType = strProp(props, 'entity_type', 'unknown');
    const description = strProp(props, 'description');
    const profilePhoto = strProp(props, 'profile_photo') || undefined;
    const filePath = strProp(props, 'file_path') || undefined;

    return {
      label,
      entityType,
      description,
      profilePhoto: entityType.toLowerCase() === 'person' ? profilePhoto : undefined,
      filePath: entityType.toLowerCase() === 'image' ? filePath : undefined,
      related: false,
    };
  }

  /**
   * Pre-populate the nodeCache with nodes from a graph response,
   * avoiding individual getGraph() calls for each label.
   */
  function cacheGraphNodes(nodes: KGNode[]): void {
    for (const node of nodes) {
      const label = node.id;
      if (!nodeCache.has(label)) {
        nodeCache.set(label, extractNodeData(label, node));
      }
    }
  }

  async function fetchEnrichedResults(labels: string[]): Promise<EnrichedResult[]> {
    const uncached = labels.filter((l) => !nodeCache.has(l));
    const concurrencyLimit = 5;

    // Fetch in batches of 5 — only for labels not already cached
    for (let i = 0; i < uncached.length; i += concurrencyLimit) {
      const batch = uncached.slice(i, i + concurrencyLimit);
      const settled = await Promise.allSettled(
        batch.map(async (label) => {
          const graph = await lightragClient.getGraph(label);
          const matchingNode = graph.nodes.find(
            (n) => n.id === label || (n.properties?.name as string) === label
          );
          if (matchingNode) {
            return { label, node: matchingNode, allNodes: graph.nodes };
          }
          return { label, node: null, allNodes: graph.nodes };
        })
      );

      for (const result of settled) {
        if (result.status === 'fulfilled') {
          // Cache all nodes from the graph response — they come for free
          if (result.value.allNodes?.length) {
            for (const node of result.value.allNodes) {
              if (!nodeCache.has(node.id)) {
                nodeCache.set(node.id, extractNodeData(node.id, node));
              }
            }
          }
          if (result.value.node) {
            const enriched = extractNodeData(result.value.label, result.value.node);
            nodeCache.set(result.value.label, enriched);
          } else {
            // No matching node found — store a minimal result
            nodeCache.set(result.value.label, {
              label: result.value.label,
              entityType: 'unknown',
              description: '',
              related: false,
            });
          }
        }
      }
    }

    return labels
      .map((l) => nodeCache.get(l) ?? { label: l, entityType: 'unknown', description: '', related: false });
  }

  async function fetchNextPage() {
    if (!hasMore || isLoadingMore) return;
    isLoadingMore = true;
    try {
      const newLabels = await lightragClient.searchLabels(currentQuery, PAGE_SIZE, currentOffset);
      if (newLabels.length === 0) {
        hasMore = false;
        // If first page returned empty, show "no results"
        if (currentOffset === 0) {
          results = [];
        }
      } else {
        const enriched = await fetchEnrichedResults(newLabels);
        allLabels = [...allLabels, ...newLabels];
        currentOffset = allLabels.length;
        hasMore = newLabels.length === PAGE_SIZE;
        // Only update if query hasn't changed since we started
        if (query.trim() === currentQuery) {
          results = [...results, ...enriched];
        }
      }
    } catch {
      if (currentOffset === 0) {
        results = [];
      }
    } finally {
      isLoadingMore = false;
    }
  }

  $effect(() => {
    if (debounceTimer) clearTimeout(debounceTimer);
    if (!query.trim()) {
      results = [];
      allLabels = [];
      hasMore = false;
      isLoading = false;
      return;
    }
    isLoading = true;
    const trimmedQuery = query.trim();
    debounceTimer = setTimeout(async () => {
      try {
        // Reset state for new query
        currentQuery = trimmedQuery;
        allLabels = [];
        currentOffset = 0;
        hasMore = true;
        nodeCache.clear();
        resolvedImageUrls = new Map();
        onsearch(trimmedQuery);
        await fetchNextPage();

        // Fetch neighborhoods for top 3 direct matches to find related entities
        const topDirectMatches = results.filter(r => !r.related).slice(0, 3);
        const seenLabels = new Set(allLabels);
        const relatedLabels: string[] = [];

        for (const match of topDirectMatches) {
          try {
            const graph = await lightragClient.getGraph(match.label);
            // Cache all nodes from the graph response — avoids re-fetching them later
            cacheGraphNodes(graph.nodes);
            for (const node of graph.nodes) {
              if (relatedLabels.length >= MAX_RELATED) break;
              const nodeLabel = node.id;
              if (!seenLabels.has(nodeLabel) && nodeLabel !== match.label) {
                seenLabels.add(nodeLabel);
                relatedLabels.push(nodeLabel);
              }
            }
          } catch {
            // Skip if graph fetch fails for this match
          }
          if (relatedLabels.length >= MAX_RELATED) break;
        }

        // Enrich related labels (most will already be cached from getGraph above)
        if (relatedLabels.length > 0) {
          const relatedEnriched = await fetchEnrichedResults(relatedLabels);
          relatedEnriched.forEach(r => r.related = true);
          // Sort: people → events → locations → organizations → concepts → artifacts → content → images → other
          relatedEnriched.sort((a, b) => {
            const aPri = entityTypeSortPriority(a.entityType);
            const bPri = entityTypeSortPriority(b.entityType);
            if (aPri !== bPri) return aPri - bPri;
            return a.label.localeCompare(b.label);
          });
          if (query.trim() === currentQuery) {
            results = [...results, ...relatedEnriched];
            allLabels = [...allLabels, ...relatedLabels];
          }
        }
      } finally {
        isLoading = false;
      }
    }, 300);
  });

  // IntersectionObserver for infinite scroll
  $effect(() => {
    const sentinel = sentinelRef;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting && hasMore && !isLoadingMore && !isLoading) {
            fetchNextPage();
          }
        }
      },
      { rootMargin: '100px' }
    );
    observer.observe(sentinel);

    return () => observer.disconnect();
  });

  $effect(() => {
    const imageResults = results.filter(
      (r) => r.entityType.toLowerCase() === 'image' && r.filePath
    );
    if (imageResults.length === 0) return;

    const currentResults = results;
    let cancelled = false;

    const updates = new Map<string, string>();
    Promise.all(
      imageResults.map(async (r) => {
        if (cancelled) return;
        const url = await lightragClient.resolveImageContentUrl(r.filePath!);
        if (url) updates.set(r.label, url);
      })
    ).then(() => {
      if (cancelled) return;
      // Only apply if results haven't changed since we started
      const stillRelevant = currentResults.every(
        (cr) => results.find((r) => r.label === cr.label)
      );
      if (stillRelevant && updates.size > 0) {
        resolvedImageUrls = new Map([...resolvedImageUrls, ...updates]);
      }
    });

    return () => { cancelled = true; };
  });

  function handleSelect(label: string) {
    onselectLabel(label);
    results = [];
    allLabels = [];
    hasMore = false;
    query = '';
    isOpen = false;
  }

  function handleFocus() {
    isOpen = true;
  }
</script>

<div class="absolute top-4 left-1/2 -translate-x-1/2 z-20 w-96 max-w-[calc(100vw-2rem)]">
  <div class="relative animate-fade-in-up">
    <div class="flex items-center bg-cyber-surface/80 backdrop-blur-md rounded-xl border border-cyber-border focus-within:border-cyber-cyan/50 focus-within:glow-cyan transition-all">
      <div class="pl-3 text-cyber-text-dim">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      </div>
      <input
        bind:this={inputRef}
        bind:value={query}
        onfocus={handleFocus}
        onblur={() => setTimeout(() => (isOpen = false), 200)}
        placeholder="Search entities..."
        class="flex-1 bg-transparent text-sm text-cyber-text placeholder:text-cyber-text-dim px-2 py-2.5 outline-none"
      />
      {#if isLoading}
        <div class="pr-3">
          <div class="w-3.5 h-3.5 border-2 border-cyber-cyan border-t-transparent rounded-full animate-spin"></div>
        </div>
      {/if}
    </div>

    {#if isOpen && (results.length > 0 || (isLoading && allLabels.length === 0))}
      <div class="absolute top-full left-0 right-0 mt-1 bg-cyber-surface/95 backdrop-blur-md border border-cyber-border rounded-xl overflow-hidden max-h-80 overflow-y-auto">
        {#each results as result, i (result.label)}
          {#if result.related && (i === 0 || !results[i-1].related)}
            <div class="gs-section px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider border-t border-cyber-border/30 {i > 0 ? 'mt-1' : ''}" style="color: #00d4ff;">
              Related entities
            </div>
          {/if}
          {#if result.related && i > 0 && results[i-1].related && entityTypeGroupLabel(result.entityType) !== entityTypeGroupLabel(results[i-1].entityType)}
            <div class="gs-subsection px-3 py-1 text-[10px] uppercase tracking-wider text-cyber-text-dim border-t border-cyber-border/20">
              {entityTypeGroupLabel(result.entityType)}
            </div>
          {/if}
          {@const colors = getTypeColor(result.entityType)}
          <button
            onclick={() => handleSelect(result.label)}
            class="gs-card w-full flex items-center gap-2.5 px-3 py-2 hover:bg-cyber-cyan/10 transition-colors text-left {result.related ? 'opacity-80' : ''}"
          >
            {#if result.entityType.toLowerCase() === 'person'}
              <div class="shrink-0 w-9 h-9 rounded-full overflow-hidden border border-cyber-border bg-cyber-surface-2 flex items-center justify-center relative">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-cyber-text-dim"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                <img
                  src={lightragClient.personPhotoUrl(result.label)}
                  alt={result.label}
                  class="absolute inset-0 w-full h-full object-cover"
                  onerror={(e) => { const img = e.currentTarget as HTMLImageElement | null; if (img) img.style.display = 'none'; }}
                />
              </div>
            {:else if result.entityType.toLowerCase() === 'image'}
              <!-- Image: inline preview with fallback icon -->
              <div class="shrink-0 w-[72px] h-[72px] rounded-lg overflow-hidden border border-cyber-border bg-cyber-surface-2 flex items-center justify-center relative">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-cyber-purple"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                {#if resolvedImageUrls.has(result.label)}
                  <img
                    src={resolvedImageUrls.get(result.label)}
                    alt={result.label}
                    class="absolute inset-0 w-full h-full object-cover"
                  />
                {/if}
              </div>
            {:else}
              <!-- Other types: colored dot -->
              <div class="shrink-0 w-2.5 h-2.5 rounded-full" style="background: {colors.text}"></div>
            {/if}

            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5">
                <span class="text-sm text-cyber-text truncate font-medium">{result.label}</span>
                <span
                  class="gs-badge shrink-0 inline-block px-1.5 py-px text-[10px] uppercase tracking-wider rounded-full border font-medium leading-none"
                  style="color: {colors.text}; border-color: {colors.border}; background: {colors.bg};"
                >
                  {getTypeBadgeLabel(result.entityType)}
                </span>
                {#if result.related}
                  <span class="text-[10px] text-cyber-text-dim ml-0.5">related</span>
                {/if}
              </div>
              {#if result.description}
                <p class="text-xs text-cyber-text-dim truncate mt-0.5">{truncate(result.description, 80)}</p>
              {/if}
            </div>
          </button>
        {/each}
        <!-- Sentinel for infinite scroll -->
        {#if hasMore}
          <div bind:this={sentinelRef} class="flex items-center justify-center py-2">
            {#if isLoadingMore}
              <div class="w-4 h-4 border-2 border-cyber-cyan border-t-transparent rounded-full animate-spin"></div>
            {:else}
              <span class="text-xs text-cyber-text-dim">Scroll for more…</span>
            {/if}
          </div>
        {/if}
      </div>
    {:else if isOpen && query.trim() && !isLoading && results.length === 0}
      <div class="absolute top-full left-0 right-0 mt-1 bg-cyber-surface/95 backdrop-blur-md border border-cyber-border rounded-xl p-4 text-center">
        <p class="text-sm text-cyber-text-dim">No results found</p>
      </div>
    {/if}
  </div>
</div>

<style>
  @media (max-width: 768px) {
    .gs-card {
      padding: 0.75rem 0.875rem;
      min-height: 44px;
    }
    .gs-badge {
      font-size: 11px;
      padding: 0.125rem 0.375rem;
    }
    .gs-section {
      font-size: 12px;
      padding: 0.5rem 0.875rem;
    }
    .gs-subsection {
      font-size: 11px;
      padding: 0.375rem 0.875rem;
    }
  }
</style>