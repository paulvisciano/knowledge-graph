import { browser } from '$app/environment';

interface SwipeHandlerOptions {
  element: HTMLElement;
  onSwipeLeft?: () => void;
  onSwipeRight?: () => void;
  onSwipeUp?: () => void;
  onSwipeDown?: () => void;
  threshold?: number;
  velocityThreshold?: number;
}

export function createSwipeHandler(options: SwipeHandlerOptions) {
  if (!browser) return { destroy: () => {} };

  const {
    element,
    onSwipeLeft,
    onSwipeRight,
    onSwipeUp,
    onSwipeDown,
    threshold = 50,
    velocityThreshold = 0.3,
  } = options;

  let startX = 0;
  let startY = 0;
  let startTime = 0;
  let tracking = false;

  function onTouchStart(e: TouchEvent) {
    const touch = e.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
    startTime = Date.now();
    tracking = true;
  }

  function onTouchEnd(e: TouchEvent) {
    if (!tracking) return;
    tracking = false;

    const touch = e.changedTouches[0];
    const dx = touch.clientX - startX;
    const dy = touch.clientY - startY;
    const dt = Date.now() - startTime;
    const velocity = Math.sqrt(dx * dx + dy * dy) / Math.max(dt, 1);
    const absDx = Math.abs(dx);
    const absDy = Math.abs(dy);

    if (absDx > absDy && absDx > threshold && (velocity > velocityThreshold || absDx > threshold * 2)) {
      if (dx > 0 && onSwipeRight) onSwipeRight();
      if (dx < 0 && onSwipeLeft) onSwipeLeft();
    } else if (absDy > absDx && absDy > threshold && (velocity > velocityThreshold || absDy > threshold * 2)) {
      if (dy > 0 && onSwipeDown) onSwipeDown();
      if (dy < 0 && onSwipeUp) onSwipeUp();
    }
  }

  function onTouchMove() {}

  element.addEventListener('touchstart', onTouchStart, { passive: true });
  element.addEventListener('touchend', onTouchEnd, { passive: true });
  element.addEventListener('touchmove', onTouchMove, { passive: true });

  return {
    destroy() {
      element.removeEventListener('touchstart', onTouchStart);
      element.removeEventListener('touchend', onTouchEnd);
      element.removeEventListener('touchmove', onTouchMove);
    }
  };
}