import { API } from '$lib/constants';
import type { KGNode } from '$lib/constants';
import { isPhotoNode } from '$lib/components/canvas/Layout';
import type { BuildCtx, CanvasNode } from '../types';
import type { NodeKindProvider } from '../NodeKindProvider';

const KG_API_BASE = '/api/kg';

function photoFilename(node: KGNode): string | null {
  const p = node.properties ?? {};
  const f =
    (p.source_id as string | undefined) ??
    (p.file_path as string | undefined) ??
    (p.filename as string | undefined) ??
    (p.file_source as string | undefined);
  if (typeof f === 'string' && f.length > 0) return f;
  return null;
}

function isStalePhoto(node: KGNode): boolean {
  const sourceId = node.properties?.source_id ?? node.properties?.file_path;
  return !sourceId || sourceId === 'manual_creation';
}

export const photoProvider: NodeKindProvider = {
  kind: 'photo',
  classify: isPhotoNode,
  shouldRender(node: KGNode): boolean {
    return !isStalePhoto(node);
  },
  buildCanvasFields(node: KGNode, ctx: BuildCtx): Partial<CanvasNode> {
    const cached = ctx.photoImages[node.id];
    const fname = photoFilename(node);
    const fullUrl = fname ? `${KG_API_BASE}${API.kg.photoImageFull(fname)}` : undefined;
    if (cached) return { imageUrl: cached, fullUrl };
    if (!fname) return {};
    return {
      imageUrl: `${KG_API_BASE}${API.kg.photoImageThumb(fname, 512)}`,
      fullUrl,
    };
  },
  planeConfig: {
    color: 0xffffff,
    textureSource: 'url',
    lodEnabled: true,
  },
};