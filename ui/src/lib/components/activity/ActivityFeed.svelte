<script lang="ts">
  import type { ActivityEvent } from '$lib/constants';
  import ActivityItem from './ActivityItem.svelte';

  let { events = [], connected = true }: { events?: ActivityEvent[]; connected?: boolean } = $props();

  let container: HTMLDivElement | undefined = $state();
  let prevLength = $state(0);

  $effect(() => {
    if (events.length > prevLength && container) {
      container.scrollTop = 0;
    }
    prevLength = events.length;
  });
</script>

<div class="flex h-full flex-col overflow-hidden rounded-lg border border-cyber-border bg-cyber-surface">
  <div class="flex shrink-0 items-center justify-between border-b border-cyber-border px-4 py-2.5">
    <div class="flex items-center gap-2">
      <h2 class="text-sm font-semibold text-cyber-text">Activity</h2>
      <span
        class="inline-block h-2 w-2 rounded-full {connected
          ? 'bg-cyber-green animate-pulse-glow'
          : 'bg-cyber-red'}"
      ></span>
    </div>
    <span class="text-xs text-cyber-text-dim">{events.length} events</span>
  </div>

  <div
    bind:this={container}
    class="flex-1 overflow-y-auto"
  >
    {#if events.length === 0}
      <div class="flex h-32 items-center justify-center text-sm text-cyber-text-dim">
        No activity yet
      </div>
    {:else}
      <div class="flex flex-col gap-1.5 p-2">
        {#each events.toSorted((a, b) => b.timestamp - a.timestamp) as event, i (event.id)}
          <div class:animate-slide-in-right={i === 0}>
            <ActivityItem {event} />
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>