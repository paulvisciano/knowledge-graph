import { isPersonNode } from '$lib/components/canvas/Layout';
import type { CanvasNode } from '../types';
import type { NodeKindProvider } from '../NodeKindProvider';

export const personProvider: NodeKindProvider = {
  kind: 'person',
  classify: isPersonNode,
  shouldRender(): boolean {
    return false;
  },
  buildCanvasFields(): Partial<CanvasNode> {
    return {};
  },
  planeConfig: {
    color: 0x4a9eff,
    textureSource: 'none',
    lodEnabled: false,
  },
};