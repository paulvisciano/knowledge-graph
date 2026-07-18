<script lang="ts">
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
  import { graphStore } from '$lib/stores/graph.svelte';
  import type { DocStatus } from '$lib/constants';
  import StatusBadge from './StatusBadge.svelte';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  marked.use({
    renderer: {
      code({ text, lang }: { text: string; lang?: string }) {
        const language = lang || 'plaintext';
        return `<pre class="my-2 overflow-x-auto rounded-md bg-cyber-bg p-3 font-mono text-xs text-cyber-text"><code class="language-${language}">${text}</code></pre>`;
      },
      paragraph({ text }: { text: string }) {
        return `<p class="mb-2 last:mb-0">${text}</p>`;
      },
    },
  });

  function renderMarkdown(text: string): string {
    const raw = marked.parse(text, { async: false }) as string;
    return DOMPurify.sanitize(raw);
  }

  let docs: DocStatus[] = $state([]);
  let totalDocs = $state(0);
  let page = $state(1);
  let pageSize = 20;
  let totalPages = $state(1);
  let hasNext = $state(false);
  let hasPrev = $state(false);
  let searchQuery = $state('');
  let error = $state('');
  let reprocessingIds: string[] = $state([]);
  let summaryDoc: DocStatus | null = $state(null);
  let summaryFullContent: string | null = $state(null);
  let summaryLoading: boolean = $state(false);
  let summaryError: string = $state('');
  let loading = $state(false);

  let filteredDocs = $derived(
    searchQuery
      ? docs.filter(
          (d) =>
            d.file_path.toLowerCase().includes(searchQuery.toLowerCase()) ||
            d.status.toLowerCase().includes(searchQuery.toLowerCase())
        )
      : docs
  );

  async function loadDocs() {
    loading = true;
    try {
      const res = await lightragClient.getDocumentsPaginated(page, pageSize);
      docs = res.documents;
      totalDocs = res.total;
      totalPages = res.total_pages;
      hasNext = res.has_next;
      hasPrev = res.has_prev;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load documents';
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    loadDocs();
    const interval = setInterval(loadDocs, 5000);
    return () => clearInterval(interval);
  });

  $effect(() => {
    if (!summaryDoc) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeSummary();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  async function openSummary(doc: DocStatus) {
    summaryDoc = doc;
    summaryFullContent = null;
    summaryLoading = true;
    summaryError = '';
    try {
      const res = await lightragClient.getDocumentFullContent(doc.id);
      summaryFullContent = res.content;
    } catch (e) {
      summaryError = e instanceof Error ? e.message : 'Failed to load full content';
    } finally {
      summaryLoading = false;
    }
  }

  function closeSummary() {
    summaryDoc = null;
    summaryFullContent = null;
    summaryLoading = false;
    summaryError = '';
  }

  async function handleReprocess(id: string) {
    reprocessingIds = [...reprocessingIds, id];
    try {
      await lightragClient.reprocessDocument(id);
      await loadDocs();
    } catch {
      void 0;
    } finally {
      reprocessingIds = reprocessingIds.filter((i) => i !== id);
    }
  }

  async function handleDelete(id: string, filePath?: string) {
    try {
      await lightragClient.deleteDocument(id);
      if (filePath) {
        try {
          await kgApiClient.deletePhotoEntities(filePath);
        } catch {
          // Best-effort cleanup — entity deletion may fail if already gone
        }
      }
      await loadDocs();
      graphStore.refresh();
    } catch {
      void 0;
    }
  }

  function relativeTime(dateStr: string): string {
    const d = new Date(dateStr);
    const diff = Date.now() - d.getTime();
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  }

  function fileName(path: string): string {
    return path.split('/').pop() ?? path;
  }

  function goToPage(p: number) {
    if (p < 1 || p > totalPages || p === page) return;
    page = p;
    loadDocs();
  }
</script>

<div class="flex h-full flex-col gap-3 overflow-hidden">
  {#if error}
    <div class="animate-fade-in-up rounded-lg border border-cyber-red/40 bg-cyber-red/10 px-3 py-2 text-sm text-cyber-red">
      {error}
      <button onclick={() => (error = '')} class="ml-2 opacity-60 hover:opacity-100">✕</button>
    </div>
  {/if}

  <div class="flex shrink-0 items-center gap-4 rounded-lg border border-cyber-border bg-cyber-surface px-4 py-2.5">
    <div class="flex items-baseline gap-1.5">
      <span class="text-lg font-semibold tabular-nums text-cyber-cyan">{totalDocs}</span>
      <span class="text-xs text-cyber-text-dim">Documents</span>
    </div>
    <div class="h-5 w-px bg-cyber-border"></div>
    <div class="flex items-baseline gap-1.5">
      <span class="text-lg font-semibold tabular-nums text-cyber-cyan">{graphStore.nodes.length}</span>
      <span class="text-xs text-cyber-text-dim">Nodes</span>
    </div>
  </div>

  <div class="flex min-h-0 flex-1 flex-col rounded-lg border border-cyber-border bg-cyber-surface">
    <div class="flex shrink-0 items-center gap-2 border-b border-cyber-border px-3 py-2">
      <h3 class="text-sm font-semibold text-cyber-text">Documents</h3>
      <span class="text-xs text-cyber-text-dim">{totalDocs}</span>
      <div class="ml-auto">
        <input
          type="text"
          placeholder="Filter…"
          bind:value={searchQuery}
          class="w-36 rounded border border-cyber-border bg-cyber-bg px-2 py-1 text-xs text-cyber-text placeholder:text-cyber-text-dim/50
            focus:border-cyber-cyan/60 focus:outline-none focus:ring-1 focus:ring-cyber-cyan/30"
        />
      </div>
    </div>

    <div class="flex-1 overflow-y-auto">
      {#if filteredDocs.length === 0}
        <div class="flex h-32 items-center justify-center text-sm text-cyber-text-dim">
          {loading ? 'Loading…' : docs.length === 0 ? 'No documents yet.' : 'No matching documents.'}
        </div>
      {:else}
        <table class="w-full text-left text-xs">
          <thead class="sticky top-0 bg-cyber-surface text-cyber-text-dim">
            <tr class="border-b border-cyber-border">
              <th class="px-3 py-1.5 font-medium">File</th>
              <th class="px-3 py-1.5 font-medium">Summary</th>
              <th class="px-3 py-1.5 font-medium whitespace-nowrap">Status</th>
              <th class="px-3 py-1.5 font-medium text-right whitespace-nowrap">Length</th>
              <th class="px-3 py-1.5 font-medium">Date</th>
              <th class="px-3 py-1.5 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {#each filteredDocs as doc (doc.id)}
              <tr class="border-b border-cyber-border/50 transition-colors hover:bg-cyber-surface-2/40 animate-fade-in-up">
                <td class="max-w-[200px] truncate px-3 py-1.5 text-cyber-text" title={doc.file_path}>
                  {fileName(doc.file_path)}
                </td>
                <td class="max-w-[320px] truncate px-3 py-1.5 text-cyber-text-dim" title={doc.content_summary}>
                  <button
                    type="button"
                    onclick={() => openSummary(doc)}
                    class="cursor-pointer text-left transition-colors hover:text-cyber-cyan"
                  >
                    {doc.content_summary}
                  </button>
                </td>
                <td class="px-3 py-1.5 whitespace-nowrap">
                  <StatusBadge status={doc.status} />
                </td>
                <td class="px-3 py-1.5 text-right text-cyber-text-dim whitespace-nowrap tabular-nums">
                  {doc.content_length.toLocaleString()}
                </td>
                <td class="px-3 py-1.5 text-cyber-text-dim whitespace-nowrap">
                  {relativeTime(doc.updated_at)}
                </td>
                <td class="px-3 py-1.5 text-right">
                  <div class="inline-flex gap-1">
                    <button
                      onclick={() => handleReprocess(doc.id)}
                      disabled={reprocessingIds.includes(doc.id)}
                      class="rounded p-1 text-cyber-text-dim transition-colors hover:bg-cyber-cyan/10 hover:text-cyber-cyan disabled:opacity-40"
                      title="Reprocess"
                    >
                      <svg class="h-3.5 w-3.5 {reprocessingIds.includes(doc.id) ? 'animate-pulse-glow' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 12a9 9 0 11-6.219-8.56" />
                        <path d="M21 3v6h-6" />
                      </svg>
                    </button>
                    <button
                      onclick={() => handleDelete(doc.id, doc.file_path)}
                      class="rounded p-1 text-cyber-text-dim transition-colors hover:bg-cyber-red/10 hover:text-cyber-red"
                      title="Delete"
                    >
                      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14" />
                      </svg>
                    </button>
                  </div>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>

    {#if totalPages > 1}
      <div class="flex shrink-0 items-center justify-between border-t border-cyber-border px-3 py-1.5">
        <span class="text-[10px] text-cyber-text-dim">
          Page {page} of {totalPages} · {totalDocs} docs
        </span>
        <div class="flex gap-1">
          <button
            onclick={() => goToPage(page - 1)}
            disabled={!hasPrev}
            class="rounded border border-cyber-border px-2 py-0.5 text-xs text-cyber-text-dim transition-colors hover:border-cyber-cyan/40 hover:text-cyber-text disabled:opacity-30 disabled:hover:border-cyber-border"
          >
            ←
          </button>
          <button
            onclick={() => goToPage(page + 1)}
            disabled={!hasNext}
            class="rounded border border-cyber-border px-2 py-0.5 text-xs text-cyber-text-dim transition-colors hover:border-cyber-cyan/40 hover:text-cyber-text disabled:opacity-30 disabled:hover:border-cyber-border"
          >
            →
          </button>
        </div>
      </div>
    {/if}
  </div>
</div>

{#if summaryDoc}
  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_actions -->
  <div
    class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 p-4 backdrop-blur-md"
    onclick={closeSummary}
    role="presentation"
  >
    <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_actions -->
    <div
      class="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-cyber-border bg-cyber-surface shadow-2xl"
      onclick={(e) => e.stopPropagation()}
      role="presentation"
    >
      <div class="flex shrink-0 items-center gap-2 border-b border-cyber-border px-4 py-3">
        <h3 class="truncate text-sm font-semibold text-cyber-text" title={summaryDoc.file_path}>
          {fileName(summaryDoc.file_path)}
        </h3>
        <span class="shrink-0">
          <StatusBadge status={summaryDoc.status} />
        </span>
        <button
          type="button"
          onclick={closeSummary}
          class="ml-auto rounded p-1 text-cyber-text-dim transition-colors hover:bg-cyber-surface-2 hover:text-cyber-text"
          title="Close (Esc)"
          aria-label="Close"
        >
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {#if summaryLoading}
          <div class="flex items-center gap-2 text-xs text-cyber-text-dim">
            <svg class="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 11-6.219-8.56" />
            </svg>
            <span>Loading full content…</span>
          </div>
        {:else if summaryError}
          <div class="rounded-md border border-cyber-red/40 bg-cyber-red/10 px-3 py-2 text-xs text-cyber-red">
            {summaryError}
          </div>
          <pre class="mt-2 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-cyber-text-dim">{summaryDoc.content_summary}</pre>
        {:else if summaryFullContent}
          <div class="prose-cyber text-xs leading-relaxed text-cyber-text/90">{@html renderMarkdown(summaryFullContent)}</div>
        {:else}
          <pre class="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-cyber-text">{summaryDoc.content_summary}</pre>
        {/if}
      </div>
    </div>
  </div>
{/if}