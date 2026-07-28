import type { KGNode } from '$lib/constants';
import { isDocChunkFileSource } from '$lib/components/canvas/Layout';
import type { BuildCtx, CanvasNode } from '../types';
import type { NodeKindProvider } from '../NodeKindProvider';

function isDocChunkNode(node: KGNode): boolean {
  const et = node.properties?.entity_type;
  if (typeof et === 'string' && et === 'Document') return true;
  if (node.labels?.some((l) => l.endsWith(' (Document)'))) return true;
  const sid = node.properties?.source_id;
  if (typeof sid === 'string' && isDocChunkFileSource(sid)) return true;
  return false;
}

export const docChunkProvider: NodeKindProvider = {
  kind: 'document',
  classify: isDocChunkNode,
  shouldRender(): boolean {
    return false;
  },
  buildCanvasFields(node: KGNode, ctx: BuildCtx): Partial<CanvasNode> {
    const np = node.properties ?? {};
    const text =
      ctx.noteContents[node.id] ??
      (np.description as string | undefined) ??
      (np.summary as string | undefined) ??
      (np.title as string | undefined) ??
      node.id;
    return { textContent: typeof text === 'string' ? text : node.id };
  },
  planeConfig: {
    color: 0xf5e9c8,
    textureSource: 'text',
    lodEnabled: false,
  },
};