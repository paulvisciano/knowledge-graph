import { isLocationNode } from '$lib/components/canvas/Layout';
import type { CanvasNode } from '../types';
import type { NodeKindProvider } from '../NodeKindProvider';

export const locationProvider: NodeKindProvider = {
  kind: 'location',
  classify: isLocationNode,
  shouldRender(): boolean {
    return false;
  },
  buildCanvasFields(): Partial<CanvasNode> {
    return {};
  },
  planeConfig: {
    color: 0x39d98a,
    textureSource: 'none',
    lodEnabled: false,
  },
};