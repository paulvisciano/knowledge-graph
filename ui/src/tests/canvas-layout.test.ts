import { describe, it, expect } from 'vitest';
import { buildCanvasLayout, classifyKind, isNoteFileSource, isNoteNode } from '$lib/components/canvas/Layout';
import { TIME_BUCKET_SPACING } from '$lib/components/canvas/renderer/constants';
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
const note = (id: string, extra: Record<string, unknown> = {}) =>
  makeNode(id, 'Note', { source_id: `note_${id}`, description: `body of ${id}`, ...extra });

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

  it('classifies note nodes by entity_type, (Note) label suffix, or note file_source', () => {
    expect(classifyKind(note('n1'))).toBe('note');
    expect(classifyKind(makeNode('diary-2026-07-22 (Note)', 'Photo', { source_id: 'diary-entry-2026-07-22' }))).toBe('note');
    expect(classifyKind(makeNode('hub', 'Photo', { source_id: 'chat-note-20260727-150000-123456' }))).toBe('note');
    expect(classifyKind(makeNode('legacy', 'Photo', { source_id: 'note_1700000000' }))).toBe('note');
    expect(classifyKind(makeNode('plan', 'Photo', { source_id: 'plan_entry' }))).toBe('note');
    expect(classifyKind(makeNode('du', 'Photo', { source_id: 'daily_update' }))).toBe('note');
    expect(classifyKind(makeNode('pcu', 'Photo', { source_id: 'personal_context_update' }))).toBe('note');
    expect(classifyKind(makeNode('mcp', 'Photo', { source_id: 'mcp-foo-20260727-150000-123456' }))).toBe('note');
  });

  it('classifies a spurious (Photo) hub with a note source_id as note (defensive 404 guard)', () => {
    const spurious = {
      id: 'diary-entry-2026-07-22-abc (Photo)',
      labels: ['Photo'],
      properties: { entity_type: 'Photo', source_id: 'diary-entry-2026-07-22-abc' },
    };
    expect(classifyKind(spurious)).toBe('note');
  });

  it('does NOT misclassify real photos as notes (image heuristic wins)', () => {
    expect(classifyKind(photo('p1', { source_id: 'PXL_20260727_123456.jpg' }))).toBe('photo');
    expect(classifyKind(photo('p2', { source_id: 'IMG_1234.png' }))).toBe('photo');
    expect(classifyKind(photo('p3', { source_id: 'shot.heic' }))).toBe('photo');
    // A note prefix on a .jpg is still a photo.
    expect(classifyKind(photo('p4', { source_id: 'diary-entry-fake.jpg' }))).toBe('photo');
  });
});

describe('isNoteFileSource', () => {
  it('recognizes legacy and prefixed note file_sources', () => {
    expect(isNoteFileSource('note_1700000000')).toBe(true);
    expect(isNoteFileSource('diary-entry-2026-07-22')).toBe(true);
    expect(isNoteFileSource('chat-note-20260727-150000-123456')).toBe(true);
    expect(isNoteFileSource('plan_entry')).toBe(true);
    expect(isNoteFileSource('daily_update')).toBe(true);
    expect(isNoteFileSource('personal_context_update')).toBe(true);
    expect(isNoteFileSource('mcp-anything')).toBe(true);
    expect(isNoteFileSource('preference-update-x')).toBe(true);
    expect(isNoteFileSource('correction-y')).toBe(true);
  });

  it('recognizes the bare MCP timestamp suffix', () => {
    expect(isNoteFileSource('something-20260727-150000-123456')).toBe(true);
  });

  it('rejects image strings even when they match a note prefix or timestamp suffix', () => {
    expect(isNoteFileSource('PXL_20260727_123456.jpg')).toBe(false);
    expect(isNoteFileSource('photo.RAW')).toBe(false);
    expect(isNoteFileSource('pic.png')).toBe(false);
    expect(isNoteFileSource('shot.heic')).toBe(false);
    expect(isNoteFileSource('diary-entry-fake.jpg')).toBe(false);
  });

  it('rejects unrelated strings', () => {
    expect(isNoteFileSource('manual_creation')).toBe(false);
    expect(isNoteFileSource('')).toBe(false);
    expect(isNoteFileSource('random-document.pdf')).toBe(false);
  });
});

describe('isNoteNode', () => {
  it('matches entity_type Note', () => {
    expect(isNoteNode(note('n1'))).toBe(true);
  });
  it('matches a (Note) label suffix', () => {
    expect(isNoteNode({ labels: ['diary (Note)'], properties: {} })).toBe(true);
  });
  it('matches a spurious (Photo) hub via source_id (note file_source)', () => {
    expect(isNoteNode({ properties: { source_id: 'plan_entry' } })).toBe(true);
    expect(isNoteNode({ properties: { source_id: 'daily_update' } })).toBe(true);
    expect(isNoteNode({ properties: { source_id: 'note_1' } })).toBe(true);
    expect(isNoteNode({ properties: { source_id: 'chat-note-1' } })).toBe(true);
    expect(isNoteNode({ properties: { source_id: 'diary-entry-2026-07-27-20260727-154548-277583' } })).toBe(true);
  });
  it('does NOT match an extracted entity whose file_path is a note file_source', () => {
    // Extracted entities (person/location/concept) carry the note's
    // file_source in `file_path` (the document they were extracted FROM),
    // but they are NOT notes — only the (Note) hub is. Checking file_path
    // here would misclassify every entity extracted from a note.
    expect(isNoteNode({ properties: { entity_type: 'person', file_path: 'daily_update' } })).toBe(false);
    expect(isNoteNode({ properties: { entity_type: 'location', file_path: 'note_1784640603' } })).toBe(false);
    expect(isNoteNode({ properties: { entity_type: 'concept', file_path: 'diary-entry-2026-07-22-20260722-145036-188422' } })).toBe(false);
    // Compound <SEP>-joined file_path (multi-source entity) is also not a note.
    expect(isNoteNode({ properties: { entity_type: 'person', file_path: 'personal_context_update<SEP>daily_update' } })).toBe(false);
  });
  it('false for a real photo', () => {
    expect(isNoteNode(photo('p1'))).toBe(false);
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

  it('assigns stable cellZ from time buckets (monotonic by date, oldest=0)', () => {
    const d1 = photo('p1', { date_taken_friendly: '2024-01-01' });
    const d2 = photo('p2', { date_taken_friendly: '2024-06-01' });
    const d3 = photo('p3', { date_taken_friendly: '2024-03-01' });
    const out = buildCanvasLayout([d1, d2, d3], [], {}, {});
    const zOf = new Map(out.map((n) => [n.id, n.cellZ]));
    // Sorted ascending by date: Jan(0) < Mar(1) < Jun(2) → cellZ 0, S, 2S
    expect(zOf.get('p1')).toBe(0);
    expect(zOf.get('p3')).toBe(1 * TIME_BUCKET_SPACING);
    expect(zOf.get('p2')).toBe(2 * TIME_BUCKET_SPACING);
  });

  it('spreads photos in the same time bucket across a 2D grid on X and Y', () => {
    // 4 photos with the same date → same cellZ bucket → arranged in a 2x2 grid
    // centered on (0,0) so they fill the viewport width AND height.
    const nodes = [
      photo('p1', { date_taken_friendly: '2024-01-01' }),
      photo('p2', { date_taken_friendly: '2024-01-01' }),
      photo('p3', { date_taken_friendly: '2024-01-01' }),
      photo('p4', { date_taken_friendly: '2024-01-01' }),
    ];
    const out = buildCanvasLayout(nodes, [], {}, {});
    const byId = new Map(out.map((n) => [n.id, n]));
    // All in the same time bucket (cellZ=0).
    expect(out.every((n) => n.cellZ === 0)).toBe(true);
    // Grid side = ceil(sqrt(4)) = 2, half = 0.5 → grid coords {-1,0} x {-1,0}.
    // At least one pair differs in cellX and at least one pair differs in cellY.
    const xs = new Set(out.map((n) => n.cellX));
    const ys = new Set(out.map((n) => n.cellY));
    expect(xs.size).toBeGreaterThan(1);
    expect(ys.size).toBeGreaterThan(1);
  });

  it('cellX is 0 for all photos when nothing is focused', () => {
    const nodes = [photo('p1'), person('David'), location('Miami')];
    const edges = [makeEdge('e1', 'p1', 'David'), makeEdge('e2', 'David', 'Miami')];
    const out = buildCanvasLayout(nodes, edges, {}, {});
    expect(out.every((n) => n.cellX === 0)).toBe(true);
  });

  it('cellX reflects relationship depth from the focused photo', () => {
    // Chain: p1 — David — p2 — Miami — p3. Focus p1.
    //   1-hop from p1: {David}  → depth 0 (with p1)
    //   2-hop from p1: {p2}     → depth 1
    //   3-hop from p1: {Miami}  → depth 2 (beyond 2-hop)
    //   4-hop from p1: {p3}     → depth 2
    // Each photo is its own time bucket (no dates) → xIdx=0, so
    // cellX = depth * 3 (the parallax offset layered on the within-bucket spread).
    const nodes = [photo('p1'), photo('p2'), photo('p3'), person('David'), location('Miami')];
    const edges = [
      makeEdge('e1', 'p1', 'David'),
      makeEdge('e2', 'David', 'p2'),
      makeEdge('e3', 'p2', 'Miami'),
      makeEdge('e4', 'Miami', 'p3'),
    ];
    const out = buildCanvasLayout(nodes, edges, {}, {}, 'p1');
    const byId = new Map(out.map((n) => [n.id, n.cellX]));
    expect(byId.get('p1')).toBe(0);
    expect(byId.get('p2')).toBe(3);
    expect(byId.get('p3')).toBe(6);
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

  it('renders note nodes as planes with textContent and no image URLs', () => {
    const n = note('n1', { description: 'hello world', date_taken_friendly: '2024-01-01' });
    const out = buildCanvasLayout([n], [], {}, {});
    expect(out.map((x) => x.id)).toEqual(['n1']);
    const node = out.find((x) => x.id === 'n1')!;
    expect(node.kind).toBe('note');
    expect(node.textContent).toBe('hello world');
    expect(node.imageUrl).toBeUndefined();
    expect(node.fullUrl).toBeUndefined();
    // Text-friendly portrait aspect (~1 : 1.4) when no image dims present.
    expect(node.height).toBeGreaterThanOrEqual(60);
    expect(node.height).toBeLessThan(120);
    expect(node.width).toBe(Math.round(node.height / 1.4));
  });

  it('note textContent falls back through summary → title → id', () => {
    const a = makeNode('a', 'Note', { source_id: 'note_a', summary: 'S' });
    const b = makeNode('b', 'Note', { source_id: 'note_b', title: 'T' });
    const c = makeNode('c', 'Note', { source_id: 'note_c' });
    const out = buildCanvasLayout([a, b, c], [], {}, {});
    const byId = new Map(out.map((x) => [x.id, x]));
    expect(byId.get('a')!.textContent).toBe('S');
    expect(byId.get('b')!.textContent).toBe('T');
    expect(byId.get('c')!.textContent).toBe('c');
  });

  it('a spurious (Photo) hub with a note source_id renders as a note plane, not a photo', () => {
    const spurious: KGNode = {
      id: 'diary-entry-2026-07-22-xyz (Photo)',
      labels: ['Photo'],
      properties: { entity_type: 'Photo', source_id: 'diary-entry-2026-07-22-xyz', description: 'D' },
    };
    const out = buildCanvasLayout([spurious], [], {}, {});
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe('note');
    expect(out[0].textContent).toBe('D');
    // The defensive guard: no photo image URL is ever built for it.
    expect(out[0].imageUrl).toBeUndefined();
    expect(out[0].fullUrl).toBeUndefined();
  });

  it('renders photos and notes together in the same time bucket grid', () => {
    const p = photo('p1', { date_taken_friendly: '2024-01-01' });
    const n = note('n1', { date_taken_friendly: '2024-01-01' });
    const out = buildCanvasLayout([p, n], [], {}, {});
    expect(out.map((x) => x.id).sort()).toEqual(['n1', 'p1']);
    const kinds = new Set(out.map((x) => x.kind));
    expect(kinds.has('photo')).toBe(true);
    expect(kinds.has('note')).toBe(true);
  });
});