import { browser } from '$app/environment';

interface SheetDragOptions {
  sheet: HTMLElement;
  onDismiss: () => void;
  dismissThreshold?: number;
  velocityThreshold?: number;
}

/**
 * Native-feeling bottom-sheet drag-to-dismiss. During a drag the sheet
 * translates 1:1 with the finger (clamped so it never moves above its rest
 * position). On release the sheet snaps open or closed based on how far it
 * was dragged and how fast, matching iOS/Android sheet behaviour.
 *
 * The drag is ignored when started on a vertically-scrollable region that
 * isn't already scrolled to its top, so scrolling the message thread doesn't
 * accidentally trigger a dismiss.
 */
export function createSheetDrag(options: SheetDragOptions) {
  if (!browser) return { destroy: () => {} };

  const { sheet, onDismiss } = options;
  const dismissThreshold = options.dismissThreshold ?? 100;
  const velocityThreshold = options.velocityThreshold ?? 0.5;

  let startY = 0;
  let currentY = 0;
  let startTime = 0;
  let dragging = false;
  let scrollable: HTMLElement | null = null;
  let scrollableAtTop = false;

  function isScrollableAtTop(el: HTMLElement): boolean {
    return el.scrollTop <= 1;
  }

  function findScrollableAncestor(target: EventTarget | null): HTMLElement | null {
    let node = target as HTMLElement | null;
    while (node && node !== sheet) {
      const style = getComputedStyle(node);
      if (
        (style.overflowY === 'auto' || style.overflowY === 'scroll') &&
        node.scrollHeight > node.clientHeight
      ) {
        return node;
      }
      node = node.parentElement;
    }
    return null;
  }

  function onTouchStart(e: TouchEvent) {
    if (e.touches.length !== 1) return;
    startY = e.touches[0].clientY;
    currentY = startY;
    startTime = Date.now();
    scrollable = findScrollableAncestor(e.target);
    scrollableAtTop = scrollable ? isScrollableAtTop(scrollable) : true;
    dragging = scrollableAtTop;
  }

  function onTouchMove(e: TouchEvent) {
    if (!dragging || e.touches.length !== 1) return;
    currentY = e.touches[0].clientY;
    const dy = currentY - startY;
    if (dy <= 0) {
      sheet.style.transform = '';
      return;
    }
    if (scrollable && !isScrollableAtTop(scrollable)) {
      dragging = false;
      sheet.style.transform = '';
      return;
    }
    e.preventDefault();
    sheet.classList.add('sheet-dragging');
    sheet.style.transform = `translateY(${dy}px)`;
  }

  function onTouchEnd() {
    if (!dragging) {
      cleanup();
      return;
    }
    const dy = currentY - startY;
    const dt = Date.now() - startTime;
    const velocity = dy / Math.max(dt, 1);
    const shouldDismiss = dy > dismissThreshold || (dy > 40 && velocity > velocityThreshold);
    cleanup();
    if (shouldDismiss) {
      onDismiss();
    }
  }

  function cleanup() {
    dragging = false;
    sheet.classList.remove('sheet-dragging');
    sheet.style.transform = '';
    scrollable = null;
  }

  sheet.addEventListener('touchstart', onTouchStart, { passive: true });
  sheet.addEventListener('touchmove', onTouchMove, { passive: false });
  sheet.addEventListener('touchend', onTouchEnd, { passive: true });
  sheet.addEventListener('touchcancel', onTouchEnd, { passive: true });

  return {
    destroy() {
      sheet.removeEventListener('touchstart', onTouchStart);
      sheet.removeEventListener('touchmove', onTouchMove);
      sheet.removeEventListener('touchend', onTouchEnd);
      sheet.removeEventListener('touchcancel', onTouchEnd);
    },
  };
}