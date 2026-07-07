import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';

export type Breakpoint = 'mobile' | 'tablet' | 'desktop';

const MOBILE_MAX = 768;
const TABLET_MAX = 1024;

function detectBreakpoint(width: number): Breakpoint {
  if (width < MOBILE_MAX) return 'mobile';
  if (width < TABLET_MAX) return 'tablet';
  return 'desktop';
}

function getInitialWidth(): number {
  return browser ? window.innerWidth : 1024;
}

export const windowWidth = writable<number>(getInitialWidth());
export const breakpoint = derived<typeof windowWidth, Breakpoint>(windowWidth, ($width) =>
  detectBreakpoint($width)
);

export const isMobile = derived<typeof breakpoint, boolean>(breakpoint, ($bp) => $bp === 'mobile');
export const isTablet = derived<typeof breakpoint, boolean>(breakpoint, ($bp) => $bp === 'tablet');
export const isDesktop = derived<typeof breakpoint, boolean>(breakpoint, ($bp) => $bp === 'desktop');
export const isMobileOrTablet = derived<typeof breakpoint, boolean>(breakpoint, ($bp) => $bp === 'mobile' || $bp === 'tablet');

if (browser) {
  const onResize = () => windowWidth.set(window.innerWidth);
  window.addEventListener('resize', onResize);
  window.addEventListener('orientationchange', () => {
    setTimeout(onResize, 100);
  });
}