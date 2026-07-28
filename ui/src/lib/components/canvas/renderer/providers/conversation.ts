import type { KGNode } from '$lib/constants';
import type { BuildCtx, CanvasNode } from '../types';
import type { NodeKindProvider } from '../NodeKindProvider';

function isConversationNode(node: KGNode): boolean {
  const et = node.properties?.entity_type;
  if (typeof et === 'string' && et === 'Conversation') return true;
  return node.labels?.some((l) => l === 'Conversation' || l.endsWith(' (Conversation)')) ?? false;
}

function formatConversationDate(ms: number): string {
  const d = new Date(ms > 1e12 ? ms : ms * 1000);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export const conversationProvider: NodeKindProvider = {
  kind: 'conversation',
  classify: isConversationNode,
  shouldRender(): boolean {
    return true;
  },
  buildCanvasFields(node: KGNode, _ctx: BuildCtx): Partial<CanvasNode> {
    const np = node.properties ?? {};
    const title = (np.name as string) ?? (np.title as string) ?? node.id;
    const createdAt = typeof np.createdAt === 'number' ? np.createdAt : null;
    const dateLabel = createdAt !== null ? formatConversationDate(createdAt) : '';
    const lines = [title];
    if (dateLabel) lines.push(dateLabel, `Chat · ${dateLabel}`);
    return { textContent: lines.join('\n') };
  },
  planeConfig: {
    color: 0x1e2a42,
    textureSource: 'text',
    lodEnabled: false,
  },
};