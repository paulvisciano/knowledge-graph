<script lang="ts">
  import { configStore } from '$lib/stores/config.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';

  let saving = $state(false);
  let saveStatus = $state<'idle' | 'saved' | 'error'>('idle');

  async function toggle() {
    if (saving) return;
    saving = true;
    const next = !configStore.faceDetectionEnabled;
    const ok = await configStore.saveFaceDetection(next);
    saving = false;
    if (ok) {
      saveStatus = 'saved';
      setTimeout(() => { saveStatus = 'idle'; }, 2000);
    } else {
      saveStatus = 'error';
      setTimeout(() => { saveStatus = 'idle'; }, 2500);
    }
  }
</script>

<div class="rounded-xl border border-cyber-border/40 bg-cyber-surface/85 backdrop-blur-xl overflow-hidden">
  <button
    onclick={toggle}
    disabled={saving}
    class="flex w-full items-center justify-between px-3 py-2.5 transition-colors hover:bg-cyber-surface-2/50 disabled:opacity-60 disabled:cursor-not-allowed"
  >
    <div class="flex items-center gap-2">
      <div class="flex h-6 w-6 items-center justify-center rounded-md bg-cyber-cyan/10">
        <Icon name="image" size={14} color="var(--color-cyber-cyan)" />
      </div>
      <div class="text-left">
        <div class="text-xs font-medium text-cyber-text">Face Detection</div>
        <div class="text-[10px] text-cyber-text-dim">
          {configStore.faceDetectionEnabled ? 'Enabled — analyzes people in photos' : 'Disabled — skips face recognition'}
        </div>
      </div>
    </div>
    <div class="flex items-center gap-2">
      {#if saveStatus === 'saved'}
        <span class="text-[10px] text-cyber-green">Saved</span>
      {:else if saveStatus === 'error'}
        <span class="text-[10px] text-cyber-red">Save failed</span>
      {/if}
      <span
        class="relative inline-flex h-5 w-9 items-center rounded-full border transition-colors duration-200 {configStore.faceDetectionEnabled ? 'bg-cyber-cyan/70 border-cyber-cyan' : 'bg-cyber-surface-2 border-cyber-border/40'}"
        role="switch"
        aria-checked={configStore.faceDetectionEnabled}
        aria-label="Toggle face detection"
      >
        <span
          class="inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform duration-200 {configStore.faceDetectionEnabled ? 'translate-x-4' : 'translate-x-0.5'}"
        ></span>
      </span>
    </div>
  </button>
</div>