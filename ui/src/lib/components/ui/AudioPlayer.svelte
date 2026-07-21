<script lang="ts">
  let {
    src,
    label,
  }: {
    src: string;
    label?: string;
  } = $props();

  let audioEl = $state<HTMLAudioElement | null>(null);
  let waveformEl = $state<HTMLDivElement | null>(null);

  let isPlaying = $state(false);
  let currentTime = $state(0);
  let duration = $state(0);
  let ready = $state(false);
  let playbackRate = $state(1);
  let dragging = $state(false);

  const BAR_COUNT = 40;
  const SPEEDS = [1, 1.5, 2] as const;

  // Deterministic pseudo-waveform: hash the src string into a stable seed,
  // then generate bar heights in [0.2, 1.0] with a gentle envelope so the
  // ends taper off and the middle is louder — looks like a real clip.
  const bars = $derived.by(() => {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < src.length; i++) {
      h ^= src.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    const rand = () => {
      h ^= h << 13; h = h >>> 0;
      h ^= h >>> 17;
      h ^= h << 5; h = h >>> 0;
      return ((h >>> 0) % 100000) / 100000;
    };
    const out: number[] = [];
    for (let i = 0; i < BAR_COUNT; i++) {
      const t = i / (BAR_COUNT - 1);
      // triangular envelope: peak in the middle, taper at the edges
      const env = 0.45 + 0.55 * Math.sin(t * Math.PI);
      const noise = 0.55 + 0.45 * rand();
      out.push(Math.max(0.18, Math.min(1, env * noise)));
    }
    return out;
  });

  let progress = $derived(duration > 0 ? Math.min(1, currentTime / duration) : 0);
  let playheadBar = $derived(Math.floor(progress * BAR_COUNT));

  const SPEED_LABEL = $derived(`${playbackRate}x`);

  function formatTime(s: number): string {
    if (!isFinite(s) || s < 0) return '--:--';
    const total = Math.floor(s);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const sec = total % 60;
    const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
    const ss = String(sec).padStart(2, '0');
    return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  let playPromise: Promise<void> | null = null;

  function togglePlay() {
    const a = audioEl;
    if (!a) return;
    if (a.paused || a.ended) {
      // play() returns a Promise. If pause() or a new play() interrupts it,
      // the browser rejects with AbortError ("play() was interrupted by pause()").
      // Track the in-flight promise so we can guard against races and swallow
      // the expected AbortError instead of surfacing it as an uncaught promise.
      const p = a.play();
      if (p) {
        playPromise = p.then(() => { playPromise = null; }).catch((err) => {
          playPromise = null;
          if (err?.name !== 'AbortError') console.warn('Audio play failed:', err);
        });
      }
    } else {
      a.pause();
    }
  }

  function cycleSpeed() {
    const i = SPEEDS.indexOf(playbackRate as 1 | 1.5 | 2);
    const next = SPEEDS[(i + 1) % SPEEDS.length];
    playbackRate = next;
    if (audioEl) audioEl.playbackRate = next;
  }

  function seekToFraction(frac: number) {
    const a = audioEl;
    if (!a || !ready || duration <= 0) return;
    a.currentTime = Math.max(0, Math.min(duration, frac * duration));
    currentTime = a.currentTime;
  }

  function fracFromEvent(e: PointerEvent | MouseEvent): number {
    const el = waveformEl;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    const x = 'clientX' in e ? e.clientX : 0;
    return Math.max(0, Math.min(1, (x - rect.left) / rect.width));
  }

  function handleWaveformPointerDown(e: PointerEvent) {
    dragging = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    seekToFraction(fracFromEvent(e));
  }
  function handleWaveformPointerMove(e: PointerEvent) {
    if (!dragging) return;
    seekToFraction(fracFromEvent(e));
  }
  function handleWaveformPointerUp(e: PointerEvent) {
    if (!dragging) return;
    dragging = false;
      try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch {}
  }

  function handleKeydown(e: KeyboardEvent) {
    // Stop the event from bubbling to window so the global space-bar listener
    // in +page.svelte (which toggles voice recording) doesn't also fire when
    // the user activates this audio player via keyboard.
    e.stopPropagation();
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      togglePlay();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      seekToFraction(progress - 5 / Math.max(1, duration));
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      seekToFraction(progress + 5 / Math.max(1, duration));
    }
  }

  let rafId = 0;
  function tick() {
    if (audioEl) currentTime = audioEl.currentTime;
    rafId = requestAnimationFrame(tick);
  }
  function startRaf() {
    cancelRaf();
    rafId = requestAnimationFrame(tick);
  }
  function cancelRaf() {
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  }

  $effect(() => {
    const a = audioEl;
    if (!a) return;
    const onPlay = () => { isPlaying = true; startRaf(); };
    const onPause = () => { isPlaying = false; cancelRaf(); currentTime = a.currentTime; };
    const onEnded = () => { isPlaying = false; cancelRaf(); currentTime = 0; };
    const onLoaded = () => {
      duration = isFinite(a.duration) ? a.duration : 0;
      ready = true;
    };
    const onDuration = () => { duration = isFinite(a.duration) ? a.duration : 0; };
    a.addEventListener('play', onPlay);
    a.addEventListener('playing', onPlay);
    a.addEventListener('pause', onPause);
    a.addEventListener('ended', onEnded);
    a.addEventListener('loadedmetadata', onLoaded);
    a.addEventListener('durationchange', onDuration);
    return () => {
      a.removeEventListener('play', onPlay);
      a.removeEventListener('playing', onPlay);
      a.removeEventListener('pause', onPause);
      a.removeEventListener('ended', onEnded);
      a.removeEventListener('loadedmetadata', onLoaded);
      a.removeEventListener('durationchange', onDuration);
      cancelRaf();
      // Wait for any in-flight play() to settle before pausing; calling pause()
      // while play() is still resolving is what triggers AbortError.
      const p = playPromise;
      if (p) {
        p.finally(() => { try { a.pause(); } catch {} });
      } else {
        try { a.pause(); } catch {}
      }
    };
  });
</script>

<!-- svelte-ignore a11y_media_has_caption -->
<audio bind:this={audioEl} {src} preload="metadata" aria-hidden="true"></audio>

<div class="w-full max-w-[280px] rounded-xl border border-cyber-border/40 bg-cyber-surface-2/80 p-2 backdrop-blur-sm transition-colors duration-200 hover:border-cyber-cyan/30">
  {#if label}
    <div class="mb-1.5 truncate px-0.5 font-mono text-[10px] uppercase tracking-wider text-cyber-text-dim">
      {label}
    </div>
  {/if}

  <div class="flex items-center gap-2">
    <button
      type="button"
      onclick={togglePlay}
      onkeydown={handleKeydown}
      class="glow-cyan flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-cyber-cyan/40 bg-cyber-cyan/10 text-cyber-cyan transition-all duration-200 hover:border-cyber-cyan/70 hover:bg-cyber-cyan/20 focus:outline-none focus:ring-2 focus:ring-cyber-cyan/50 active:scale-95"
      aria-label={isPlaying ? 'Pause audio' : 'Play audio'}
    >
      {#if isPlaying}
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
          <path d="M6 4h4v16H6zM14 4h4v16h-4z" />
        </svg>
      {:else}
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
          <path d="M8 5v14l11-7z" />
        </svg>
      {/if}
    </button>

    <div
      bind:this={waveformEl}
      onpointerdown={handleWaveformPointerDown}
      onpointermove={handleWaveformPointerMove}
      onpointerup={handleWaveformPointerUp}
      onpointercancel={handleWaveformPointerUp}
      class="relative flex h-10 flex-1 cursor-pointer items-center gap-[2px] rounded-md px-1"
      role="slider"
      tabindex="-1"
      aria-label="Seek audio"
      aria-valuemin="0"
      aria-valuemax={Math.floor(duration)}
      aria-valuenow={Math.floor(currentTime)}
    >
      {#each bars as h, i}
        <div
          class="flex-1 rounded-full transition-colors duration-150 {i < playheadBar
            ? 'bg-cyber-cyan'
            : 'bg-cyber-text-dim/30'} {i === playheadBar && isPlaying ? 'animate-pulse-glow' : ''}"
          style="height: {Math.round(h * 100)}%; {i < playheadBar ? 'box-shadow: 0 0 4px rgba(0,212,255,0.45);' : ''}"
        ></div>
      {/each}
    </div>
  </div>

  <div class="mt-1.5 flex items-center justify-between px-0.5">
    <span class="font-mono text-[10px] tabular-nums text-cyber-text-dim">
      {formatTime(currentTime)}<span class="mx-0.5 opacity-50">/</span>{formatTime(duration)}
    </span>
    <button
      type="button"
      onclick={cycleSpeed}
      class="rounded px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-cyber-text-dim transition-colors hover:text-cyber-cyan focus:outline-none focus:ring-1 focus:ring-cyber-cyan/40"
      aria-label="Cycle playback speed, currently {SPEED_LABEL}"
    >
      {SPEED_LABEL}
    </button>
  </div>
</div>