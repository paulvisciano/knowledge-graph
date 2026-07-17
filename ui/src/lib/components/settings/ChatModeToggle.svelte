<script lang="ts">
  import { chatMode } from '$lib/stores/ui';
  import type { ChatMode } from '$lib/constants';
  import Icon from '$lib/components/ui/Icon.svelte';

  let expanded = $state(false);

  function setMode(mode: ChatMode) {
    chatMode.set(mode);
  }
</script>

<div class="rounded-xl border border-cyber-border/40 bg-cyber-surface/85 backdrop-blur-xl overflow-hidden">
  <button
    onclick={() => (expanded = !expanded)}
    class="flex w-full items-center justify-between px-3 py-2.5 transition-colors hover:bg-cyber-surface-2/50"
  >
    <div class="flex items-center gap-2">
      <div class="flex h-6 w-6 items-center justify-center rounded-md bg-cyber-cyan/10">
        <Icon name={$chatMode === 'kg-direct' ? 'database' : 'cpu'} size={14} color="var(--color-cyber-cyan)" />
      </div>
      <div class="text-left">
        <div class="text-xs font-medium text-cyber-text">Chat Mode</div>
        <div class="text-[10px] text-cyber-text-dim">{$chatMode === 'kg-direct' ? 'Direct KG' : 'LLM + MCP'}</div>
      </div>
    </div>
    <svg class="h-4 w-4 text-cyber-text-dim transition-transform duration-200 {expanded ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
  </button>

  {#if expanded}
    <div class="border-t border-cyber-border/30 px-3 py-3 space-y-2">
      <label class="group flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors cursor-pointer hover:bg-cyber-surface-2/40">
        <input
          type="radio"
          name="chatmode"
          checked={$chatMode === 'kg-direct'}
          onchange={() => setMode('kg-direct')}
          class="mt-0.5 h-3 w-3 accent-cyber-cyan"
        />
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5">
            <Icon name="database" size={12} color="var(--color-cyber-cyan)" />
            <span class="text-xs font-medium text-cyber-text">Direct KG</span>
          </div>
          <div class="mt-0.5 text-[10px] text-cyber-text-dim/70 leading-relaxed">LightRAG handles retrieval and generation directly against your knowledge graph.</div>
        </div>
      </label>

      <label class="group flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors cursor-pointer hover:bg-cyber-surface-2/40">
        <input
          type="radio"
          name="chatmode"
          checked={$chatMode === 'llm-mcp'}
          onchange={() => setMode('llm-mcp')}
          class="mt-0.5 h-3 w-3 accent-cyber-cyan"
        />
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5">
            <Icon name="cpu" size={12} color="var(--color-cyber-cyan)" />
            <span class="text-xs font-medium text-cyber-text">LLM + MCP</span>
          </div>
          <div class="mt-0.5 text-[10px] text-cyber-text-dim/70 leading-relaxed">LLM calls KG tools via MCP for tool-augmented responses.</div>
        </div>
      </label>
    </div>
  {/if}
</div>