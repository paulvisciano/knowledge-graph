<script lang="ts">
  let { busy = false, onScan }: { busy?: boolean; onScan: () => Promise<void> } = $props();

  let loading = $state(false);

  async function handleClick() {
    if (busy || loading) return;
    loading = true;
    try {
      await onScan();
    } finally {
      loading = false;
    }
  }
</script>

<button
  onclick={handleClick}
  disabled={busy || loading}
  class="group relative inline-flex items-center gap-2 rounded-lg border border-cyber-cyan/40 bg-cyber-cyan/5 px-4 py-2 text-sm font-medium text-cyber-cyan transition-all duration-300
    hover:border-cyber-cyan hover:bg-cyber-cyan/10 hover:text-glow-cyan
    disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-cyber-cyan/40 disabled:hover:bg-cyber-cyan/5 disabled:hover:text-cyber-cyan disabled:hover:shadow-none"
>
  {#if busy || loading}
    <span class="animate-pulse-glow inline-block h-2 w-2 rounded-full bg-cyber-orange"></span>
    <span>Scanning…</span>
  {:else}
    <svg class="h-4 w-4 transition-transform duration-200 group-hover:rotate-45" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
    </svg>
    <span>Scan Input Directory</span>
  {/if}
</button>