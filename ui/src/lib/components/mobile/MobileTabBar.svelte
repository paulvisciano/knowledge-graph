<script lang="ts">
  import { activeTab, type TabId } from '$lib/stores/ui';
  import Icon from '$lib/components/ui/Icon.svelte';

  const tabs: { id: TabId; icon: string; label: string }[] = [
    { id: 'graph', icon: 'graph', label: 'Graph' },
    { id: 'ingestion', icon: 'upload', label: 'Ingest' },
  ];
</script>

<div class="mobile-tabbar">
  {#each tabs as tab (tab.id)}
    <button
      class="tabbar-item {$activeTab === tab.id ? 'tabbar-item-active' : ''}"
      onclick={() => activeTab.set(tab.id)}
      aria-label={tab.label}
    >
      <Icon name={tab.icon} size={20} color={$activeTab === tab.id ? 'var(--color-cyber-cyan)' : 'var(--color-cyber-text-dim)'} />
      <span class="tabbar-label">{tab.label}</span>
    </button>
  {/each}
</div>

<style>
  .mobile-tabbar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 50;
    display: flex;
    align-items: stretch;
    height: 56px;
    background: rgba(10, 14, 23, 0.95);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-top: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }

  .tabbar-item {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    background: transparent;
    border: none;
    color: var(--color-cyber-text-dim, #5a6b80);
    transition: all 200ms ease;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    touch-action: manipulation;
    position: relative;
  }

  .tabbar-item-active {
    color: var(--color-cyber-cyan, #00d4ff);
  }

  .tabbar-item-active::before {
    content: '';
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 24px;
    height: 2px;
    background: var(--color-cyber-cyan, #00d4ff);
    border-radius: 1px;
    box-shadow: 0 0 8px rgba(0, 212, 255, 0.4);
  }

  .tabbar-label {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.03em;
    line-height: 1;
  }

  .tabbar-item-active .tabbar-label {
    color: var(--color-cyber-cyan, #00d4ff);
    text-shadow: 0 0 8px rgba(0, 212, 255, 0.3);
  }
</style>