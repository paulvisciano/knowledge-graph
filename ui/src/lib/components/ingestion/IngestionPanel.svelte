<script lang="ts">
  import { lightragClient } from '$lib/services/lightrag-client';
  import type { DocStatus, PipelineStatus } from '$lib/constants';
  import StatusBadge from './StatusBadge.svelte';
  import ScanButton from './ScanButton.svelte';

  let docs: DocStatus[] = $state([]);
  let pipeline: PipelineStatus | null = $state(null);
  let totalDocs = $state(0);
  let page = $state(1);
  let pageSize = 20;
  let searchQuery = $state('');
  let dragging = $state(false);
  let uploading = $state(false);
  let uploadProgress = $state(0);
  let error = $state('');
  let historyExpanded = $state(false);
  let reprocessingIds: string[] = $state([]);
  let fileInput: HTMLInputElement | undefined = $state();

  let filteredDocs = $derived(
    (searchQuery
      ? docs.filter(
          (d) =>
            d.file_path.toLowerCase().includes(searchQuery.toLowerCase()) ||
            d.status.toLowerCase().includes(searchQuery.toLowerCase())
        )
      : docs
    ).sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
  );

  let totalPages = $derived(Math.max(1, Math.ceil(totalDocs / pageSize)));

  async function loadDocs() {
    try {
      const res = await lightragClient.getDocuments(page, pageSize);
      docs = res.documents;
      totalDocs = res.total;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load documents';
    }
  }

  async function loadPipeline() {
    try {
      pipeline = await lightragClient.getPipelineStatus();
    } catch {
      pipeline = null;
    }
  }

  async function refresh() {
    await Promise.all([loadDocs(), loadPipeline()]);
  }

  $effect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  });

  async function handleFiles(fileList: FileList) {
    const files = Array.from(fileList);
    if (files.length === 0) return;

    uploading = true;
    uploadProgress = 0;
    error = '';

    const total = files.length;
    let done = 0;

    try {
      for (const file of files) {
        await lightragClient.uploadDocuments([file]);
        done++;
        uploadProgress = Math.round((done / total) * 100);
      }
      await loadDocs();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Upload failed';
    } finally {
      uploading = false;
      uploadProgress = 0;
    }
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    if (e.dataTransfer?.files) {
      handleFiles(e.dataTransfer.files);
    }
  }

  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    dragging = true;
  }

  function handleDragLeave() {
    dragging = false;
  }

  async function handleScan() {
    await lightragClient.scanDocuments();
    await loadPipeline();
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

  async function handleDelete(id: string) {
    try {
      await lightragClient.deleteDocument(id);
      await loadDocs();
    } catch {
      void 0;
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
</script>

<div class="flex h-full flex-col gap-3 overflow-hidden">
  {#if error}
    <div class="animate-fade-in-up rounded-lg border border-cyber-red/40 bg-cyber-red/10 px-3 py-2 text-sm text-cyber-red">
      {error}
      <button onclick={() => (error = '')} class="ml-2 opacity-60 hover:opacity-100">✕</button>
    </div>
  {/if}


  <div
    class="group relative shrink-0 rounded-lg border-2 border-dashed transition-all duration-300
      {dragging ? 'border-cyber-cyan bg-cyber-cyan/5 glow-cyan' : 'border-cyber-border hover:border-cyber-cyan/60 hover:bg-cyber-surface-2/30'}"
    ondrop={handleDrop}
    ondragover={handleDragOver}
    ondragleave={handleDragLeave}
    role="button"
    tabindex="0"
  >
    <input
      type="file"
      multiple
      accept=".pdf,.txt,.md,.docx,.json,.csv"
      class="hidden"
      bind:this={fileInput}
      onchange={(e) => {
        const input = e.currentTarget as HTMLInputElement;
        if (input.files) handleFiles(input.files);
        input.value = '';
      }}
      id="file-upload-input"
    />
    <label for="file-upload-input" class="flex cursor-pointer flex-col items-center gap-2 px-6 py-6">
      {#if uploading}
        <div class="flex flex-col items-center gap-2">
          <svg class="h-8 w-8 animate-pulse-glow text-cyber-cyan" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M12 16V4m0 0L8 8m4-4l4 4" stroke-linecap="round" stroke-linejoin="round" />
            <path d="M20 16.7A4.5 4.5 0 0017.5 8h-1.1A7 7 0 104 14.9" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span class="text-sm text-cyber-cyan">Uploading… {uploadProgress}%</span>
          <div class="h-1 w-48 overflow-hidden rounded-full bg-cyber-surface-2">
            <div
              class="h-full rounded-full bg-cyber-cyan transition-all duration-300"
              style="width: {uploadProgress}%"
            ></div>
          </div>
        </div>
      {:else}
        <svg class="h-8 w-8 text-cyber-text-dim transition-colors group-hover:text-cyber-cyan" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M12 16V4m0 0L8 8m4-4l4 4" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M20 16.7A4.5 4.5 0 0017.5 8h-1.1A7 7 0 104 14.9" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span class="text-sm text-cyber-text-dim group-hover:text-cyber-text">
          Drop files here or <span class="text-cyber-cyan underline">click to browse</span>
        </span>
        <div class="flex gap-1.5">
          {#each ['PDF', 'TXT', 'MD', 'DOCX'] as ext}
            <span class="rounded bg-cyber-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-cyber-text-dim">{ext}</span>
          {/each}
        </div>
      {/if}
    </label>
  </div>


  <div class="shrink-0 rounded-lg border border-cyber-border bg-cyber-surface px-3 py-2.5">
    {#if pipeline?.busy}
      <div class="flex items-center gap-2">
        <span class="inline-block h-2.5 w-2.5 rounded-full bg-cyber-orange animate-pulse-glow"></span>
        <span class="text-sm font-medium text-cyber-orange">Processing</span>
        <span class="ml-auto font-mono text-xs text-cyber-text-dim">
          {pipeline.job_name} · {pipeline.cur_batch}/{pipeline.batchs}
        </span>
        <button
          onclick={async () => {
            try {
              await lightragClient.cancelPipeline();
              await loadPipeline();
            } catch { void 0; }
          }}
          class="rounded border border-cyber-red/40 px-2 py-0.5 text-xs text-cyber-red transition-colors hover:bg-cyber-red/10"
        >
          Cancel
        </button>
      </div>
      {#if pipeline.latest_message}
        <div class="mt-1.5 overflow-hidden">
          <div class="truncate font-mono text-xs text-cyber-text-dim">{pipeline.latest_message}</div>
        </div>
      {/if}
      {#if pipeline.history_messages && pipeline.history_messages.length > 0}
        <button
          onclick={() => (historyExpanded = !historyExpanded)}
          class="mt-1.5 text-[10px] text-cyber-text-dim/60 transition-colors hover:text-cyber-text-dim"
        >
          {historyExpanded ? '▾ Hide' : '▸ Show'} history ({pipeline.history_messages.length})
        </button>
        {#if historyExpanded}
          <div class="mt-1 max-h-28 overflow-y-auto rounded border border-cyber-border/50 bg-cyber-bg px-2 py-1 font-mono text-[10px] text-cyber-text-dim">
            {#each pipeline.history_messages as msg, i}
              <div class="py-0.5 {i > 0 ? 'border-t border-cyber-border/30' : ''}">{msg}</div>
            {/each}
          </div>
        {/if}
      {/if}
    {:else}
      <div class="flex items-center gap-2">
        <span class="inline-block h-2.5 w-2.5 rounded-full bg-cyber-green"></span>
        <span class="text-sm font-medium text-cyber-green">Idle</span>
        <span class="ml-auto">
          <ScanButton busy={pipeline?.busy ?? false} onScan={handleScan} />
        </span>
      </div>
    {/if}
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
          {docs.length === 0 ? 'No documents yet. Drop files above to begin.' : 'No matching documents.'}
        </div>
      {:else}
        <table class="w-full text-left text-xs">
          <thead class="sticky top-0 bg-cyber-surface text-cyber-text-dim">
            <tr class="border-b border-cyber-border">
              <th class="px-3 py-1.5 font-medium">File</th>
              <th class="px-3 py-1.5 font-medium">Type</th>
              <th class="px-3 py-1.5 font-medium">Status</th>
              <th class="px-3 py-1.5 font-medium text-right">Size</th>
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
                <td class="px-3 py-1.5">
                  <span class="rounded px-1.5 py-0.5 font-mono text-[10px]
                    {doc.file_type === 'image' ? 'bg-cyber-purple/20 text-cyber-purple' : 'bg-cyber-cyan/10 text-cyber-cyan'}">
                    {doc.file_type === 'image' ? 'IMG' : 'TXT'}
                  </span>
                </td>
                <td class="px-3 py-1.5">
                  <StatusBadge status={doc.status} />
                </td>
                <td class="px-3 py-1.5 text-right text-cyber-text-dim">
                  {formatSize(doc.content_length)}
                </td>
                <td class="px-3 py-1.5 text-cyber-text-dim">
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
                      onclick={() => handleDelete(doc.id)}
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
          Page {page} of {totalPages}
        </span>
        <div class="flex gap-1">
          <button
            onclick={() => { page = Math.max(1, page - 1); loadDocs(); }}
            disabled={page <= 1}
            class="rounded border border-cyber-border px-2 py-0.5 text-xs text-cyber-text-dim transition-colors hover:border-cyber-cyan/40 hover:text-cyber-text disabled:opacity-30 disabled:hover:border-cyber-border"
          >
            ←
          </button>
          <button
            onclick={() => { page = Math.min(totalPages, page + 1); loadDocs(); }}
            disabled={page >= totalPages}
            class="rounded border border-cyber-border px-2 py-0.5 text-xs text-cyber-text-dim transition-colors hover:border-cyber-cyan/40 hover:text-cyber-text disabled:opacity-30 disabled:hover:border-cyber-border"
          >
            →
          </button>
        </div>
      </div>
    {/if}
  </div>
</div>