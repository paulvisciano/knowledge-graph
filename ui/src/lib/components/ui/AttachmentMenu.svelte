<script lang="ts">
  let {
    disabled = false,
    size = 'md',
    fluid = false,
    onPickDocument,
  }: {
    disabled?: boolean;
    size?: 'sm' | 'md';
    fluid?: boolean;
    onPickDocument: () => void;
  } = $props();

  let open = $state(false);

  const sizeClass = $derived(fluid ? 'h-full w-11' : size === 'sm' ? 'h-10 w-10' : 'h-11 w-11');
  const iconSize = $derived(size === 'sm' ? 'h-4 w-4' : 'h-5 w-5');

  function toggle() {
    if (!disabled) open = !open;
  }

  function pickDocument() {
    open = false;
    onPickDocument();
  }

  function handleClickOutside(e: MouseEvent) {
    const target = e.target as HTMLElement;
    if (!target.closest('.attachment-menu-root')) {
      open = false;
    }
  }

  $effect(() => {
    if (open) {
      window.addEventListener('click', handleClickOutside);
      return () => window.removeEventListener('click', handleClickOutside);
    }
  });
</script>

<div class="attachment-menu-root relative">
  <button
    onclick={toggle}
    {disabled}
    data-testid="attach-file-button"
    class="flex {sizeClass} shrink-0 items-center justify-center rounded-full text-cyber-text-dim/70 transition-colors hover:bg-cyber-surface-2/60 hover:text-cyber-cyan disabled:cursor-not-allowed disabled:opacity-30"
    title="Attach file"
  >
    <svg class="{iconSize}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16M4 12h16" />
    </svg>
  </button>
  {#if open}
    <div class="absolute bottom-full right-0 mb-2 min-w-[160px] rounded-2xl border border-cyber-cyan/25 bg-cyber-surface-2/80 backdrop-blur-md shadow-lg">
      <button
        onclick={pickDocument}
        class="flex w-full items-center gap-2.5 rounded-2xl px-3 py-2 text-left text-sm text-cyber-text-dim transition-colors hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
      >
        <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <div>
          <div class="text-xs font-medium">Document</div>
          <div class="text-[10px] text-cyber-text-dim/60">TXT, MD, CSV, JSON, HTML, YAML</div>
        </div>
      </button>
    </div>
  {/if}
</div>