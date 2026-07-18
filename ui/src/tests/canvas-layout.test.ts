import { describe, it, expect } from 'vitest';
import { buildCanvasLayout, classifyKind } from '$lib/components/canvas/Layout';
import type { KGNode, KGEdge } from '$lib/constants';

function makeNode(id: string, entityType: string, props: Record<string, unknown> = {}): KGNode {
  return {
    id,
    labels: [entityType],
    properties: { entity_type: entityType, ...props },
  };
}

function makeEdge(id: string, source: string, target: string, type = 'RELATED'): KGEdge {
  return { id, source, target, type, properties: {} };
}

const photo = (id: string, extra: Record<string, unknown> = {}) =>
  makeNode(id, 'Photo', { source_id: `${id}.jpg`, ...extra });

const person = (id: string) => makeNode(id, 'Person', { name: id });
const location = (id: string) => makeNode(id, 'Location', { name: id });
const event = (id: string) => makeNode(id, 'Event', { name: id });

describe('classifyKind', () => {
  it('classifies photos by label and entity_type', () => {
    expect(classifyKind(photo('p1'))).toBe('photo');
    expect(classifyKind(makeNode('x', 'Image'))).toBe('photo');
  });

  it('classifies persons, locations, events, and falls back to concept', () => {
    expect(classifyKind(person('David'))).toBe('person');
    expect(classifyKind(location('Miami'))).toBe('location');
    expect(classifyKind(event('Party'))).toBe('event');
    expect(classifyKind(makeNode('org', 'Organization'))).toBe('concept');
  });
});

describe('buildCanvasLayout', () => {
  it('returns an empty array for no nodes', () => {
    expect(buildCanvasLayout([], [], {}, {})).toEqual([]);
  });

  it('emits only photo/image nodes — non-photo entities inform the layout but are not rendered', () => {
    const nodes = [photo('p1'), person('David'), location('Miami'), event('Party')];
    const out = buildCanvasLayout(nodes, [], {}, {});
    expect(out.map((n) => n.id)).toEqual(['p1']);
    expect(out.every((n) => n.kind === 'photo')).toBe(true);
  });

  it('assigns stable cellX from time buckets (monotonic by date)', () => {
    const d1 = photo('p1', { date_taken_friendly: '2024-01-01' });
    const d2 = photo('p2', { date_taken_friendly: '2024-06-01' });
    const d3 = photo('p3', { date_taken_friendly: '2024-03-01' });
    const out = buildCanvasLayout([d1, d2, d3], [], {}, {});
    const xOf = new Map(out.map((n) => [n.id, n.cellX]));
    // Sorted by date: Jan < Mar < Jun → cellX 0,1,2
    expect(xOf.get('p1')).toBe(0);
    expect(xOf.get('p3')).toBe(1);
    expect(xOf.get('p2')).toBe(2);
  });

  it('places photos in different clusters on different cellY bands', () => {
    // Two photo hubs in separate clusters (each connected to a different
    // person, no edge between the photos) → two distinct hub bands.
    const nodes = [photo('p1'), photo('p2'), person('David'), person('Sarah')];
    const edges = [
      makeEdge('e1', 'p1', 'David'),
      makeEdge('e2', 'p2', 'Sarah'),
    ];
    const out = buildCanvasLayout(nodes, edges, {}, {});
    const byId = new Map(out.map((n) => [n.id, n]));
    expect(byId.get('p1')!.cellY).not.toBe(byId.get('p2')!.cellY);
  });

  it('places photos in the same cluster on the same cellY band', () => {
    // Make David the clear hub by raising the median degree above 1:
    //   David(4) — p1, p2, Miami, Sarah
    //   Miami(3) — David, Party, Event1
    //   Party(2) — Miami, Event1
    //   Event1(2)— Miami, Party
    // Degrees: David=4, Miami=3, Party=2, Event1=2, p1=1, p2=1, Sarah=1.
    // Median of [1,1,1,2,2,3,4] = 2 → hubs = {David, Miami, Party, Event1}.
    // p1 (deg 1) inherits David's band; p2 (deg 1) inherits David's band.
    const nodes = [
      photo('p1'), photo('p2'), person('David'),
      location('Miami'), event('Party'), event('Event1'), person('Sarah'),
    ];
    const edges = [
      makeEdge('e1', 'p1', 'David'),
      makeEdge('e2', 'p2', 'David'),
      makeEdge('e3', 'David', 'Miami'),
      makeEdge('e4', 'David', 'Sarah'),
      makeEdge('e5', 'Miami', 'Party'),
      makeEdge('e6', 'Miami', 'Event1'),
      makeEdge('e7', 'Party', 'Event1'),
    ];
    const out = buildCanvasLayout(nodes, edges, {}, {});
    const byId = new Map(out.map((n) => [n.id, n]));
    expect(byId.get('p1')!.cellY).toBe(byId.get('p2')!.cellY);
  });

  it('cellZ is 0 for all photos when nothing is focused', () => {
    const nodes = [photo('p1'), person('David'), location('Miami')];
    const edges = [makeEdge('e1', 'p1', 'David'), makeEdge('e2', 'David', 'Miami')];
    const out = buildCanvasLayout(nodes, edges, {}, {});
    expect(out.every((n) => n.cellZ === 0)).toBe(true);
  });

  it('cellZ reflects relationship depth from the focused photo', () => {
    // Chain: p1 — David — p2 — Miami — p3. Focus p1.
    //   1-hop from p1: {David}  → depth 0 (with p1)
    //   2-hop from p1: {p2}     → depth 1
    //   3-hop from p1: {Miami}  → depth 2 (beyond 2-hop)
    //   4-hop from p1: {p3}     → depth 2
    const nodes = [photo('p1'), photo('p2'), photo('p3'), person('David'), location('Miami')];
    const edges = [
      makeEdge('e1', 'p1', 'David'),
      makeEdge('e2', 'David', 'p2'),
      makeEdge('e3', 'p2', 'Miami'),
      makeEdge('e4', 'Miami', 'p3'),
    ];
    const out = buildCanvasLayout(nodes, edges, {}, {}, 'p1');
    const byId = new Map(out.map((n) => [n.id, n.cellZ]));
    expect(byId.get('p1')).toBe(0);
    expect(byId.get('p2')).toBe(1);
    expect(byId.get('p3')).toBe(2);
  });

  it('is deterministic: same inputs → same outputs', () => {
    const nodes = [photo('p1', { date_taken_friendly: '2024-01-01' }), person('David')];
    const edges = [makeEdge('e1', 'p1', 'David')];
    const a = buildCanvasLayout(nodes, edges, {}, {});
    const b = buildCanvasLayout(nodes, edges, {}, {});
    expect(a).toEqual(b);
  });

  it('builds thumbnail and full URLs for photo nodes', () => {
    const p = photo('p1');
    const out = buildCanvasLayout([p], [], {}, {});
    const node = out.find((n) => n.id === 'p1')!;
    expect(node.imageUrl).toContain('/api/kg/images/photo/');
    expect(node.imageUrl).toContain('?w=512');
    expect(node.fullUrl).toContain('?w=full');
  });

  it('uses cached photoImages URL when provided', () => {
    const p = photo('p1');
    const out = buildCanvasLayout([p], [], { p1: 'https://cached/thumb.jpg' }, {});
    const node = out.find((n) => n.id === 'p1')!;
    expect(node.imageUrl).toBe('https://cached/thumb.jpg');
  });

  it('sizes photo planes preserving aspect ratio with a random base', () => {
    const p = photo('p1', { width: 800, height: 600 });
    const out = buildCanvasLayout([p], [], {}, {});
    const pNode = out.find((n) => n.id === 'p1')!;
    // 4:3 aspect preserved; base height is random in [60, 120).
    expect(pNode.height).toBeGreaterThanOrEqual(60);
    expect(pNode.height).toBeLessThan(120);
    expect(pNode.width).toBe(Math.round(pNode.height * (800 / 600)));
  });

  it('photo localZ is non-negative (no parallax-behind for photos)', () => {
    const out = buildCanvasLayout([photo('p1'), photo('p2'), photo('p3')], [], {}, {});
    expect(out.every((n) => n.localZ >= 0)).toBe(true);
  });

  it('preserves KGNode identity (labels, properties, kind)', () => {
    const p = photo('p1', { camera: 'Canon' });
    const out = buildCanvasLayout([p], [], {}, {});
    const node = out[0];
    expect(node.id).toBe('p1');
    expect(node.labels).toEqual(['Photo']);
    expect(node.properties.camera).toBe('Canon');
    expect(node.kind).toBe('photo');
  });
});