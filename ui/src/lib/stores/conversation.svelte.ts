import type { ChatMessage } from '$lib/constants';

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

class ConversationStore {
  conversations = $state<Conversation[]>([]);
  activeConversationId = $state('');
  unreadConversations = $state<Set<string>>(new Set());

  get activeConversation(): Conversation | undefined {
    return this.conversations.find((c) => c.id === this.activeConversationId);
  }

  get activeMessages(): ChatMessage[] {
    return this.activeConversation?.messages ?? [];
  }

  generateId(): string {
    return crypto.randomUUID();
  }

  createConversation(): Conversation {
    const conv: Conversation = {
      id: this.generateId(),
      title: '',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    this.conversations.unshift(conv);
    this.activeConversationId = conv.id;
    return conv;
  }

  switchConversation(id: string): void {
    this.activeConversationId = id;
    if (this.unreadConversations.has(id)) {
      this.unreadConversations = new Set(
        [...this.unreadConversations].filter((cid) => cid !== id)
      );
    }
  }

  markUnread(id: string): void {
    this.unreadConversations = new Set([...this.unreadConversations, id]);
  }

  updateConversation(id: string, updater: (conv: Conversation) => void): void {
    const conv = this.conversations.find((c) => c.id === id);
    if (conv) {
      updater(conv);
      conv.updatedAt = Date.now();
    }
  }

  setConversations(conversations: Conversation[]): void {
    this.conversations = conversations;
  }
}

export const conversationStore = new ConversationStore();