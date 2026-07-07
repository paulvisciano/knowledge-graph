<script lang="ts">
  let { status, size = 8 }: { status: 'connected' | 'disconnected' | 'busy' | 'error'; size?: number } = $props();

  let dotStyle = $derived.by(() => {
    const base = `inline-block rounded-full`;
    switch (status) {
      case 'connected':
        return `${base} bg-cyber-green animate-pulse-glow shadow-[0_0_6px_rgba(0,255,136,0.5)]`;
      case 'disconnected':
        return `${base} bg-cyber-text-dim/50`;
      case 'busy':
        return `${base} bg-cyber-orange animate-pulse-glow shadow-[0_0_6px_rgba(255,140,0,0.5)]`;
      case 'error':
        return `${base} bg-cyber-red shadow-[0_0_6px_rgba(255,51,102,0.5)]`;
      default:
        return `${base} bg-cyber-text-dim/50`;
    }
  });
</script>

<span class={dotStyle} style:width="{size}px" style:height="{size}px"></span>