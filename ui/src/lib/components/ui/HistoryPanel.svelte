<script lang="ts">
  import { historyPanelOpen } from '$lib/stores/ui';
  import { conversationStore } from '$lib/stores/conversation.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { isMobile } from '$lib/composables/use-breakpoint';

  function close() {
    historyPanelOpen.set(false);
  }

  function handleBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) {
      close();
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      close();
    }
  }

  function formatTime(ts: number): string {
    const d = new Date(ts);
    const now = new Date();
    const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
    if (diffDays === 0) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'short' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function switchConversation(id: string) {
    conversationStore.switchConversation(id);
    close();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if $isMobile}
  {#if $historyPanelOpen}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="mobile-sheet-backdrop" onclick={handleBackdropClick} role="presentation">
      <div class="mobile-sheet mobile-sheet-bottom">
        <div class="mobile-sheet-handle"><div class="mobile-sheet-handle-bar"></div></div>
        <div class="flex items-center justify-between border-b border-cyber-border/50 px-4 py-3">
          <div class="flex items-center gap-2">
            <div class="flex h-7 w-7 items-center justify-center rounded-lg bg-cyber-cyan/10">
              <Icon name="clock" size={16} color="var(--color-cyber-cyan)" />
            </div>
            <h2 class="text-sm font-bold uppercase tracking-[0.15em] text-cyber-text">History</h2>
          </div>
          <button
            onclick={close}
            class="flex h-7 w-7 items-center justify-center rounded-lg border border-cyber-border/30 bg-cyber-surface-2/50 text-cyber-text-dim transition-all duration-200 active:bg-cyber-cyan/10 active:text-cyber-cyan"
            aria-label="Close history"
          >
            <Icon name="x" size={14} color="currentColor" />
          </button>
        </div>

        <div class="flex-1 overflow-y-auto overscroll-contain p-3">
          {#if conversationStore.conversations.length === 0}
            <div class="flex flex-col items-center justify-center py-16 text-center">
              <div class="flex h-12 w-12 items-center justify-center rounded-xl border border-cyber-border/30 bg-cyber-surface-2/30">
                <Icon name="send" size={20} color="var(--color-cyber-text-dim)" />
              </div>
              <p class="mt-3 text-xs text-cyber-text-dim">No conversations yet</p>
            </div>
          {:else}
            <div class="flex flex-col gap-1.5">
              {#each conversationStore.conversations as conv (conv.id)}
                <button
                  onclick={() => switchConversation(conv.id)}
                  class="conv-item {conversationStore.activeConversationId === conv.id ? 'conv-item-active' : ''}"
                >
                  <div class="conv-item-icon">
                    <Icon name="send" size={12} color={conversationStore.activeConversationId === conv.id ? 'var(--color-cyber-cyan)' : 'var(--color-cyber-text-dim)'} />
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5">
                      <span class="truncate text-xs font-medium {conversationStore.activeConversationId === conv.id ? 'text-cyber-cyan' : 'text-cyber-text'}">
                        {conv.title || 'New conversation'}
                      </span>
                      {#if conversationStore.unreadConversations.has(conv.id)}
                        <span class="unread-dot"></span>
                      {/if}
                    </div>
                    <div class="text-[10px] text-cyber-text-dim/50 mt-0.5">
                      {conv.messages.length} msg{conv.messages.length !== 1 ? 's' : ''} · {formatTime(conv.updatedAt)}
                    </div>
                  </div>
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    </div>
  {/if}
{:else}
  {#if $historyPanelOpen}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="history-backdrop"
      onclick={handleBackdropClick}
      role="presentation"
    >
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <aside class="history-panel" onkeydown={handleKeydown}>
        <div class="history-header">
          <div class="flex items-center gap-2">
            <div class="flex h-7 w-7 items-center justify-center rounded-lg bg-cyber-cyan/10">
              <Icon name="clock" size={16} color="var(--color-cyber-cyan)" />
            </div>
            <h2 class="text-sm font-bold uppercase tracking-[0.15em] text-cyber-text">History</h2>
          </div>
          <button
            onclick={close}
            class="flex h-7 w-7 items-center justify-center rounded-lg border border-cyber-border/30 bg-cyber-surface-2/50 text-cyber-text-dim transition-all duration-200 hover:border-cyber-border/60 hover:text-cyber-text hover:bg-cyber-surface-2"
            aria-label="Close history"
          >
            <Icon name="x" size={14} color="currentColor" />
          </button>
        </div>

        <div class="history-content">
          {#if conversationStore.conversations.length === 0}
            <div class="flex flex-col items-center justify-center py-16 text-center">
              <div class="flex h-12 w-12 items-center justify-center rounded-xl border border-cyber-border/30 bg-cyber-surface-2/30">
                <Icon name="send" size={20} color="var(--color-cyber-text-dim)" />
              </div>
              <p class="mt-3 text-xs text-cyber-text-dim">No conversations yet</p>
            </div>
          {:else}
            <div class="flex flex-col gap-1.5">
              {#each conversationStore.conversations as conv (conv.id)}
                <button
                  onclick={() => switchConversation(conv.id)}
                  class="conv-item {conversationStore.activeConversationId === conv.id ? 'conv-item-active' : ''}"
                >
                  <div class="conv-item-icon">
                    <Icon name="send" size={12} color={conversationStore.activeConversationId === conv.id ? 'var(--color-cyber-cyan)' : 'var(--color-cyber-text-dim)'} />
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5">
                      <span class="truncate text-xs font-medium {conversationStore.activeConversationId === conv.id ? 'text-cyber-cyan' : 'text-cyber-text'}">
                        {conv.title || 'New conversation'}
                      </span>
                      {#if conversationStore.unreadConversations.has(conv.id)}
                        <span class="unread-dot"></span>
                      {/if}
                    </div>
                    <div class="text-[10px] text-cyber-text-dim/50 mt-0.5">
                      {conv.messages.length} msg{conv.messages.length !== 1 ? 's' : ''} · {formatTime(conv.updatedAt)}
                    </div>
                  </div>
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </aside>
    </div>
  {/if}
{/if}

<style>
  .mobile-sheet-backdrop {
    position: fixed;
    inset: 0;
    z-index: 60;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    animation: backdrop-in 200ms ease-out;
  }

  .mobile-sheet {
    display: flex;
    flex-direction: column;
    background: var(--color-cyber-bg, #0a0e17);
    animation: sheet-enter 300ms cubic-bezier(0.16, 1, 0.3, 1);
    overflow: hidden;
  }

  .mobile-sheet-bottom {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    max-height: 85dvh;
    border-radius: 16px 16px 0 0;
    border-top: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    box-shadow: 0 -8px 40px rgba(0, 0, 0, 0.6), 0 -2px 20px rgba(0, 212, 255, 0.05);
  }

  .mobile-sheet-handle {
    display: flex;
    justify-content: center;
    padding: 8px 0 4px;
  }

  .mobile-sheet-handle-bar {
    width: 36px;
    height: 4px;
    border-radius: 2px;
    background: rgba(200, 214, 229, 0.3);
  }

  .history-backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(4px);
    animation: backdrop-in 200ms ease-out;
  }

  .history-panel {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: 320px;
    display: flex;
    flex-direction: column;
    background: var(--color-cyber-bg, #0a0e1a);
    border-left: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    box-shadow: -8px 0 40px rgba(0, 0, 0, 0.6), -2px 0 20px rgba(0, 212, 255, 0.05);
    animation: slide-in 250ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  @media (max-width: 768px) {
    .history-panel {
      top: auto;
      right: 0;
      left: 0;
      bottom: 0;
      width: 100%;
      max-height: 85dvh;
      border-left: none;
      border-top: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
      border-radius: 16px 16px 0 0;
      box-shadow: 0 -8px 40px rgba(0, 0, 0, 0.6), 0 -2px 20px rgba(0, 212, 255, 0.05);
      animation: slide-in-bottom 300ms cubic-bezier(0.16, 1, 0.3, 1);
    }
  }

  .history-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    background: var(--color-cyber-surface, rgba(13, 18, 36, 0.85));
    flex-shrink: 0;
  }

  .history-content {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
  }

  .conv-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    width: 100%;
    padding: 10px 12px;
    text-align: left;
    border-radius: 10px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--color-cyber-text);
    cursor: pointer;
    transition: all 200ms ease;
  }

  .conv-item:hover:not(.conv-item-active) {
    background: var(--color-cyber-surface-2);
    border-color: var(--color-cyber-border);
  }

  .conv-item-active {
    background: var(--color-cyber-surface);
    border-color: var(--color-cyber-cyan-dim);
    box-shadow: 0 0 12px rgba(0, 212, 255, 0.08);
  }

  .conv-item-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    border-radius: 6px;
    background: var(--color-cyber-surface-2);
    border: 1px solid var(--color-cyber-border);
    flex-shrink: 0;
    margin-top: 1px;
    transition: all 200ms ease;
  }

  .conv-item-active .conv-item-icon {
    background: var(--color-cyber-cyan-dim);
    border-color: var(--color-cyber-cyan-dim);
  }

  .unread-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 9999px;
    background: var(--color-cyber-cyan);
    box-shadow: 0 0 8px rgba(0, 212, 255, 0.4);
    flex-shrink: 0;
  }

  @keyframes backdrop-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  @keyframes slide-in {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }

  @keyframes slide-in-bottom {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }

  @keyframes sheet-enter {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }

  @keyframes pulse-glow {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
</style>