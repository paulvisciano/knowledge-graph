<script lang="ts">
  import type { ChatMessage, MCPToolCall } from '$lib/constants';
  import { API } from '$lib/constants';
  import ChatSidebar from './ChatSidebar.svelte';
  import ChatMessages from './ChatMessages.svelte';
  import ChatInput from './ChatInput.svelte';
  import { buildMessageContent, isImageType, type Attachment } from '$lib/utils/file-utils';

  interface Conversation {
    id: string;
    title: string;
    messages: ChatMessage[];
    createdAt: number;
    updatedAt: number;
  }

  let conversations = $state<Conversation[]>([]);
  let activeConversationId = $state('');
  let isStreaming = $state(false);
  let sidebarCollapsed = $state(false);
  let availableModels = $state<string[]>([]);
  let selectedModel = $state('');
  let streamAbortController: AbortController | null = null;

  let activeConversation = $derived(
    conversations.find((c) => c.id === activeConversationId)
  );
  let activeMessages = $derived(activeConversation?.messages ?? []);

  function generateId(): string {
    return crypto.randomUUID();
  }

  function createConversation(): Conversation {
    return {
      id: generateId(),
      title: '',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
  }

  function startNewConversation() {
    const conv = createConversation();
    conversations.unshift(conv);
    activeConversationId = conv.id;
  }

  function switchConversation(id: string) {
    if (!isStreaming) {
      activeConversationId = id;
    }
  }

  function toggleSidebar() {
    sidebarCollapsed = !sidebarCollapsed;
  }

  function cancelStreaming() {
    if (streamAbortController) {
      streamAbortController.abort();
      streamAbortController = null;
    }
    // Finalize any in-flight streaming message — keep whatever content arrived
    if (isStreaming) {
      const conv = conversations.find((c) => c.id === activeConversationId);
      if (conv) {
        conv.messages = conv.messages.map((m) =>
          m.isStreaming ? { ...m, isStreaming: false } : m
        );
      }
      isStreaming = false;
    }
  }

  async function handleSend(text: string, attachments: Attachment[] = []) {
    if (!activeConversation) {
      startNewConversation();
    }
    const conv = conversations.find((c) => c.id === activeConversationId);
    if (!conv) return;

    const imageUrls = attachments
      .filter((a) => isImageType(a.mimeType))
      .map((a) => a.thumbnailUrl || a.dataUrl);

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
      ...(imageUrls.length > 0 ? { imageUrls } : {}),
    };
    conv.messages.push(userMsg);

    if (!conv.title) {
      conv.title = text.slice(0, 50) + (text.length > 50 ? '…' : '');
    }
    conv.updatedAt = Date.now();

    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isStreaming: true,
      mcpToolCalls: [],
    };
    conv.messages.push(assistantMsg);
    isStreaming = true;

    // Create abort controller for this request cycle
    streamAbortController = new AbortController();

    try {
      type ContentPart = { type: string; text?: string; image_url?: { url: string } };
      type ApiMessage = { role: string; content: string | ContentPart[] | null };
      const apiMessages: ApiMessage[] = conv.messages
        .filter((m) => !m.isStreaming)
        .map((m) => {
          if (m.role === 'user' && m.imageUrls && m.imageUrls.length > 0) {
            const parts: ContentPart[] = [];
            if (m.content.trim()) parts.push({ type: 'text', text: m.content });
            for (const url of m.imageUrls) parts.push({ type: 'image_url', image_url: { url } });
            return { role: m.role, content: parts };
          }
          return { role: m.role, content: m.content };
        });

      if (attachments.length > 0) {
        let lastUserIdx = -1;
        for (let i = apiMessages.length - 1; i >= 0; i--) {
          if (apiMessages[i].role === 'user') { lastUserIdx = i; break; }
        }
        if (lastUserIdx !== -1) {
          const existing = apiMessages[lastUserIdx];
          const textContent = typeof existing.content === 'string' ? existing.content : '';
          const userContent = buildMessageContent(textContent, attachments);
          if (typeof userContent !== 'string') {
            apiMessages[lastUserIdx] = { ...existing, content: userContent };
          }
        }
      }

      const response = await fetch(API.llama.chatCompletions, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: selectedModel || undefined,
          messages: apiMessages,
          stream: true,
        }),
        signal: streamAbortController?.signal,
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data: ')) continue;
          const data = trimmed.slice(6);
          if (data === '[DONE]') continue;

          try {
            const parsed = JSON.parse(data);
            const delta = parsed.choices?.[0]?.delta;
            if (delta?.content) {
              assistantMsg.content += delta.content;
            }
          } catch {
            // skip unparseable SSE lines
          }
        }
      }

      assistantMsg.isStreaming = false;
    } catch (err) {
      // If the stream was aborted (user clicked stop), don't show an error
      if (err instanceof DOMException && err.name === 'AbortError') {
        // Stream cancelled — cancelStreaming already finalized state
        return;
      }
      assistantMsg.content += `\n\n**Error:** ${err instanceof Error ? err.message : 'Unknown error'}`;
      assistantMsg.isStreaming = false;
    } finally {
      streamAbortController = null;
      isStreaming = false;
      conv.updatedAt = Date.now();
    }
  }

  async function fetchModels() {
    try {
      const res = await fetch(API.llama.models);
      const data = await res.json();
      availableModels = (data.data ?? []).map((m: { id: string }) => m.id);
      if (availableModels.length > 0 && !selectedModel) {
        selectedModel = availableModels[0];
      }
    } catch {
      // models will remain empty
    }
  }

  $effect(() => {
    fetchModels();
  });
</script>

<div class="flex h-screen w-screen overflow-hidden bg-cyber-bg">
  <ChatSidebar
    {conversations}
    activeId={activeConversationId}
    onSelect={switchConversation}
    onNewConversation={startNewConversation}
    collapsed={sidebarCollapsed}
    onToggleCollapse={toggleSidebar}
  />

  <div class="flex flex-1 flex-col overflow-hidden">
    <ChatMessages messages={activeMessages} />
    <ChatInput
      onSend={handleSend}
      disabled={isStreaming}
      isStreaming={isStreaming}
      onCancel={cancelStreaming}
      models={availableModels}
      selectedModel={selectedModel}
      onModelChange={(m: string) => (selectedModel = m)}
    />
  </div>
</div>