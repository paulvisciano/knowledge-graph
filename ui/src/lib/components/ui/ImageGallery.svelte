<script lang="ts">
  let {
    images = [],
    alt = 'Image',
  }: {
    images: string[];
    alt?: string;
  } = $props();

  let lightboxIndex = $state<number | null>(null);
  let lightboxVisible = $state(false);

  function openLightbox(index: number) {
    lightboxIndex = index;
    lightboxVisible = true;
  }

  function closeLightbox() {
    lightboxVisible = false;
    setTimeout(() => { lightboxIndex = null; }, 200);
  }

  /** Upgrade a thumbnail URL to its full-res form for the lightbox view.
   *  Only affects backend photo URLs carrying `?w=<int>`; other URLs
   *  (data URLs, document-content URLs, MCP image URLs) pass through. */
  function fullResUrl(url: string): string {
    return url.replace(/([?&]w=)\d+\b/, '$1full');
  }

  function goNext() {
    if (lightboxIndex !== null && images.length > 0) {
      lightboxIndex = (lightboxIndex + 1) % images.length;
    }
  }

  function goPrev() {
    if (lightboxIndex !== null && images.length > 0) {
      lightboxIndex = (lightboxIndex - 1 + images.length) % images.length;
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (!lightboxVisible) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeLightbox();
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      goNext();
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      goPrev();
    }
  }

  $effect(() => {
    if (lightboxVisible) {
      window.addEventListener('keydown', handleKeydown);
      document.body.style.overflow = 'hidden';
      return () => {
        window.removeEventListener('keydown', handleKeydown);
        document.body.style.overflow = '';
      };
    }
  });

  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    return { destroy() { node.remove(); } };
  }
</script>

{#if images.length > 0}
  <div class="mt-2 flex gap-2 overflow-x-auto pb-1" style="scrollbar-width: thin;">
    {#each images as imgUrl, i}
      <button
        type="button"
        onclick={() => openLightbox(i)}
        class="group/img relative h-24 w-24 shrink-0 overflow-hidden rounded-lg border border-cyber-border/30 transition-all duration-200 hover:border-cyber-cyan/40 hover:shadow-[0_0_8px_rgba(0,212,255,0.15)] focus:outline-none focus:ring-2 focus:ring-cyber-cyan/50"
        aria-label="Expand image {i + 1} of {images.length}"
      >
        <img
          src={imgUrl}
          {alt}
          class="h-full w-full object-cover transition-transform duration-200 group-hover/img:scale-110"
          loading="lazy"
          onerror={(e) => { (e.target as HTMLElement).closest('button')?.remove(); }}
        />
        <div class="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 transition-all duration-200 group-hover/img:bg-black/30">
          <svg class="h-4 w-4 opacity-0 transition-opacity duration-200 group-hover/img:opacity-90" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 3h6v6M14 10l6.1-6.1M9 21H3v-6M10 14l-6.1 6.1" />
          </svg>
        </div>
      </button>
    {/each}
  </div>
{/if}

{#if lightboxVisible && lightboxIndex !== null}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div use:portal
    class="fixed inset-0 z-[9999] bg-black animate-fade-in-up"
    onclick={(e) => { if (e.target === e.currentTarget) closeLightbox(); }}
    onkeydown={handleKeydown}
    role="dialog"
    aria-modal="true"
    aria-label="Image gallery"
  >
    <img
      src={fullResUrl(images[lightboxIndex])}
      alt="{alt} {lightboxIndex + 1}"
      class="absolute inset-0 h-full w-full object-contain"
    />

    <div class="pointer-events-none absolute inset-0 z-10 flex items-center justify-between px-3">
      {#if images.length > 1}
        <button
          type="button"
          onclick={goPrev}
          class="pointer-events-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-white/15 bg-black/40 text-white/80 backdrop-blur-sm transition-all hover:bg-black/60 hover:text-white"
          aria-label="Previous image"
        >
          <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <button
          type="button"
          onclick={goNext}
          class="pointer-events-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-white/15 bg-black/40 text-white/80 backdrop-blur-sm transition-all hover:bg-black/60 hover:text-white"
          aria-label="Next image"
        >
          <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      {:else}
        <div></div>
        <div></div>
      {/if}
    </div>

    <div class="absolute top-3 right-3 z-20 flex items-center gap-2">
      {#if images.length > 1}
        <div class="rounded-full bg-black/40 px-3 py-1 font-mono text-xs text-white/70 backdrop-blur-sm">
          {lightboxIndex + 1} / {images.length}
        </div>
      {/if}
      <button
        type="button"
        onclick={closeLightbox}
        class="flex h-11 w-11 items-center justify-center rounded-full border border-white/15 bg-black/40 text-white/80 backdrop-blur-sm transition-all hover:bg-black/60 hover:text-white"
        aria-label="Close lightbox"
      >
        <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  </div>
{/if}