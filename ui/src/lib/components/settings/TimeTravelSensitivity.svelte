<script lang="ts">
  import { configStore } from '$lib/stores/config.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';

  const MIN = 0.1;
  const MAX = 2.0;
  const STEP = 0.05;
  const DEFAULT = 1.0;

  let saving = $state(false);
  let saveStatus = $state<'idle' | 'saved' | 'error'>('idle');
  let saveTimer: ReturnType<typeof setTimeout> | null = null;

  let draft = $state(configStore.pinchZoomSensitivity);

  $effect(() => {
    void configStore.pinchZoomSensitivity;
    draft = configStore.pinchZoomSensitivity;
  });

  function labelFor(v: number): string {
    if (v <= 0.3) return 'Very slow';
    if (v < 0.7) return 'Slower';
    if (v <= 1.2) return 'Default';
    if (v < 1.6) return 'Fast';
    return 'Very fast';
  }

  async function persist(v: number) {
    if (saveTimer) clearTimeout(saveTimer);
    saving = true;
    saveStatus = 'idle';
    saveTimer = setTimeout(async () => {
      const ok = await configStore.savePinchZoomSensitivity(v);
      saving = false;
      if (ok) {
        saveStatus = 'saved';
        setTimeout(() => { saveStatus = 'idle'; }, 2000);
      } else {
        saveStatus = 'error';
        setTimeout(() => { saveStatus = 'idle'; }, 2500);
      }
    }, 400);
  }

  function onInput(e: Event) {
    const v = Number((e.target as HTMLInputElement).value);
    draft = v;
    persist(v);
  }
</script>

<div class="rounded-xl border border-cyber-border/40 bg-cyber-surface/85 backdrop-blur-xl overflow-hidden">
  <div class="flex items-center gap-2 px-3 py-2.5">
    <div class="flex h-6 w-6 items-center justify-center rounded-md bg-cyber-cyan/10">
      <Icon name="clock" size={14} color="var(--color-cyber-cyan)" />
    </div>
    <div class="flex-1 text-left">
      <div class="text-xs font-medium text-cyber-text">Time Travel Sensitivity</div>
      <div class="text-[10px] text-cyber-text-dim">
        How fast pinching scrubs through time · {labelFor(draft)}
      </div>
    </div>
    <div class="flex items-center gap-2">
      {#if saving}
        <span class="text-[10px] text-cyber-text-dim">Saving…</span>
      {:else if saveStatus === 'saved'}
        <span class="text-[10px] text-cyber-green">Saved</span>
      {:else if saveStatus === 'error'}
        <span class="text-[10px] text-cyber-red">Save failed</span>
      {/if}
      {#if Math.abs(draft - DEFAULT) > 0.001}
        <button
          onclick={() => onInput({ target: { value: String(DEFAULT) } } as unknown as Event)}
          class="text-[10px] text-cyber-cyan/80 hover:text-cyber-cyan underline-offset-2 hover:underline"
          aria-label="Reset to default sensitivity"
        >Reset</button>
      {/if}
      <span class="text-[11px] font-mono tabular-nums text-cyber-text-dim">{draft.toFixed(2)}×</span>
    </div>
  </div>
  <div class="px-3 pb-3 pt-1">
    <input
      type="range"
      min={MIN}
      max={MAX}
      step={STEP}
      value={draft}
      oninput={onInput}
      class="sensitivity-range w-full"
      aria-label="Time travel sensitivity"
    />
    <div class="mt-1 flex justify-between text-[9px] uppercase tracking-wider text-cyber-text-dim/70">
      <span>0.1× slower</span>
      <span>0.5× default</span>
      <span>2× faster</span>
    </div>
  </div>
</div>

<style>
  .sensitivity-range {
    -webkit-appearance: none;
    appearance: none;
    height: 4px;
    border-radius: 2px;
    background: rgba(200, 214, 229, 0.15);
    outline: none;
  }
  .sensitivity-range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--color-cyber-cyan, #00d4ff);
    border: 2px solid rgba(10, 14, 23, 0.8);
    box-shadow: 0 0 6px rgba(0, 212, 255, 0.5);
    cursor: pointer;
    transition: transform 0.1s ease;
  }
  .sensitivity-range::-webkit-slider-thumb:active {
    transform: scale(1.15);
  }
  .sensitivity-range::-moz-range-thumb {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--color-cyber-cyan, #00d4ff);
    border: 2px solid rgba(10, 14, 23, 0.8);
    box-shadow: 0 0 6px rgba(0, 212, 255, 0.5);
    cursor: pointer;
  }
  .sensitivity-range::-moz-range-track {
    height: 4px;
    border-radius: 2px;
    background: rgba(200, 214, 229, 0.15);
  }
</style>