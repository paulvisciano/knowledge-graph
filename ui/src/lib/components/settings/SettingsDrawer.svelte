<script lang="ts">
  import { settingsDrawerOpen } from '$lib/stores/ui';
  import Icon from '$lib/components/ui/Icon.svelte';
  import SystemPromptEditor from '$lib/components/settings/SystemPromptEditor.svelte';
  import ChatModeToggle from '$lib/components/settings/ChatModeToggle.svelte';
  import { isMobile } from '$lib/composables/use-breakpoint';
  import { createSwipeHandler } from '$lib/composables/use-swipe';

  let panelEl: HTMLDivElement | undefined = $state();

  function close() {
    settingsDrawerOpen.set(false);
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

  $effect(() => {
    if (!$isMobile || !$settingsDrawerOpen || !panelEl) return;
    const handler = createSwipeHandler({
      element: panelEl,
      onSwipeDown: () => close(),
      threshold: 60,
    });
    return () => handler.destroy();
  });
</script>

<svelte:window onkeydown={handleKeydown} />

{#if $isMobile}
  {#if $settingsDrawerOpen}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="mobile-sheet-backdrop" onclick={handleBackdropClick} role="presentation">
      <div bind:this={panelEl} class="mobile-sheet mobile-sheet-bottom">
        <div class="mobile-sheet-handle"><div class="mobile-sheet-handle-bar"></div></div>
        <div class="flex items-center justify-between border-b border-cyber-border/50 px-4 py-3">
          <div class="flex items-center gap-2">
            <div class="flex h-7 w-7 items-center justify-center rounded-lg bg-cyber-cyan/10">
              <Icon name="settings" size={16} color="var(--color-cyber-cyan)" />
            </div>
            <h2 class="text-sm font-bold uppercase tracking-[0.15em] text-cyber-text">Settings</h2>
          </div>
          <button
            onclick={close}
            class="flex h-11 w-11 md:h-7 md:w-7 items-center justify-center rounded-lg border border-cyber-border/30 bg-cyber-surface-2/50 text-cyber-text-dim transition-all duration-200 active:bg-cyber-cyan/10 active:text-cyber-cyan"
            aria-label="Close settings"
          >
            <Icon name="x" size={14} color="currentColor" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto overscroll-contain p-4">
          <div class="space-y-3">
            <ChatModeToggle />
            <SystemPromptEditor />
          </div>
        </div>
      </div>
    </div>
  {/if}
{:else}
  {#if $settingsDrawerOpen}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11x_no_static_element_interactions -->
    <div
      class="drawer-backdrop"
      onclick={handleBackdropClick}
      role="presentation"
    >
      <aside
        class="drawer-panel"
        onkeydown={handleKeydown}
      >
        <div class="drawer-header">
          <div class="flex items-center gap-2">
            <div class="flex h-7 w-7 items-center justify-center rounded-lg bg-cyber-cyan/10">
              <Icon name="settings" size={16} color="var(--color-cyber-cyan)" />
            </div>
            <h2 class="text-sm font-bold uppercase tracking-[0.15em] text-cyber-text">Settings</h2>
          </div>
          <button
            onclick={close}
            class="flex h-9 w-9 items-center justify-center rounded-lg border border-cyber-border/30 bg-cyber-surface-2/50 text-cyber-text-dim transition-all duration-200 hover:border-cyber-border/60 hover:text-cyber-text hover:bg-cyber-surface-2"
            aria-label="Close settings"
          >
            <Icon name="x" size={14} color="currentColor" />
          </button>
        </div>

        <div class="drawer-content">
          <div class="space-y-3">
            <ChatModeToggle />
            <SystemPromptEditor />
          </div>
        </div>
      </aside>
    </div>
  {/if}
{/if}

<style>
  .sheet-handle-bar {
    display: flex;
    justify-content: center;
    padding: 8px 0 4px;
  }
  .sheet-handle-bar-inner {
    width: 36px;
    height: 4px;
    border-radius: 2px;
    background: rgba(200, 214, 229, 0.3);
  }

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
    max-height: 90dvh;
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

  .drawer-backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(4px);
    animation: backdrop-in 200ms ease-out;
  }

  .drawer-panel {
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

  .drawer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    background: var(--color-cyber-surface, rgba(13, 18, 36, 0.85));
    flex-shrink: 0;
  }

  .drawer-content {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  @keyframes backdrop-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  @keyframes slide-in {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }

  @keyframes sheet-enter {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }

  @keyframes slide-in-bottom {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }
</style>