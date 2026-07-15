<script lang="ts">
  import type { ChatMessage } from '$lib/constants';

  interface Conversation {
    id: string;
    title: string;
    messages: ChatMessage[];
    createdAt: number;
    updatedAt: number;
  }

  let {
    conversations = [],
    activeId = '',
    onSelect,
    onNewConversation,
    collapsed = false,
    onToggleCollapse,
  }: {
    conversations: Conversation[];
    activeId: string;
    onSelect: (id: string) => void;
    onNewConversation: () => void;
    collapsed?: boolean;
    onToggleCollapse: () => void;
  } = $props();

  function formatTime(ts: number): string {
    const d = new Date(ts);
    const now = new Date();
    const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
    if (diffDays === 0) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'short' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
</script>

<div
  class="flex h-full flex-col border-r border-cyber-border bg-cyber-surface transition-all duration-300 {collapsed ? 'w-12' : 'w-72'}"
>
  <div class="flex items-center justify-between border-b border-cyber-border px-3 py-3">
    {#if !collapsed}
      <h2 class="text-sm font-semibold uppercase tracking-wider text-cyber-text-dim">Conversations</h2>
    {/if}
    <div class="flex gap-2">
      {#if !collapsed}
        <button
          onclick={onNewConversation}
          class="flex h-11 w-11 md:h-7 md:w-7 shrink-0 items-center justify-center rounded-lg border border-cyber-border text-cyber-text-dim transition-all duration-200 hover:border-cyber-cyan/50 hover:text-cyber-cyan hover:glow-cyan"
          title="New conversation"
        >
          <svg class="h-5 w-5 md:h-4 md:w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
        </button>
      {/if}
      <button
        onclick={onToggleCollapse}
        class="flex h-11 w-11 md:h-7 md:w-7 shrink-0 items-center justify-center rounded-lg border border-cyber-border text-cyber-text-dim transition-all duration-200 hover:border-cyber-purple/50 hover:text-cyber-purple"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        <svg
          class="h-5 w-5 md:h-4 md:w-4 transition-transform duration-200 {collapsed ? 'rotate-180' : ''}"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
        </svg>
      </button>
    </div>
  </div>

  {#if !collapsed}
    <div class="flex-1 overflow-y-auto py-1">
      {#each conversations as conv (conv.id)}
        <button
          onclick={() => onSelect(conv.id)}
          class="group flex w-full flex-col border-l-2 px-3 py-2.5 text-left transition-all duration-200 {activeId === conv.id
            ? 'border-cyber-cyan bg-cyber-cyan/5 text-cyber-text'
            : 'border-transparent text-cyber-text-dim hover:border-cyber-border hover:bg-cyber-surface-2/30 hover:text-cyber-text'}"
        >
          <span class="truncate text-sm font-medium">{conv.title || 'New conversation'}</span>
          <span class="mt-0.5 flex items-center gap-2 text-xs text-cyber-text-dim">
            <span>{formatTime(conv.updatedAt)}</span>
            <span>·</span>
            <span>{conv.messages.length} msg{conv.messages.length !== 1 ? 's' : ''}</span>
          </span>
        </button>
      {:else}
        <div class="px-4 py-8 text-center text-xs text-cyber-text-dim">
          No conversations yet
        </div>
      {/each}
    </div>
  {/if}
</div>