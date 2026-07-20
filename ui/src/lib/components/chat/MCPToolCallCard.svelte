<script lang="ts">
  import type { MCPToolCall } from '$lib/constants';

  let { toolCall }: { toolCall: MCPToolCall } = $props();

  let expanded = $state(false);
  let argsExpanded = $state(false);
  let resultExpanded = $state(false);

  let status = $derived(
    toolCall.result === undefined
      ? ('running' as const)
      : toolCall.isError
        ? ('error' as const)
        : ('completed' as const)
  );

  let formattedArgs = $derived(JSON.stringify(toolCall.arguments, null, 2));
  let formattedResult = $derived(
    toolCall.result ? tryFormatJson(toolCall.result) : ''
  );

  let insertedText = $derived(
    typeof toolCall.arguments?.text === 'string' ? (toolCall.arguments.text as string) : ''
  );
  let fileSource = $derived(
    typeof toolCall.arguments?.file_source === 'string'
      ? (toolCall.arguments.file_source as string)
      : ''
  );
  let charCount = $derived(insertedText.length);
  let wordCount = $derived(
    insertedText.trim().length > 0
      ? insertedText.trim().split(/\s+/).length
      : 0
  );
  const PREVIEW_MAX_CHARS = 280;
  const PREVIEW_MAX_LINES = 3;
  let textPreview = $derived.by(() => {
    const text = insertedText;
    if (!text) return '';
    const lines = text.split('\n');
    const byLines = lines.slice(0, PREVIEW_MAX_LINES).join('\n');
    const byChars = text.slice(0, PREVIEW_MAX_CHARS);
    return byLines.length < byChars.length ? byLines : byChars;
  });
  let textTruncated = $derived(
    insertedText.length > PREVIEW_MAX_CHARS ||
      insertedText.split('\n').length > PREVIEW_MAX_LINES
  );
  let fileSourceChip = $derived(fileSource.trim() || 'chat-note');

  function tryFormatJson(str: string): string {
    try {
      return JSON.stringify(JSON.parse(str), null, 2);
    } catch {
      return str;
    }
  }

  function toggleExpand() {
    expanded = !expanded;
  }
</script>

<div
  class="mt-2 rounded-lg border transition-all duration-300 {status === 'running'
    ? 'border-cyber-orange/40 shadow-[0_0_12px_rgba(255,140,0,0.15)]'
    : status === 'error'
      ? 'border-cyber-red/30'
      : 'border-cyber-border'}"
>
  {#if toolCall.toolName === 'insert_text'}
    <button
      onclick={toggleExpand}
      class="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-cyber-surface-2/50"
    >
      <div
        class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs
          {expanded ? 'bg-cyber-purple/20 text-cyber-purple' : 'bg-cyber-surface-2 text-cyber-text-dim'}"
      >
        {expanded ? '▾' : '▸'}
      </div>

      <svg
        class="h-4 w-4 shrink-0 text-cyber-purple"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M10 3v9" />
        <path d="M6.5 9 10 12.5 13.5 9" />
        <path d="M4 14.5h12" />
        <path d="M4 17h12" />
      </svg>

      <span class="font-mono text-sm text-cyber-purple">{toolCall.toolName}</span>
      <span class="text-xs text-cyber-text-dim">Inserted into knowledge graph</span>

      <span class="ml-auto text-xs text-cyber-text-dim">
        {new Date(toolCall.timestamp).toLocaleTimeString()}
      </span>

      {#if status === 'running'}
        <span class="animate-pulse-glow ml-1 inline-block h-2 w-2 rounded-full bg-cyber-orange"></span>
      {:else if status === 'completed'}
        <svg class="ml-1 h-4 w-4 text-cyber-green" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
        </svg>
      {:else}
        <svg class="ml-1 h-4 w-4 text-cyber-red" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L9.586 10 5.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
        </svg>
      {/if}
    </button>

    <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1 px-3 pb-2 text-xs text-cyber-text-dim">
      <span
        class="inline-flex items-center rounded-full bg-cyber-cyan/10 px-2 py-0.5 font-mono text-[0.7rem] text-cyber-cyan"
      >
        {fileSourceChip}
      </span>
      <span aria-hidden="true">·</span>
      <span class="font-mono">{charCount} chars</span>
      <span aria-hidden="true">·</span>
      <span class="font-mono">~{wordCount} words</span>
    </div>

    {#if insertedText}
      <div class="relative px-3 pb-2">
        <pre
          class="max-h-32 overflow-hidden whitespace-pre-wrap break-words rounded bg-cyber-bg/60 p-2 font-mono text-xs text-cyber-text"
        >{textPreview}</pre>
        {#if textTruncated}
          <div
            class="pointer-events-none absolute inset-x-3 bottom-2 h-6 rounded-b bg-gradient-to-b from-transparent to-cyber-bg/90"
          ></div>
        {/if}
      </div>
    {/if}

    {#if expanded}
      <div class="border-t border-cyber-border px-3 py-2">
        <div class="mb-1 font-mono uppercase tracking-wider text-xs text-cyber-text-dim">
          Full text
        </div>
        <pre
          class="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-cyber-bg/60 p-2 font-mono text-xs text-cyber-text"
        >{insertedText}</pre>

        {#if toolCall.result !== undefined}
          <button
            onclick={() => (resultExpanded = !resultExpanded)}
            class="mt-2 mb-1 flex items-center gap-1 text-xs text-cyber-text-dim transition-colors hover:text-cyber-green"
          >
            <span>{resultExpanded ? '▾' : '▸'}</span>
            <span class="font-mono uppercase tracking-wider">Result</span>
          </button>
          {#if resultExpanded}
            <pre
              class="max-h-64 overflow-auto rounded bg-cyber-bg/60 p-2 font-mono text-xs {status === 'error' ? 'text-cyber-red' : 'text-cyber-green'}"
            >{formattedResult}</pre>
          {/if}
        {/if}
      </div>
    {/if}
  {:else}
    <button
      onclick={toggleExpand}
      class="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-cyber-surface-2/50"
    >
      <div
        class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs
          {expanded ? 'bg-cyber-purple/20 text-cyber-purple' : 'bg-cyber-surface-2 text-cyber-text-dim'}"
      >
        {expanded ? '▾' : '▸'}
      </div>

      <span class="font-mono text-sm text-cyber-purple">{toolCall.toolName}</span>

      <span class="ml-auto text-xs text-cyber-text-dim">
        {new Date(toolCall.timestamp).toLocaleTimeString()}
      </span>

      {#if status === 'running'}
        <span class="animate-pulse-glow ml-1 inline-block h-2 w-2 rounded-full bg-cyber-orange"></span>
      {:else if status === 'completed'}
        <svg class="ml-1 h-4 w-4 text-cyber-green" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
        </svg>
      {:else}
        <svg class="ml-1 h-4 w-4 text-cyber-red" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L9.586 10 5.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
        </svg>
      {/if}
    </button>

    {#if expanded}
      <div class="border-t border-cyber-border px-3 py-2">
        <button
          onclick={() => (argsExpanded = !argsExpanded)}
          class="mb-1 flex items-center gap-1 text-xs text-cyber-text-dim transition-colors hover:text-cyber-cyan"
        >
          <span>{argsExpanded ? '▾' : '▸'}</span>
          <span class="font-mono uppercase tracking-wider">Arguments</span>
        </button>
        {#if argsExpanded}
          <pre
            class="max-h-48 overflow-auto rounded bg-cyber-bg/60 p-2 font-mono text-xs text-cyber-text"
          >{formattedArgs}</pre>
        {/if}

        {#if toolCall.result !== undefined}
          <button
            onclick={() => (resultExpanded = !resultExpanded)}
            class="mt-2 mb-1 flex items-center gap-1 text-xs text-cyber-text-dim transition-colors hover:text-cyber-green"
          >
            <span>{resultExpanded ? '▾' : '▸'}</span>
            <span class="font-mono uppercase tracking-wider">Result</span>
          </button>
          {#if resultExpanded}
            <pre
              class="max-h-64 overflow-auto rounded bg-cyber-bg/60 p-2 font-mono text-xs {status === 'error' ? 'text-cyber-red' : 'text-cyber-green'}"
            >{formattedResult}</pre>
          {/if}
        {/if}
      </div>
    {/if}
  {/if}
</div>