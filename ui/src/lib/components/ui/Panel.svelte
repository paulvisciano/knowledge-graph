<script lang="ts">
  import Icon from './Icon.svelte';

  let {
    title = '',
    icon = '',
    collapsible = false,
    defaultCollapsed = false,
    glow = '',
    children,
  }: {
    title?: string;
    icon?: string;
    collapsible?: boolean;
    defaultCollapsed?: boolean;
    glow?: 'cyan' | 'purple' | 'green' | '';
    children: import('svelte').Snippet;
  } = $props();

  let collapsed = $state(defaultCollapsed === true);

  let glowClass = $derived(
    glow === 'cyan' ? 'glow-cyan' : glow === 'purple' ? 'glow-purple' : glow === 'green' ? 'glow-green' : ''
  );

  let borderClass = $derived(
    glow === 'cyan'
      ? 'border-cyber-cyan/30'
      : glow === 'purple'
        ? 'border-cyber-purple/30'
        : glow === 'green'
          ? 'border-cyber-green/30'
          : 'border-cyber-border'
  );

  function toggle() {
    if (collapsible) collapsed = !collapsed;
  }
</script>

<div
  class="flex flex-col rounded-lg border {borderClass} bg-cyber-surface/95 backdrop-blur-sm transition-all duration-300 {glowClass}"
>
  {#if title || icon}
    <button
      onclick={toggle}
      class="flex items-center gap-2 border-b border-cyber-border/50 px-4 py-2.5 text-left transition-all duration-200 {collapsible ? 'cursor-pointer hover:bg-cyber-surface-2/50' : 'cursor-default'} group/header"
    >
      {#if icon}
        <Icon name={icon} size={16} color="var(--color-cyber-cyan)" />
      {/if}
      {#if title}
        <span class="text-sm font-semibold uppercase tracking-wider text-cyber-text-dim">{title}</span>
      {/if}
      {#if collapsible}
        <span class="ml-auto transition-transform duration-200 {collapsed ? '' : 'rotate-90'}">
          <Icon name="chevron-right" size={14} color="var(--color-cyber-text-dim)" />
        </span>
      {/if}
    </button>
  {/if}

  <div
    class="overflow-hidden transition-all duration-300 ease-in-out"
    style:display={collapsible && collapsed ? 'none' : 'block'}
  >
    <div class="p-4">
      {@render children()}
    </div>
  </div>
</div>