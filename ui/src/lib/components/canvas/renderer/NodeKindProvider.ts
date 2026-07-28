import type { KGNode } from '$lib/constants';
import type { BuildCtx, CanvasNode, NodeKind, PlaneConfig } from './types';

/** A renderer provider for one node kind. The registry iterates providers
 *  in registration order; the first whose `classify` returns true wins.
 *  Order matters: note/docChunk before photo (the note-fix invariant —
 *  a spurious (Photo) hub with a note/chunk source_id must reclassify). */
export interface NodeKindProvider {
  readonly kind: NodeKind;
  /** Synchronous structural check — does this node belong to this kind?
   *  Reads node.labels, properties.entity_type, properties.source_id,
   *  properties.file_type. Must NOT do async work. */
  classify(node: KGNode): boolean;
  /** Should this node render as a plane on the canvas? Consumed by
   *  `buildCanvasLayout`'s filter. False = node is edge-connected only
   *  (no plane). For docChunk: false for image-description chunks (hidden
   *  entirely — the photo has its own (Photo) hub). */
  shouldRender(node: KGNode, ctx: BuildCtx): boolean;
  /** Populate imageUrl?/fullUrl?/textContent? on the CanvasNode. Pure —
   *  reads ctx maps, returns a Partial<CanvasNode>. */
  buildCanvasFields(node: KGNode, ctx: BuildCtx): Partial<CanvasNode>;
  /** Renderer config for NodePlane. */
  readonly planeConfig: PlaneConfig;
}

const registry = new Map<NodeKind, NodeKindProvider>();
const order: NodeKind[] = [];

export function registerProvider(p: NodeKindProvider): void {
  if (registry.has(p.kind)) throw new Error(`Duplicate provider: ${p.kind}`);
  registry.set(p.kind, p);
  order.push(p.kind);
}

export function getProvider(kind: NodeKind): NodeKindProvider | undefined {
  return registry.get(kind);
}

/** Classify a node by iterating providers in registration order. Returns
 *  the first matching provider's kind, or 'concept' (fallback). Replaces
 *  the hard-coded classifyKind switch — same outputs for same inputs. */
export function classifyKind(node: KGNode): NodeKind {
  for (const kind of order) {
    const provider = registry.get(kind);
    if (provider && provider.classify(node)) return kind;
  }
  return 'concept';
}