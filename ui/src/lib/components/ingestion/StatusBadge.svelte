<script lang="ts">
  import type { DocStatus as DocStatusType } from '$lib/constants';

  let { status }: { status: DocStatusType['status'] } = $props();

  let config = $derived.by(() => {
    const map: Record<DocStatusType['status'], { bg: string; text: string; pulse: boolean; icon: string }> = {
      pending: { bg: 'bg-cyber-text-dim/20', text: 'text-cyber-text-dim', pulse: false, icon: '○' },
      parsing: { bg: 'bg-cyber-orange/20', text: 'text-cyber-orange', pulse: true, icon: '◐' },
      analyzing: { bg: 'bg-cyber-orange/20', text: 'text-cyber-orange', pulse: true, icon: '◑' },
      handling: { bg: 'bg-cyber-orange/20', text: 'text-cyber-orange', pulse: true, icon: '◔' },
      processing: { bg: 'bg-cyber-cyan/20', text: 'text-cyber-cyan', pulse: true, icon: '◉' },
      preprocessed: { bg: 'bg-cyber-purple/20', text: 'text-cyber-purple', pulse: false, icon: '◆' },
      processed: { bg: 'bg-cyber-green/20', text: 'text-cyber-green', pulse: false, icon: '✓' },
      failed: { bg: 'bg-cyber-red/20', text: 'text-cyber-red', pulse: false, icon: '✕' },
    };
    return map[status] ?? map.pending;
  });
</script>

<span
  class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium
    {config.bg} {config.text} {config.pulse ? 'animate-pulse-glow' : ''}"
>
  <span class="text-[10px]">{config.icon}</span>
  {status}
</span>