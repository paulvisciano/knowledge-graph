<script lang="ts">
  import '../app.css';
  import Icon from '$lib/components/ui/Icon.svelte';
  import NavDrawer from '$lib/components/ui/NavDrawer.svelte';
  import HistoryPanel from '$lib/components/ui/HistoryPanel.svelte';
  import SettingsDrawer from '$lib/components/settings/SettingsDrawer.svelte';
  import { navDrawerOpen } from '$lib/stores/ui';
  import { connectionStore } from '$lib/stores/connection.svelte';
  import { lightragStatus, llamaStatus } from '$lib/stores/ui';
  import { isMobile, isTablet } from '$lib/composables/use-breakpoint';

  let { children }: { children: import('svelte').Snippet } = $props();

  let pollingStarted = false;
  $effect(() => {
    let cancelled = false;
    connectionStore
      .connectMcp()
      .catch((e) => console.error('MCP connect failed:', e))
      .finally(() => {
        if (cancelled || pollingStarted) return;
        pollingStarted = true;
        connectionStore.startPolling();
      });
    return () => {
      cancelled = true;
      pollingStarted = false;
      connectionStore.stopPolling();
    };
  });

  $effect(() => {
    lightragStatus.set(connectionStore.lightragConnected ? 'connected' : 'disconnected');
    llamaStatus.set(connectionStore.llamaConnected ? 'connected' : 'disconnected');
  });


</script>

{#if $isMobile || $isTablet}
  <div class="nexus-mobile-shell">
      <button
        onclick={() => navDrawerOpen.update((v) => !v)}
        class="menu-trigger"
        title="Menu"
        aria-label="Open navigation menu"
      >
        <Icon name="menu" size={18} color="currentColor" />
      </button>
      <div class="mobile-page-content">
        {@render children()}
      </div>
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
    height: 100vh;
    height: 100dvh;
    width: 100vw;
    overflow: hidden;
    background: var(--color-cyber-bg, #0a0e17);
    color: var(--color-cyber-text, #c8d6e5);
    position: relative;
  }

  .mobile-page-content {
    flex: 1;
    overflow: hidden;
    position: relative;
    min-height: 0;
  }

  .menu-trigger {
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

  .menu-trigger:hover {
    background: rgba(26, 34, 53, 0.9);
    color: var(--color-cyber-text, #c8d6e5);
    border-color: rgba(0, 212, 255, 0.3);
    box-shadow: 0 0 12px rgba(0, 212, 255, 0.1);
  }
</style>