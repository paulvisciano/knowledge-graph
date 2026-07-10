import { test, expect, type Page } from '@playwright/test';

/**
 * Shared helpers for the image-processing E2E workflow.
 */

/** Wait for the 3D force graph canvas to be mounted and rendering. */
export async function waitForGraphReady(page: Page) {
  const canvas = page.getByTestId('graph-canvas');
  await expect(canvas).toBeAttached({ timeout: 30_000 });
  // Give the graph a moment to finish its initial load + layout tick
  await page.waitForTimeout(2_000);
}

/** Open the attachment menu and pick "Image" to trigger the hidden file input. */
export async function openImagePicker(page: Page) {
  await page.getByTestId('attach-file-button').click();
  await page.getByTestId('pick-image-button').click();
}

/** Upload an image file through the hidden file input. Returns the file input element. */
export async function uploadImage(page: Page, filePath: string) {
  const fileInput = page.getByTestId('image-file-input');
  await fileInput.setInputFiles(filePath);
  return fileInput;
}

/**
 * Wait for an SSE event to appear in the activity feed.
 * The activity feed shows events with their names (e.g. "photo node created").
 */
export async function waitForActivityEvent(page: Page, eventName: string, timeout = 60_000) {
  const activityFeed = page.getByTestId('activity-feed');
  await expect(
    activityFeed.getByText(eventName, { exact: false })
  ).toBeVisible({ timeout });
}

/** Wait for a specific number of nodes to appear in the graph store. */
export async function waitForNodeCount(page: Page, minCount: number, timeout = 60_000) {
  await page.waitForFunction(
    (count: number) => {
      // GraphStore is accessed via the window for testing
      const el = document.querySelector('[data-testid="graph-view"]');
      if (!el) return false;
      // Check the Svelte context — we expose graphStore via window for E2E
      const store = (window as any).__graphStore;
      if (!store) return false;
      return store.nodes.length >= count;
    },
    minCount,
    { timeout }
  );
}

/** Wait for a node with a specific label to exist in the graph. */
export async function waitForNodeWithLabel(page: Page, label: string, timeout = 60_000) {
  await page.waitForFunction(
    (lbl: string) => {
      const store = (window as any).__graphStore;
      if (!store) return false;
      return store.nodes.some((n: any) =>
        n.labels?.some((l: string) => l.toLowerCase() === lbl.toLowerCase())
      );
    },
    label,
    { timeout }
  );
}

/** Wait for a node whose ID contains a substring (e.g. "Photo" or "Date"). */
export async function waitForNodeWithId(page: Page, idSubstring: string, timeout = 60_000) {
  await page.waitForFunction(
    (substr: string) => {
      const store = (window as any).__graphStore;
      if (!store) return false;
      return store.nodes.some((n: any) => n.id.includes(substr));
    },
    idSubstring,
    { timeout }
  );
}

/** Wait for an edge to exist between two node ID substrings. */
export async function waitForEdge(page: Page, sourceSubstring: string, targetSubstring: string, timeout = 30_000) {
  await page.waitForFunction(
    ({ src, tgt }: { src: string; tgt: string }) => {
      const store = (window as any).__graphStore;
      if (!store) return false;
      return store.edges.some(
        (e: any) => e.source.includes(src) && e.target.includes(tgt)
      );
    },
    { src: sourceSubstring, tgt: targetSubstring },
    { timeout }
  );
}

/** Wait for the image processing pipeline to reach a given stage for a node. */
export async function waitForProcessingStage(
  page: Page,
  nodeIdSubstring: string,
  stage: string,
  timeout = 60_000
) {
  await page.waitForFunction(
    ({ idSub, stg }: { idSub: string; stg: string }) => {
      const store = (window as any).__imageProcessingStore;
      if (!store) return false;
      const entry = Object.values(store.statuses).find(
        (s: any) => s.nodeId.includes(idSub) || s.fileName.includes(idSub)
      );
      if (!entry) return false;
      return (entry as any).stage === stg;
    },
    { idSub: nodeIdSubstring, stg: stage },
    { timeout }
  );
}

/** Reset the graph by navigating to the page fresh. */
export async function resetPage(page: Page) {
  await page.goto('/');
  await waitForGraphReady(page);
}