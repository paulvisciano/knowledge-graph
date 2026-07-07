<script lang="ts">
  import { mcpClient } from '$lib/services/mcp-client.svelte';
  import { connectionStore } from '$lib/stores/connection.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';

  let expanded = $state(false);

  async function handleConnect() {
    await connectionStore.connectMcp();
  }

  async function handleDisconnect() {
    await connectionStore.disconnectMcp();
  }

  function toggleTool(name: string, enabled: boolean) {
    mcpClient.toggleTool(name, enabled);
  }
</script>

<div class="rounded-xl border border-cyber-border/40 bg-cyber-surface/85 backdrop-blur-xl overflow-hidden">
  <button
    onclick={() => (expanded = !expanded)}
    class="flex w-full items-center justify-between px-3 py-2.5 transition-colors hover:bg-cyber-surface-2/50"
  >
    <div class="flex items-center gap-2">
      <div class="flex h-6 w-6 items-center justify-center rounded-md {mcpClient.isConnected ? 'bg-cyber-purple/15' : 'bg-cyber-surface-2/80'}">
        <Icon name="zap" size={14} color={mcpClient.isConnected ? 'var(--color-cyber-purple)' : 'var(--color-cyber-text-dim)'} />
      </div>
      <div class="text-left">
        <div class="text-xs font-medium text-cyber-text">MCP Tools</div>
        <div class="text-[10px] text-cyber-text-dim">
          {#if mcpClient.isConnecting}
            Connecting...
          {:else if mcpClient.isConnected}
            {mcpClient.enabledTools.length}/{mcpClient.tools.length} enabled
          {:else if mcpClient.error}
            <span class="text-cyber-red">{mcpClient.error.slice(0, 40)}</span>
          {:else}
            Not connected
          {/if}
        </div>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <div class="h-2 w-2 rounded-full {mcpClient.isConnected ? 'bg-cyber-green animate-pulse-glow' : mcpClient.isConnecting ? 'bg-cyber-orange animate-pulse' : 'bg-cyber-text-dim/30'}"></div>
      <svg class="h-4 w-4 text-cyber-text-dim transition-transform duration-200 {expanded ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
    </div>
  </button>

  {#if expanded}
    <div class="border-t border-cyber-border/30 px-3 py-2">
      {#if !mcpClient.isConnected}
        <button
          onclick={handleConnect}
          disabled={mcpClient.isConnecting}
          class="w-full rounded-lg bg-cyber-purple/15 px-3 py-2 text-xs font-medium text-cyber-purple transition-all hover:bg-cyber-purple/25 disabled:opacity-50"
        >
          {mcpClient.isConnecting ? 'Connecting...' : 'Connect to MCP Server'}
        </button>
        {#if mcpClient.error}
          <p class="mt-2 text-[10px] text-cyber-red">{mcpClient.error}</p>
        {/if}
      {:else}
        <div class="flex items-center gap-2 mb-2">
          <span class="text-[10px] text-cyber-text-dim">
            {mcpClient.tools.length} tools available
          </span>
          <div class="flex gap-1 ml-auto">
            <button
              onclick={() => mcpClient.enableAllTools()}
              class="rounded px-1.5 py-0.5 text-[10px] text-cyber-cyan hover:bg-cyber-cyan/10"
            >All</button>
            <button
              onclick={() => mcpClient.disableAllTools()}
              class="rounded px-1.5 py-0.5 text-[10px] text-cyber-text-dim hover:bg-cyber-surface-2"
            >None</button>
          </div>
          <button
            onclick={handleDisconnect}
            class="rounded px-1.5 py-0.5 text-[10px] text-cyber-red hover:bg-cyber-red/10"
          >Disconnect</button>
        </div>

        <div class="max-h-64 overflow-y-auto space-y-1">
          {#each mcpClient.tools as tool (tool.name)}
            <label class="group flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-cyber-surface-2/40 cursor-pointer">
              <input
                type="checkbox"
                checked={tool.enabled}
                onchange={() => toggleTool(tool.name, !tool.enabled)}
                class="h-3 w-3 rounded border-cyber-border bg-cyber-surface-2 text-cyber-purple accent-cyber-purple"
              />
              <div class="min-w-0 flex-1">
                <div class="truncate font-mono text-[11px] text-cyber-text group-hover:text-cyber-purple">{tool.name}</div>
                {#if tool.description}
                  <div class="truncate text-[10px] text-cyber-text-dim/60">{tool.description.slice(0, 80)}</div>
                {/if}
              </div>
            </label>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>