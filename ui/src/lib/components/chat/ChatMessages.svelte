<script lang="ts">
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import type { ChatMessage } from '$lib/constants';
  import MCPToolCallCard from './MCPToolCallCard.svelte';
  import ImageGallery from '$lib/components/ui/ImageGallery.svelte';

  let { messages = [] }: { messages: ChatMessage[] } = $props();

  let containerEl: HTMLDivElement | undefined = $state();
  let shouldAutoScroll = $state(true);
  let expandedPromptIds = $state<Set<string>>(new Set());

  function togglePrompt(id: string) {
    expandedPromptIds = new Set(
      expandedPromptIds.has(id)
        ? [...expandedPromptIds].filter((x) => x !== id)
        : [...expandedPromptIds, id]
    );
  }

  /** Split the assembled LightRAG prompt into its structural sections
   *  (---Role---, ---Goal---, ---Instructions---, Context, ---User Query---)
   *  so the UI can render each block separately and the user can see exactly
   *  what was sent to the LLM. Returns sections in the order they appear. */
  function parsePromptSections(prompt: string): { title: string; body: string }[] {
    const sections: { title: string; body: string }[] = [];
    const markerRegex = /^---(.+?)---\s*$/gm;
    const marks: { name: string; start: number }[] = [];
    let m: RegExpExecArray | null;
    while ((m = markerRegex.exec(prompt)) !== null) {
      marks.push({ name: m[1], start: m.index });
    }
    for (let i = 0; i < marks.length; i++) {
      const headerEnd = marks[i].start + `---${marks[i].name}---`.length;
      const bodyStart = headerEnd + 1;
      const bodyEnd = i + 1 < marks.length ? marks[i + 1].start : prompt.length;
      sections.push({
        title: marks[i].name,
        body: prompt.slice(bodyStart, bodyEnd).trim(),
      });
    }
    if (sections.length === 0) sections.push({ title: 'Prompt', body: prompt.trim() });
    return sections;
  }

  marked.use({
    renderer: {
      code({ text, lang }: { text: string; lang?: string }) {
        const language = lang || 'plaintext';
        return `<pre class="my-2 overflow-x-auto rounded-md bg-cyber-bg p-3 font-mono text-xs text-cyber-text"><code class="language-${language}">${text}</code></pre>`;
      },
      paragraph({ text }: { text: string }) {
        return `<p class="mb-2 last:mb-0">${text}</p>`;
      },
      link({ href, text }: { href: string; text: string }) {
        return `<a href="${href}" target="_blank" rel="noopener noreferrer" class="text-cyber-cyan underline decoration-cyber-cyan/30 transition-colors hover:text-cyber-cyan/80">${text}</a>`;
      },
    },
  });

  function renderMarkdown(content: string): string {
    const raw = marked.parse(content) as string;
    return DOMPurify.sanitize(raw);
  }

  $effect(() => {
    if (messages.length && shouldAutoScroll && containerEl) {
      requestAnimationFrame(() => {
        containerEl?.scrollTo({ top: containerEl.scrollHeight, behavior: 'smooth' });
      });
    }
  });

  function handleScroll() {
    if (!containerEl) return;
    const { scrollTop, scrollHeight, clientHeight } = containerEl;
    shouldAutoScroll = scrollHeight - scrollTop - clientHeight < 60;
  }
</script>

<div bind:this={containerEl} onscroll={handleScroll} class="flex-1 overflow-y-auto">
  <div class="mx-auto max-w-3xl space-y-4 px-4 py-6">
    {#each messages as msg, i (msg.id)}
      <div
        class="animate-fade-in-up {msg.role === 'user'
          ? 'ml-auto max-w-[80%]'
          : msg.role === 'system'
            ? 'mx-auto max-w-[90%]'
            : 'mr-auto max-w-[85%]'}"
      >
        {#if msg.role === 'user'}
          <div class="space-y-1">
            {#if msg.imageUrls && msg.imageUrls.length > 0}
              <ImageGallery images={msg.imageUrls} alt="Uploaded image" />
            {/if}
            <div class="rounded-2xl rounded-br-sm border border-cyber-cyan/20 bg-cyber-cyan/5 px-4 py-3 text-sm text-cyber-text">
              {msg.content}
            </div>
          </div>
          <div class="mt-1 text-right text-xs text-cyber-text-dim">
            {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
        {:else if msg.role === 'system'}
          <div class="text-center text-xs italic text-cyber-text-dim">
            {msg.content}
          </div>
        {:else}
          <div class="rounded-2xl rounded-bl-sm border border-cyber-border bg-cyber-surface px-4 py-3">
            <div class="prose-invert text-sm leading-relaxed [&_a]:text-cyber-cyan [&_a]:underline [&_code]:font-mono [&_code]:rounded [&_code]:bg-cyber-bg [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:text-cyber-cyan [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-cyber-text [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-cyber-text [&_li]:text-cyber-text [&_ol]:list-decimal [&_ol]:pl-4 [&_p]:text-cyber-text [&_pre]:bg-cyber-bg [&_pre]:rounded-md [&_pre]:p-3 [&_pre]:text-xs [&_strong]:text-cyber-cyan [&_ul]:list-disc [&_ul]:pl-4">
              {@html renderMarkdown(msg.content)}
              {#if msg.isStreaming}
                <span class="ml-0.5 inline-block h-4 w-1.5 animate-pulse-glow bg-cyber-cyan align-text-bottom"></span>
              {/if}
            </div>

            {#if msg.kgPrompt}
              <div class="mt-3 border-t border-cyber-border pt-2">
                <button
                  type="button"
                  onclick={() => togglePrompt(msg.id)}
                  class="flex w-full items-center gap-1.5 text-xs font-medium text-cyber-text-dim transition-colors hover:text-cyber-cyan"
                >
                  <svg
                    class="h-3 w-3 shrink-0 transition-transform duration-200 {expandedPromptIds.has(msg.id) ? 'rotate-90' : ''}"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                  </svg>
                  <span>Prompt sent to LLM</span>
                </button>
                {#if expandedPromptIds.has(msg.id)}
                  <div class="mt-2 space-y-3">
                    {#each parsePromptSections(msg.kgPrompt) as section}
                      <div class="rounded-md border border-cyber-border/60 bg-cyber-bg/60 px-3 py-2">
                        <div class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-cyber-cyan/80">{section.title}</div>
                        <pre class="max-h-80 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-cyber-text-dim">{section.body}</pre>
                      </div>
                    {/each}
                    {#if msg.kgPromptHistory && msg.kgPromptHistory.length > 0}
                      <div class="rounded-md border border-cyber-border/60 bg-cyber-bg/60 px-3 py-2">
                        <div class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-cyber-cyan/80">Conversation history ({msg.kgPromptHistory.length})</div>
                        <div class="space-y-1.5">
                          {#each msg.kgPromptHistory as hm, idx}
                            <div class="text-[11px] leading-relaxed">
                              <span class="font-mono uppercase text-cyber-text-dim/70">{hm.role}:</span>
                              <span class="text-cyber-text-dim">{hm.content.slice(0, 200)}{hm.content.length > 200 ? '…' : ''}</span>
                            </div>
                          {/each}
                        </div>
                      </div>
                    {/if}
                  </div>
                {/if}
              </div>
            {/if}

            {#if msg.mcpToolCalls && msg.mcpToolCalls.length > 0}
              <div class="mt-3 border-t border-cyber-border pt-2">
                {#each msg.mcpToolCalls as toolCall (toolCall.timestamp)}
                  <MCPToolCallCard {toolCall} />
                {/each}
              </div>
            {/if}
          </div>
          <div class="mt-1 text-xs text-cyber-text-dim">
            {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
        {/if}
      </div>
    {:else}
      <div class="flex h-64 flex-col items-center justify-center text-center">
        <div class="mb-3 text-4xl text-cyber-cyan text-glow-cyan">⟐</div>
        <div class="text-sm text-cyber-text-dim">Start a conversation</div>
        <div class="mt-1 text-xs text-cyber-text-dim/60">Type a message below to begin</div>
      </div>
    {/each}
  </div>
</div>