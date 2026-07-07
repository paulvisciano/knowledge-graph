<script lang="ts">
  import { configStore } from '$lib/stores/config.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';

  let expanded = $state(false);
  let editing = $state(false);
  let draft = $state('');
  let saving = $state(false);
  let saveStatus = $state<'idle' | 'saved' | 'error'>('idle');

  function startEdit() {
    draft = configStore.systemPrompt;
    editing = true;
    saveStatus = 'idle';
  }

  function cancelEdit() {
    editing = false;
    draft = '';
    saveStatus = 'idle';
  }

  async function saveEdit() {
    saving = true;
    const ok = await configStore.save(draft);
    saving = false;
    if (ok) {
      saveStatus = 'saved';
      editing = false;
      setTimeout(() => { saveStatus = 'idle'; }, 2000);
    } else {
      saveStatus = 'error';
    }
  }

  function resetToDefault() {
    configStore.resetToDefault();
    draft = configStore.systemPrompt;
    saveStatus = 'idle';
  }

  const charCount = $derived(configStore.systemPrompt.length);
  const lineCount = $derived(configStore.systemPrompt.split('\n').length);
</script>

<div class="rounded-xl border border-cyber-border/40 bg-cyber-surface/85 backdrop-blur-xl overflow-hidden">
  <button
    onclick={() => (expanded = !expanded)}
    class="flex w-full items-center justify-between px-3 py-2.5 transition-colors hover:bg-cyber-surface-2/50"
  >
    <div class="flex items-center gap-2">
      <div class="flex h-6 w-6 items-center justify-center rounded-md bg-cyber-cyan/10">
        <Icon name="terminal" size={14} color="var(--color-cyber-cyan)" />
      </div>
      <div class="text-left">
        <div class="text-xs font-medium text-cyber-text">System Prompt</div>
        <div class="text-[10px] text-cyber-text-dim">{lineCount} lines · {charCount} chars</div>
      </div>
    </div>
    <div class="flex items-center gap-2">
      {#if saveStatus === 'saved'}
        <span class="text-[10px] text-cyber-green">Saved</span>
      {:else if saveStatus === 'error'}
        <span class="text-[10px] text-cyber-red">Save failed</span>
      {/if}
      <svg class="h-4 w-4 text-cyber-text-dim transition-transform duration-200 {expanded ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
    </div>
  </button>

  {#if expanded}
    <div class="border-t border-cyber-border/30 px-3 py-2">
      {#if editing}
        <textarea
          bind:value={draft}
          class="w-full h-64 rounded-lg border border-cyber-border bg-cyber-bg/80 px-3 py-2 font-mono text-[11px] text-cyber-text resize-y focus:border-cyber-cyan/50 focus:outline-none focus:ring-1 focus:ring-cyber-cyan/30"
          placeholder="Enter system prompt..."
        ></textarea>
        <div class="flex items-center gap-2 mt-2">
          <button
            onclick={saveEdit}
            disabled={saving}
            class="rounded-lg bg-cyber-cyan/15 px-3 py-1.5 text-xs font-medium text-cyber-cyan transition-all hover:bg-cyber-cyan/25 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
          <button
            onclick={cancelEdit}
            class="rounded-lg bg-cyber-surface-2 px-3 py-1.5 text-xs font-medium text-cyber-text-dim transition-all hover:bg-cyber-border/50"
          >
            Cancel
          </button>
          <button
            onclick={resetToDefault}
            class="ml-auto rounded-lg px-3 py-1.5 text-xs font-medium text-cyber-text-dim/60 transition-all hover:text-cyber-red hover:bg-cyber-red/10"
          >
            Reset to default
          </button>
        </div>
      {:else}
        <div class="max-h-48 overflow-y-auto rounded-lg bg-cyber-bg/60 px-3 py-2 font-mono text-[11px] text-cyber-text-dim leading-relaxed whitespace-pre-wrap break-words">
          {configStore.systemPrompt}
        </div>
        <div class="flex items-center gap-2 mt-2">
          <button
            onclick={startEdit}
            class="rounded-lg bg-cyber-cyan/15 px-3 py-1.5 text-xs font-medium text-cyber-cyan transition-all hover:bg-cyber-cyan/25"
          >
            Edit
          </button>
          <button
            onclick={resetToDefault}
            class="rounded-lg px-3 py-1.5 text-xs font-medium text-cyber-text-dim/60 transition-all hover:text-cyber-red hover:bg-cyber-red/10"
          >
            Reset to default
          </button>
        </div>
      {/if}
    </div>
  {/if}
</div>