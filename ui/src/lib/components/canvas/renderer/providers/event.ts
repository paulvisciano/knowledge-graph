import { isEventNode } from '$lib/components/canvas/Layout';
import type { BuildCtx, CanvasNode } from '../types';
import type { NodeKindProvider } from '../NodeKindProvider';

export const eventProvider: NodeKindProvider = {
  kind: 'event',
  classify: isEventNode,
  shouldRender(): boolean {
    return false;
  },
  buildCanvasFields(): Partial<CanvasNode> {
    return {};
  },
  planeConfig: {
    color: 0xf6c344,
    textureSource: 'none',
    lodEnabled: false,
  },
};