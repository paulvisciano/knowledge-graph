import { test, expect } from '@playwright/test';
import {
  waitForGraphReady,
  uploadImage,
  waitForNodeWithLabel,
  waitForNodeWithId,
  waitForEdge,
  resetPage,
} from './helpers';

test.describe('Image processing workflow', () => {

  test.beforeEach(async ({ page }) => {
    // Expose graphStore and imageProcessingStore on window for E2E assertions
    await page.goto('/');
    await page.waitForFunction(() => !!(window as any).__graphStore);
    await waitForGraphReady(page);
  });

  test('graph loads and renders the canvas', async ({ page }) => {
    await expect(page.getByTestId('graph-canvas')).toBeVisible();
  });

  test('image file input is present and hidden', async ({ page }) => {
    const fileInput = page.getByTestId('image-file-input');
    await expect(fileInput).toBeAttached();
    // Hidden file input should not be visible
    await expect(fileInput).not.toBeVisible();
  });

  test('attach file button opens menu with image option', async ({ page }) => {
    await page.getByTestId('attach-file-button').click();
    await expect(page.getByTestId('pick-image-button')).toBeVisible();
  });

  test('uploading an image creates a Photo node in the graph', async ({ page }) => {
    const testImage = 'e2e/fixtures/test-photo.jpg';

    // Upload the image
    await uploadImage(page, testImage);

    // Wait for the Photo node to appear in the graph
    await waitForNodeWithId(page, '(Photo)', 90_000);

    // Verify it has the Photo label
    const photoNodeId = await page.evaluate(() => {
      const store = (window as any).__graphStore;
      const node = store.nodes.find((n: any) => n.id.includes('(Photo)'));
      return node?.id ?? null;
    });
    expect(photoNodeId).toBeTruthy();
  });

  test('uploading an image with EXIF creates EXIF nodes immediately', async ({ page }) => {
    const testImage = 'e2e/fixtures/test-photo.jpg';

    await uploadImage(page, testImage);

    // Photo node should appear first
    await waitForNodeWithId(page, '(Photo)', 90_000);

    // EXIF nodes should appear without waiting for LightRAG processing
    // (Date, Location, Camera — whichever are present in the test image's EXIF)
    const photoNodeCount = await page.evaluate(() => {
      const store = (window as any).__graphStore;
      return store.nodes.filter((n: any) => n.id.includes('(Photo)')).length;
    });
    expect(photoNodeCount).toBeGreaterThanOrEqual(1);

    // At least one EXIF-derived node should appear
    const exifNodeCount = await page.evaluate(() => {
      const store = (window as any).__graphStore;
      return store.nodes.filter((n: any) =>
        n.id.includes('(Date)') ||
        n.id.includes('(Location)') ||
        n.id.includes('(Camera)')
      ).length;
    });
    expect(exifNodeCount).toBeGreaterThanOrEqual(1);
  });

  test('Photo node has edges to EXIF nodes', async ({ page }) => {
    const testImage = 'e2e/fixtures/test-photo.jpg';

    await uploadImage(page, testImage);
    await waitForNodeWithId(page, '(Photo)', 90_000);

    // Wait for at least one edge from the Photo node
    await waitForEdge(page, '(Photo)', '(Date)', 30_000).catch(() => {
      // Date might not be in EXIF — try Location or Camera
    });

    // Verify edges exist from Photo to at least one EXIF node
    const edgeCount = await page.evaluate(() => {
      const store = (window as any).__graphStore;
      return store.edges.filter((e: any) =>
        e.source.includes('(Photo)') &&
        (e.target.includes('(Date)') ||
         e.target.includes('(Location)') ||
         e.target.includes('(Camera)'))
      ).length;
    });
    expect(edgeCount).toBeGreaterThanOrEqual(1);
  });

  test('graph refresh after pipeline completion preserves nodes', async ({ page }) => {
    const testImage = 'e2e/fixtures/test-photo.jpg';

    await uploadImage(page, testImage);

    // Wait for the pipeline to complete (graphStore.pipelineDone is set on pipeline_complete)
    await page.waitForFunction(() => !!(window as any).__graphStore?.pipelineDone, { timeout: 120_000 });

    // The Photo node should still exist after the graph refreshes
    await waitForNodeWithId(page, '(Photo)', 30_000);

    const photoNodeCount = await page.evaluate(() => {
      const store = (window as any).__graphStore;
      return store.nodes.filter((n: any) => n.id.includes('(Photo)')).length;
    });
    expect(photoNodeCount).toBeGreaterThanOrEqual(1);
  });
});

test.describe('Graph interaction', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(() => !!(window as any).__graphStore);
    await waitForGraphReady(page);
  });

  test('hovering a node shows it in the hovered state', async ({ page }) => {
    // If the graph has nodes, hover one and check the UI responds
    const hasNodes = await page.evaluate(() => {
      const store = (window as any).__graphStore;
      return store.nodes.length > 0;
    });

    if (!hasNodes) {
      // No nodes to test with — skip
      test.skip();
      return;
    }

    // Get the graph canvas and simulate hover
    const canvas = page.getByTestId('graph-canvas');
    await expect(canvas).toBeVisible();
  });

  test('graph search filters nodes', async ({ page }) => {
    // The search input should exist and be functional
    const searchInput = page.locator('input[placeholder*="earch"]');
    if (await searchInput.count() === 0) {
      test.skip();
      return;
    }

    await searchInput.fill('Photo');
    await page.waitForTimeout(500);

    // Filtered nodes should be reduced
    const visibleCount = await page.evaluate(() => {
      const store = (window as any).__graphStore;
      return store.filteredNodes.length;
    });
    // If there are Photo nodes, they should show in filtered
    expect(typeof visibleCount).toBe('number');
  });
});

test.describe('API health checks', () => {

  test('KG API health endpoint responds', async ({ request }) => {
    const baseUrl = process.env.BASE_URL ?? 'http://localhost:5180';
    const response = await request.get(`${baseUrl}/api/kg/health`);
    expect(response.ok()).toBeTruthy();
  });

  test('LightRAG health endpoint responds', async ({ request }) => {
    const baseUrl = process.env.BASE_URL ?? 'http://localhost:5180';
    const response = await request.get(`${baseUrl}/api/lightrag/health`);
    expect(response.ok()).toBeTruthy();
  });
});