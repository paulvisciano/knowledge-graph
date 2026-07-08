<script lang="ts">
  let {
    onzoomIn = () => {},
    onzoomOut = () => {},
    onfitView = () => {}
  }: {
    onzoomIn: () => void;
    onzoomOut: () => void;
    onfitView: () => void;
  } = $props();
</script>

<div class="graph-controls absolute bottom-16 right-4 z-20 flex flex-col gap-2 animate-fade-in-up">
  <div class="bg-cyber-surface/80 backdrop-blur-md rounded-xl border border-cyber-border p-2 flex flex-col gap-1">
    <button
      onclick={onzoomIn}
      class="zoom-btn w-9 h-9 md:w-9 md:h-9 flex items-center justify-center rounded-lg text-cyber-cyan hover:bg-cyber-cyan/10 transition-colors"
      title="Zoom In"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
    </button>
    <button
      onclick={onzoomOut}
      class="zoom-btn w-9 h-9 md:w-9 md:h-9 flex items-center justify-center rounded-lg text-cyber-cyan hover:bg-cyber-cyan/10 transition-colors"
      title="Zoom Out"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
    </button>
    <div class="w-full h-px bg-cyber-border my-0.5"></div>
    <button
      onclick={onfitView}
      class="zoom-btn w-9 h-9 md:w-9 md:h-9 flex items-center justify-center rounded-lg text-cyber-cyan hover:bg-cyber-cyan/10 transition-colors"
      title="Fit to View"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg>
    </button>
  </div>
</div>

<style>
  @media (max-width: 768px) {
    .graph-controls {
      bottom: 140px;
      right: 8px;
    }
    .zoom-btn {
      width: 44px;
      height: 44px;
    }
  }
</style>