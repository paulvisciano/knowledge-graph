<script lang="ts">
  import type { ActivityEvent } from '$lib/constants';

  let { event }: { event: ActivityEvent } = $props();

  let expanded = $state(false);

  let iconConfig = $derived.by(() => {
    const map: Record<ActivityEvent['type'], { icon: string; color: string }> = {
      ingestion: { icon: '📄', color: 'text-cyber-orange' },
      query: { icon: '🔍', color: 'text-cyber-cyan' },
      mcp_call: { icon: '🔧', color: 'text-cyber-purple' },
      graph_update: { icon: '◉', color: 'text-cyber-green' },
      system: { icon: '⚙', color: 'text-cyber-text-dim' },
    };
    return map[event.type] ?? map.system;
  });

  let statusConfig = $derived.by(() => {
    if (!event.status) return null;
    const map: Record<string, { color: string; label: string; pulse: boolean }> = {
      running: { color: 'bg-cyber-orange/20 text-cyber-orange', label: 'running', pulse: true },
      completed: { color: 'bg-cyber-green/20 text-cyber-green', label: 'done', pulse: false },
      error: { color: 'bg-cyber-red/20 text-cyber-red', label: 'error', pulse: false },
    };
    return map[event.status] ?? null;
  });

  let relativeTime = $derived.by(() => {
    const diff = Date.now() - event.timestamp;
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  });

  let metaEntries = $derived(
    event.meta ? Object.entries(event.meta) : []
  );
</script>

<div
  class="group rounded-lg border border-cyber-border bg-cyber-surface/50 px-3 py-2 transition-all duration-200
    hover:border-cyber-cyan/30 hover:glow-cyan"
>
  <div class="flex items-start gap-2.5">
    <div class="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-cyber-surface-2 text-sm {iconConfig.color}">
      {iconConfig.icon}
    </div>

    <div class="min-w-0 flex-1">
      <div class="flex items-center gap-2">
        <span class="truncate text-sm font-medium text-cyber-text">{event.title}</span>
        {#if statusConfig}
          <span class="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium {statusConfig.color} {statusConfig.pulse ? 'animate-pulse-glow' : ''}">
            {statusConfig.label}
          </span>
        {/if}
      </div>
      <p class="mt-0.5 truncate text-xs text-cyber-text-dim">{event.description}</p>
      <div class="mt-1 flex items-center gap-2 text-[10px] text-cyber-text-dim/70">
        <span>{relativeTime}</span>
        {#if metaEntries.length > 0}
          <button
            onclick={() => (expanded = !expanded)}
            class="text-cyber-cyan/60 transition-colors hover:text-cyber-cyan"
          >
            {expanded ? '▾ hide' : '▸ details'}
          </button>
        {/if}
      </div>

      {#if expanded && metaEntries.length > 0}
        <div class="mt-1.5 rounded border border-cyber-border/50 bg-cyber-bg/50 px-2 py-1.5 font-mono text-[10px]">
          {#each metaEntries as [key, value]}
            <div class="flex gap-1">
              <span class="text-cyber-cyan">{key}:</span>
              <span class="text-cyber-text-dim">{JSON.stringify(value)}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</div>