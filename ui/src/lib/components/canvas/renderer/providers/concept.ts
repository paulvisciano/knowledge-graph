import type { BuildCtx, CanvasNode } from '../types';
import type { KGNode } from '$lib/constants';
import type { NodeKindProvider } from '../NodeKindProvider';

/** The fallback provider — never registered (classifyKind returns 'concept'
 *  when no provider matches). Defined here so NodePlane can fetch a default
 *  planeConfig via getProvider('concept') once the registry is wired. */
export const conceptProvider: NodeKindProvider = {
  kind: 'concept',
  classify(): boolean {
    return false;
  },
  shouldRender(): boolean {
    return true;
  },
  buildCanvasFields(node: KGNode, _ctx: BuildCtx): Partial<CanvasNode> {
    const np = node.properties ?? {};
    const text =
      (np.description as string | undefined) ??
      (np.summary as string | undefined) ??
      (np.title as string | undefined) ??
      node.id;
    return { textContent: typeof text === 'string' ? text : node.id };
  },
  planeConfig: {
    color: 0xb57bff,
    textureSource: 'none',
    lodEnabled: false,
  },
};