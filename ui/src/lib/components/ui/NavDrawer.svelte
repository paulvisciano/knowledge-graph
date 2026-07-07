<script lang="ts">
  import { navDrawerOpen, activeTab, settingsDrawerOpen, lightragStatus, llamaStatus, mcpStatus, type TabId } from '$lib/stores/ui';
  import Icon from '$lib/components/ui/Icon.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import { isMobile } from '$lib/composables/use-breakpoint';

  function close() {
    navDrawerOpen.set(false);
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

  function openSettings() {
    settingsDrawerOpen.set(true);
    close();
  }

  const tabs: { id: TabId; icon: string; label: string }[] = [
    { id: 'graph', icon: 'graph', label: 'Graph' },
    { id: 'ingestion', icon: 'upload', label: 'Ingest' },
    { id: 'activity', icon: 'activity', label: 'Activity' },
  ];
</script>

<svelte:window onkeydown={handleKeydown} />

{#if $navDrawerOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="nav-drawer-backdrop"
    onclick={handleBackdropClick}
    role="presentation"
  >
    <aside class="nav-drawer-panel {$isMobile ? 'nav-drawer-bottom' : ''}">
      <div class="nav-drawer-header">
        <div class="flex items-center gap-2.5">
          <div class="flex h-8 w-8 items-center justify-center rounded-lg border border-cyber-cyan/20 bg-cyber-cyan/10">
            <Icon name="sidebar" size={16} color="var(--color-cyber-cyan)" />
          </div>
          <div>
            <h2 class="text-sm font-bold uppercase tracking-[0.15em] text-cyber-text text-glow-cyan">Nexus</h2>
          </div>
        </div>
        <button
          onclick={close}
          class="nav-icon-btn"
          aria-label="Close menu"
        >
          <Icon name="x" size={14} color="currentColor" />
        </button>
      </div>

      <nav class="flex-1 overflow-y-auto p-3">
        <div class="flex flex-col gap-1.5">
          {#each tabs as tab (tab.id)}
            <button
              onclick={() => { activeTab.set(tab.id); close(); }}
              class="nav-item {$activeTab === tab.id ? 'nav-item-active' : ''}"
            >
              <div class="nav-item-icon">
                <Icon name={tab.icon} size={18} color={$activeTab === tab.id ? 'var(--color-cyber-cyan)' : 'currentColor'} />
              </div>
              <span class="text-sm font-medium">{tab.label}</span>
              {#if $activeTab === tab.id}
                <div class="nav-item-glow"></div>
              {/if}
            </button>
          {/each}
        </div>
      </nav>

      <div class="nav-drawer-footer">
        <div class="status-row">
          <div class="status-item" title="LightRAG">
            <StatusDot status={$lightragStatus} size={6} />
            <span class="status-label">LR</span>
          </div>
          <div class="status-item" title="llama-server">
            <StatusDot status={$llamaStatus} size={6} />
            <span class="status-label">LLM</span>
          </div>
          <div class="status-item" title="MCP">
            <StatusDot status={$mcpStatus} size={6} />
            <span class="status-label">MCP</span>
          </div>
        </div>
        <button
          onclick={openSettings}
          class="nav-icon-btn"
          title="Settings"
          aria-label="Open settings"
        >
          <Icon name="settings" size={14} color="currentColor" />
        </button>
      </div>
    </aside>
  </div>
{/if}

<style>
  .nav-drawer-backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(4px);
    animation: backdrop-in 200ms ease-out;
  }

  .nav-drawer-panel {
    position: absolute;
    top: 0;
    left: 0;
    bottom: 0;
    width: 280px;
    display: flex;
    flex-direction: column;
    background: var(--color-cyber-bg, #0a0e1a);
    border-right: 1px solid var(--color-cyber-border);
    box-shadow: 8px 0 40px rgba(0, 0, 0, 0.6), 2px 0 20px rgba(0, 212, 255, 0.05);
    animation: slide-in-left 250ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .nav-drawer-bottom {
    top: auto;
    left: 0;
    right: 0;
    bottom: 0;
    width: 100%;
    max-height: 85dvh;
    border-right: none;
    border-top: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    border-radius: 16px 16px 0 0;
    box-shadow: 0 -8px 40px rgba(0, 0, 0, 0.6), 0 -2px 20px rgba(0, 212, 255, 0.05);
    animation: slide-in-bottom 300ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .nav-drawer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px;
    background: var(--color-cyber-surface);
    border-bottom: 1px solid var(--color-cyber-border);
    flex-shrink: 0;
  }

  .nav-icon-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    border: 1px solid var(--color-cyber-border);
    background: var(--color-cyber-surface-2);
    color: var(--color-cyber-text-dim);
    transition: all 200ms ease;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .nav-icon-btn:hover {
    border-color: var(--color-cyber-cyan-dim);
    background: var(--color-cyber-surface-2);
    color: var(--color-cyber-text);
    box-shadow: 0 0 8px rgba(0, 212, 255, 0.1);
  }

  .nav-icon-btn:active {
    background: rgba(0, 212, 255, 0.1);
    transform: scale(0.92);
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    padding: 10px 12px;
    text-align: left;
    border-radius: 10px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--color-cyber-text-dim);
    cursor: pointer;
    transition: all 200ms ease;
    position: relative;
    overflow: hidden;
    -webkit-tap-highlight-color: transparent;
  }

  .nav-item:hover:not(.nav-item-active) {
    background: var(--color-cyber-surface-2);
    color: var(--color-cyber-text);
    border-color: var(--color-cyber-border);
  }

  .nav-item:active {
    background: rgba(0, 212, 255, 0.1);
    transform: scale(0.98);
  }

  .nav-item-active {
    background: var(--color-cyber-surface);
    color: var(--color-cyber-cyan);
    border-color: var(--color-cyber-cyan-dim);
    box-shadow: 0 0 12px rgba(0, 212, 255, 0.12), inset 0 0 20px rgba(0, 212, 255, 0.03);
  }

  .nav-item-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: var(--color-cyber-surface-2);
    border: 1px solid var(--color-cyber-border);
    flex-shrink: 0;
    transition: all 200ms ease;
  }

  .nav-item-active .nav-item-icon {
    background: var(--color-cyber-cyan-dim);
    border-color: var(--color-cyber-cyan-dim);
    box-shadow: 0 0 8px rgba(0, 212, 255, 0.25);
  }

  .nav-drawer-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    background: var(--color-cyber-surface);
    border-top: 1px solid var(--color-cyber-border);
    flex-shrink: 0;
  }

  .status-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .status-item {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .status-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-cyber-text-dim);
  }

  .nav-item-glow {
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, rgba(0, 212, 255, 0.08), transparent);
    pointer-events: none;
  }

  @keyframes backdrop-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  @keyframes slide-in-left {
    from { transform: translateX(-100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }

  @keyframes slide-in-bottom {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }
</style>