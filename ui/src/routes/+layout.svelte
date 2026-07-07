<script lang="ts">
  import '$lib/framework7';
  import '../app.css';
  import Icon from '$lib/components/ui/Icon.svelte';
  import NavDrawer from '$lib/components/ui/NavDrawer.svelte';
  import HistoryPanel from '$lib/components/ui/HistoryPanel.svelte';
  import SettingsDrawer from '$lib/components/settings/SettingsDrawer.svelte';
  import MobileTabBar from '$lib/components/mobile/MobileTabBar.svelte';
  import { navDrawerOpen, historyPanelOpen, settingsDrawerOpen, activeTab, type TabId } from '$lib/stores/ui';
  import { connectionStore } from '$lib/stores/connection.svelte';
  import { lightragStatus, llamaStatus, mcpStatus } from '$lib/stores/ui';
  import { isMobile } from '$lib/composables/use-breakpoint';

  let { children }: { children: import('svelte').Snippet } = $props();

  $effect(() => {
    connectionStore.startPolling();
    connectionStore.connectMcp();
    return () => connectionStore.stopPolling();
  });

  $effect(() => {
    lightragStatus.set(connectionStore.lightragConnected ? 'connected' : 'disconnected');
    llamaStatus.set(connectionStore.llamaConnected ? 'connected' : 'disconnected');
    mcpStatus.set(connectionStore.mcpConnected ? 'connected' : 'disconnected');
  });

  function openMobileNav() {
    navDrawerOpen.set(true);
  }

  function openMobileHistory() {
    historyPanelOpen.set(true);
  }

  function openMobileSettings() {
    settingsDrawerOpen.set(true);
  }
</script>

{#if $isMobile}
  <div class="nexus-mobile-shell">
      <header class="mobile-topbar">
        <button onclick={openMobileNav} class="mobile-topbar-btn" aria-label="Menu">
          <Icon name="menu" size={18} color="currentColor" />
        </button>
        <div class="mobile-topbar-title">
          <Icon name="sidebar" size={14} color="var(--color-cyber-cyan)" />
          <span class="text-glow-cyan">Nexus</span>
        </div>
        <div class="flex items-center gap-1">
          <button onclick={openMobileHistory} class="mobile-topbar-btn" aria-label="History">
            <Icon name="clock" size={16} color="currentColor" />
          </button>
          <button onclick={openMobileSettings} class="mobile-topbar-btn" aria-label="Settings">
            <Icon name="settings" size={16} color="currentColor" />
          </button>
        </div>
      </header>
      <div class="mobile-page-content">
        {@render children()}
      </div>
      <MobileTabBar />
      <NavDrawer />
      <HistoryPanel />
      <SettingsDrawer />
    </div>
  {:else}
    <div class="nexus-shell flex h-screen w-screen overflow-hidden bg-cyber-bg text-cyber-text">
      <button
        onclick={() => navDrawerOpen.update((v) => !v)}
        class="menu-trigger"
        title="Menu"
        aria-label="Open navigation menu"
      >
        <Icon name="menu" size={18} color="currentColor" />
      </button>

      <main class="flex-1 overflow-hidden">
        {@render children()}
      </main>

      <NavDrawer />
      <HistoryPanel />
      <SettingsDrawer />
    </div>
  {/if}

<style>
  .nexus-mobile-shell {
    display: flex;
    flex-direction: column;
    height: 100dvh;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
    background: var(--color-cyber-bg, #0a0e17);
    color: var(--color-cyber-text, #c8d6e5);
    position: relative;
  }

  .mobile-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    padding-top: calc(env(safe-area-inset-top, 0px) + 8px);
    background: rgba(10, 14, 23, 0.9);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    z-index: 30;
    flex-shrink: 0;
  }

  .mobile-topbar-title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: var(--color-cyber-text, #c8d6e5);
  }

  .mobile-topbar-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border-radius: 10px;
    border: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    background: rgba(17, 24, 39, 0.8);
    color: var(--color-cyber-text-dim, #5a6b80);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    transition: all 200ms ease;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .mobile-topbar-btn:active {
    background: rgba(0, 212, 255, 0.15);
    color: var(--color-cyber-cyan, #00d4ff);
    transform: scale(0.92);
  }

  .mobile-page-content {
    flex: 1;
    overflow: hidden;
    position: relative;
    min-height: 0;
  }

  .nexus-shell .menu-trigger {
    position: fixed;
    top: 12px;
    left: 12px;
    z-index: 40;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border-radius: 10px;
    border: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    background: rgba(17, 24, 39, 0.8);
    color: var(--color-cyber-text-dim, #5a6b80);
    backdrop-filter: blur(12px);
    transition: all 200ms ease;
    cursor: pointer;
  }

  .nexus-shell .menu-trigger:hover {
    background: rgba(26, 34, 53, 0.9);
    color: var(--color-cyber-text, #c8d6e5);
    border-color: rgba(0, 212, 255, 0.3);
    box-shadow: 0 0 12px rgba(0, 212, 255, 0.1);
  }
</style>