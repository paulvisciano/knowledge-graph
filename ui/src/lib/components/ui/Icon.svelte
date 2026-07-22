<script lang="ts">
  let { name, size = 20, color = 'currentColor' }: { name: string; size?: number; color?: string } = $props();

  type IconDef = { path: string; viewBox?: string; fill?: boolean; stroke?: boolean };

  const icons: Record<string, IconDef> = {
    send: { path: 'M2.01 21L23 12 2.01 3 2 10l15 2-15 2z', viewBox: '0 0 24 24', fill: true },
    search: { path: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z', viewBox: '0 0 24 24' },
    database: { path: 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4', viewBox: '0 0 24 24' },
    graph: { path: 'M18 20V10M12 20V4M6 20v-6', viewBox: '0 0 24 24' },
    document: { path: 'M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z M14 2v6h6', viewBox: '0 0 24 24' },
    plus: { path: 'M12 5v14m-7-7h14', viewBox: '0 0 24 24' },
    x: { path: 'M18 6L6 18M6 6l12 12', viewBox: '0 0 24 24' },
    'chevron-down': { path: 'M6 9l6 6 6-6', viewBox: '0 0 24 24' },
    'chevron-right': { path: 'M9 18l6-6-6-6', viewBox: '0 0 24 24' },
    upload: { path: 'M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12', viewBox: '0 0 24 24' },
    'refresh-cw': { path: 'M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15', viewBox: '0 0 24 24' },
    'trash-2': { path: 'M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2M10 11v6M14 11v6', viewBox: '0 0 24 24' },
    check: { path: 'M20 6L9 17l-5-5', viewBox: '0 0 24 24' },
    'alert-circle': { path: 'M12 8v4m0 4h.01M22 12a10 10 0 11-20 0 10 10 0 0120 0z', viewBox: '0 0 24 24' },
    settings: { path: 'M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z', viewBox: '0 0 24 24' },
    menu: { path: 'M3 12h18M3 6h18M3 18h18', viewBox: '0 0 24 24' },
    sidebar: { path: 'M3 3h18v18H3zM9 3v18', viewBox: '0 0 24 24' },
    'maximize-2': { path: 'M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7', viewBox: '0 0 24 24' },
    'minimize-2': { path: 'M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7', viewBox: '0 0 24 24' },
    eye: { path: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z', viewBox: '0 0 24 24' },
    cpu: { path: 'M6 3v3M6 18v3M18 3v3M18 18v3M3 6h3M18 6h3M3 18h3M18 18h3M6 6h12v12H6z', viewBox: '0 0 24 24' },
    zap: { path: 'M13 2L3 14h9l-1 8 10-12h-9l1-8z', viewBox: '0 0 24 24', fill: true },
    globe: { path: 'M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20M12 2a15 15 0 014 10 15 15 0 01-4 10 15 15 0 01-4-10A15 15 0 0112 2z', viewBox: '0 0 24 24' },
    link: { path: 'M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71', viewBox: '0 0 24 24' },
    'file-text': { path: 'M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z M14 2v6h6M16 13H8M16 17H8M10 9H8', viewBox: '0 0 24 24' },
    image: { path: 'M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zM8.5 10a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM21 15l-5-5L5 21', viewBox: '0 0 24 24' },
    mic: { path: 'M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8', viewBox: '0 0 24 24' },
    'mic-off': { path: 'M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM1 10v2a7 7 0 0014 0v-2M12 19v4M8 23h8', viewBox: '0 0 24 24' },
    square: { path: 'M6 6h12v12H6z', viewBox: '0 0 24 24', fill: true },
    play: { path: 'M8 5v14l11-7z', viewBox: '0 0 24 24', fill: true },
    pause: { path: 'M6 4h4v16H6zM14 4h4v16h-4z', viewBox: '0 0 24 24', fill: true },
    download: { path: 'M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3', viewBox: '0 0 24 24' },
    clock: { path: 'M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zM12 6v6l4 2', viewBox: '0 0 24 24' },
  };

  let icon = $derived(icons[name] ?? icons['alert-circle']);
</script>

<svg
  xmlns="http://www.w3.org/2000/svg"
  width={size}
  height={size}
  viewBox={icon.viewBox ?? '0 0 24 24'}
  fill={icon.fill ? color : 'none'}
  stroke={icon.stroke !== false && !icon.fill ? color : 'none'}
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
  class="shrink-0"
>
  <path d={icon.path} />
</svg>