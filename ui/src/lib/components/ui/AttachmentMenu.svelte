<script lang="ts">
  let {
    disabled = false,
    size = 'md',
    onPickImage,
    onPickDocument,
  }: {
    disabled?: boolean;
    size?: 'sm' | 'md';
    onPickImage: () => void;
    onPickDocument: () => void;
  } = $props();

  let open = $state(false);

  const sizeClass = $derived(size === 'sm' ? 'h-8 w-8' : 'h-9 w-9');
  const iconSize = $derived(size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4');

  function toggle() {
    if (!disabled) open = !open;
  }

  function pickImage() {
    open = false;
    onPickImage();
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
    class="flex {sizeClass} shrink-0 items-center justify-center rounded-lg border border-cyber-border text-cyber-text-dim transition-colors hover:border-cyber-purple/40 hover:text-cyber-purple disabled:cursor-not-allowed disabled:opacity-30"
    title="Attach file"
  >
    <svg class="{iconSize}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
    </svg>
  </button>
  {#if open}
    <div class="absolute bottom-full left-0 mb-2 min-w-[160px] rounded-lg border border-cyber-border bg-cyber-surface-2 shadow-lg">
      <button
        onclick={pickImage}
        data-testid="pick-image-button"
        class="flex w-full items-center gap-2.5 rounded-t-lg px-3 py-2 text-left text-sm text-cyber-text-dim transition-colors hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
      >
        <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
        <div>
          <div class="text-xs font-medium">Image</div>
          <div class="text-[10px] text-cyber-text-dim/60">PNG, JPEG, GIF, WebP, SVG</div>
        </div>
      </button>
      <div class="mx-2 border-t border-cyber-border/50"></div>
      <button
        onclick={pickDocument}
        class="flex w-full items-center gap-2.5 rounded-b-lg px-3 py-2 text-left text-sm text-cyber-text-dim transition-colors hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
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