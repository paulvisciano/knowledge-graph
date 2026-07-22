import { SYNC_URL, API } from '$lib/constants';
import type { ChatMessage, MCPToolCall, MessageTimings } from '$lib/constants';

const SYNC_PREFIX = '/api/sync';

interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

interface DatabaseConversation {
  id: string;
  name: string;
  lastModified: number;
  currNode: string | null;
  mcpServerOverrides: { serverId: string; enabled: boolean }[] | null;
  thinkingEnabled: boolean | null;
  reasoningEffort: string | null;
  forkedFromConversationId: string | null;
  pinned: boolean | null;
}

interface DatabaseMessage {
  id: string;
  convId: string;
  type: string;
  timestamp: number;
  role: string;
  content: string;
  parent: string | null;
  children: string[];
  extra: unknown[] | null;
  reasoningContent: string | null;
  toolCalls: string | null;
  completionId: string | null;
  toolCallId: string | null;
  timings: Record<string, unknown> | null;
  model: string | null;
}

interface ExportedConversation {
  conv: DatabaseConversation;
  messages: DatabaseMessage[];
}

interface ConversationSummary {
  id: string;
  name: string;
  lastModified: number;
  currNode: string | null;
  pinned: boolean | null;
}

function buildUrl(path: string): string {
  return `${SYNC_URL}${SYNC_PREFIX}${path}`;
}

class SyncClient {
  conversations = $state<Conversation[]>([]);
  isSyncing = $state(false);
  error = $state<string | null>(null);

  private lastSyncTimestamp = 0;
  private periodicTimer: ReturnType<typeof setInterval> | null = null;
  private loadedConversations = new Map<string, ChatMessage[]>();

  async init(): Promise<void> {
    this.isSyncing = true;
    this.error = null;
    try {
      const url = buildUrl(API.sync.conversations);
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Failed to load conversations: ${res.status}`);
      const summaries: ConversationSummary[] = await res.json();
      this.conversations = summaries
        .map((s) => this.fromSyncConv(s))
        .sort((a, b) => b.createdAt - a.createdAt);
      this.lastSyncTimestamp = Date.now();
    } catch (e: any) {
      this.error = e.message ?? 'Failed to init sync';
    } finally {
      this.isSyncing = false;
    }
  }

  async loadConversation(id: string): Promise<ChatMessage[]> {
    const cached = this.loadedConversations.get(id);
    if (cached) return cached;

    this.error = null;
    try {
      const url = buildUrl(API.sync.conversation(id));
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Failed to load conversation: ${res.status}`);
      const data: { conv: DatabaseConversation; messages: DatabaseMessage[] } = await res.json();
      const messages = data.messages.map((m) => this.fromSyncMsg(m));
      this.loadedConversations.set(id, messages);
      return messages;
    } catch (e: any) {
      this.error = e.message ?? 'Failed to load conversation';
      return [];
    }
  }

  async saveConversation(conv: Conversation): Promise<void> {
    this.error = null;
    try {
      const dbConv = this.toSyncConv(conv);
      const dbMessages = conv.messages
        .filter((m) => !m.isStreaming)
        .map((m, i, arr) => this.toSyncMsg(conv.id, m, i > 0 ? arr[i - 1].id : null));

      const payload: ExportedConversation = { conv: dbConv, messages: dbMessages };
      const url = buildUrl(API.sync.conversations);
      const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.status === 409) {
        return;
      }

      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Save failed: ${res.status} ${body}`);
      }
    } catch (e: any) {
      this.error = e.message ?? 'Failed to save conversation';
    }
  }

  async deleteConversation(id: string): Promise<void> {
    this.error = null;
    try {
      const url = buildUrl(API.sync.conversation(id));
      const res = await fetch(url, { method: 'DELETE' });
      if (!res.ok && res.status !== 404) {
        throw new Error(`Delete failed: ${res.status}`);
      }
      this.conversations = this.conversations.filter((c) => c.id !== id);
      this.loadedConversations.delete(id);
    } catch (e: any) {
      this.error = e.message ?? 'Failed to delete conversation';
    }
  }

  private onSync: ((conv: Conversation[]) => void) | null = null;

  startPeriodicSync(intervalMs: number = 30_000, onSync?: (conv: Conversation[]) => void): void {
    this.onSync = onSync ?? null;
    this.stopPeriodicSync();
    this.periodicTimer = setInterval(() => this.pullRemoteChanges(), intervalMs);
  }

  stopPeriodicSync(): void {
    if (this.periodicTimer) {
      clearInterval(this.periodicTimer);
      this.periodicTimer = null;
    }
  }

  private async pullRemoteChanges(): Promise<void> {
    if (this.lastSyncTimestamp === 0) return;
    try {
      const url = buildUrl(`${API.sync.conversationsSince}?timestamp=${this.lastSyncTimestamp}`);
      const res = await fetch(url);
      if (!res.ok) return;
      const updated: ExportedConversation[] = await res.json();

      if (updated.length === 0) return;

      for (const exported of updated) {
        const nexusConv = this.fromSyncConv(exported.conv);
        const nexusMessages = exported.messages.map((m) => this.fromSyncMsg(m));
        const idx = this.conversations.findIndex((c) => c.id === nexusConv.id);

        if (idx >= 0) {
          this.conversations[idx] = nexusConv;
        } else {
          this.conversations.unshift(nexusConv);
        }
        this.loadedConversations.set(nexusConv.id, nexusMessages);
      }

      this.conversations.sort((a, b) => b.createdAt - a.createdAt);
      this.lastSyncTimestamp = Date.now();
      this.onSync?.(this.conversations);
    } catch {
      return;
    }
  }

  toSyncConv(conv: Conversation): DatabaseConversation {
    return {
      id: conv.id,
      name: conv.title,
      lastModified: conv.updatedAt,
      currNode: String(conv.createdAt),
      mcpServerOverrides: null,
      thinkingEnabled: null,
      reasoningEffort: null,
      forkedFromConversationId: null,
      pinned: null,
    };
  }

  toSyncMsg(convId: string, msg: ChatMessage, parentId: string | null): DatabaseMessage {
    const extra: unknown[] = [];
    // Persist the durable base64 audio data + format, NOT the blob: URL —
    // blob URLs are session-only and die on reload (audioUrl is regenerated
    // from audioData on load in fromSyncMsg). Mirrors how imageUrls stores
    // durable data: URLs.
    if (msg.audioData) extra.push({ type: 'audioData', data: msg.audioData, format: msg.audioFormat ?? 'wav' });
    if (msg.imageUrls?.length) extra.push({ type: 'imageUrls', urls: msg.imageUrls });

    return {
      id: msg.id,
      convId,
      type: 'message',
      timestamp: msg.timestamp,
      role: msg.role,
      content: msg.content,
      parent: parentId,
      children: [],
      extra: extra.length > 0 ? extra : null,
      reasoningContent: msg.thinkingContent ?? null,
      toolCalls: msg.mcpToolCalls ? JSON.stringify(msg.mcpToolCalls) : null,
      completionId: null,
      toolCallId: null,
      timings: (msg.timings as Record<string, unknown>) ?? null,
      model: msg.model ?? null,
    };
  }

  fromSyncConv(data: ConversationSummary | DatabaseConversation): Conversation {
    const createdAt = typeof data.currNode === 'string' && data.currNode !== ''
      ? parseInt(data.currNode, 10)
      : (data as DatabaseConversation).lastModified ?? Date.now();

    return {
      id: data.id,
      title: data.name,
      messages: [],
      createdAt: Number.isNaN(createdAt) ? Date.now() : createdAt,
      updatedAt: data.lastModified,
    };
  }

  fromSyncMsg(data: DatabaseMessage): ChatMessage {
    let mcpToolCalls: MCPToolCall[] | undefined;
    if (data.toolCalls) {
      try {
        mcpToolCalls = JSON.parse(data.toolCalls) as MCPToolCall[];
      } catch {
        mcpToolCalls = undefined;
      }
    }

    let audioUrl: string | undefined;
    let audioData: string | undefined;
    let audioFormat: 'wav' | 'mp3' | undefined;
    let imageUrls: string[] | undefined;
    if (Array.isArray(data.extra)) {
      for (const item of data.extra) {
        if (item && typeof item === 'object') {
          const e = item as Record<string, unknown>;
          if (e.type === 'audioData' && typeof e.data === 'string') {
            audioData = e.data;
            audioFormat = (e.format === 'mp3' ? 'mp3' : 'wav');
            audioUrl = `data:audio/${audioFormat};base64,${audioData}`;
          }
          if (e.type === 'imageUrls' && Array.isArray(e.urls)) imageUrls = e.urls as string[];
        }
      }
    }

    return {
      id: data.id,
      role: data.role as 'user' | 'assistant' | 'system',
      content: data.content,
      timestamp: data.timestamp,
      thinkingContent: data.reasoningContent ?? undefined,
      mcpToolCalls,
      timings: data.timings as MessageTimings | undefined,
      model: data.model ?? undefined,
      audioUrl,
      audioData,
      audioFormat,
      imageUrls,
    };
  }
}

export const syncClient = new SyncClient();