<script lang="ts">
  import type { ChatMessage, MCPToolCall, ChatMode, OllamaMessage } from '$lib/constants';
  import { API } from '$lib/constants';
  import { eventBus } from '$lib/stores/event-bus.svelte';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { activeTab, selectedNodeId, navDrawerOpen, historyPanelOpen } from '$lib/stores/ui';
  import { mcpClient } from '$lib/services/mcp-client.svelte';
  import { connectionStore } from '$lib/stores/connection.svelte';
  import { configStore } from '$lib/stores/config.svelte';
  import { conversationStore } from '$lib/stores/conversation.svelte';
  import GraphView from '$lib/components/graph/GraphView.svelte';
  import IngestionPanel from '$lib/components/ingestion/IngestionPanel.svelte';
  import ActivityFeed from '$lib/components/activity/ActivityFeed.svelte';
  import NodeDetail from '$lib/components/graph/NodeDetail.svelte';

  import Icon from '$lib/components/ui/Icon.svelte';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import { AudioRecorder, blobToBase64, isAudioRecordingSupported } from '$lib/utils/audio-recording';
  import { extractImageFilePaths } from '$lib/utils/extract-image-paths';
  import ImageGallery from '$lib/components/ui/ImageGallery.svelte';

  import AttachmentMenu from '$lib/components/ui/AttachmentMenu.svelte';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
  import { syncClient } from '$lib/services/sync-client.svelte';
  import { fileToAttachment, buildMessageContent, revokeAttachmentUrls, isImageType, MAX_ATTACHMENTS, type Attachment } from '$lib/utils/file-utils';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';

  interface Conversation {
    id: string;
    title: string;
    messages: ChatMessage[];
    createdAt: number;
    updatedAt: number;
  }

  let conversations = $state<Conversation[]>([]);

  // Expose stores on window for E2E testing (stripped in production builds)
  if (typeof window !== 'undefined') {
    (window as any).__graphStore = graphStore;
    (window as any).__imageProcessingStore = imageProcessingStore;
  }

  $effect(() => {
    resumeInProgressJobs();
  });
  let activeConversationId = $state('');
  let messages = $state<ChatMessage[]>([]);
  let isStreaming = $state(false);
  let chatInput = $state('');
  let panelChatInput = $state('');
  let chatExpanded = $state(false);
  let chatMode = $state<ChatMode>('kg-direct');
  let availableModels = $state<string[]>([]);
  let selectedModel = $state('');
  let textareaEl: HTMLTextAreaElement | undefined = $state();
  let panelTextareaEl: HTMLTextAreaElement | undefined = $state();
  let messagesContainer: HTMLDivElement | undefined = $state();
  let thinkingContent = $state('');
  let isProcessing = $state(false);
  let processingLabel = $state('');
  let tokensPerSecond = $state<number | null>(null);
  let attachments = $state<Attachment[]>([]);
  let attachError = $state('');
  let imageFileInput: HTMLInputElement | undefined = $state();
  let docFileInput: HTMLInputElement | undefined = $state();
  let isDraggingOver = $state(false);
  let dragCounter = 0;

  // Stream cancellation support — allows navigation during streaming
  let streamAbortController: AbortController | null = null;

  // Throttled streaming state — buffers tokens and flushes to UI on animation frames
  let streamingBuffer: { content: string; thinking: string; assistantId: string } | null = null;
  let streamingRafId: number | null = null;

  // Tracks which conversation owns the active stream, so that conversation
  // switches during streaming don't redirect writes to the wrong messages array.
  let streamingConversationId: string | null = null;

  // Unread activity indicators — set when a background stream produces new content
  let unreadConversations = $state<Set<string>>(new Set());

  // Whether the currently-viewed conversation is the one being streamed to.
  // Used to gate input controls — you can still type in other conversations.
  let isActiveConversationStreaming = $derived(
    isStreaming && streamingConversationId === activeConversationId
  );

  /**
   * Update a specific message in the conversation that owns the active stream.
   * If that conversation is currently displayed, updates `messages` directly
   * (so Svelte reacts and re-renders). If the stream is in the background,
   * updates `conversations[i].messages` and marks it as unread.
   */
  function updateStreamMessage(
    assistantId: string,
    updater: (m: ChatMessage) => ChatMessage
  ) {
    if (!streamingConversationId) {
      // No stream context — update messages directly
      messages = messages.map((m) => m.id === assistantId ? updater(m) : m);
      return;
    }

    const isActive = streamingConversationId === activeConversationId;

    if (isActive) {
      // Stream's conversation is on screen — update reactive state
      messages = messages.map((m) => m.id === assistantId ? updater(m) : m);
    } else {
      // Stream is in the background — update the conversation object directly
      const conv = conversations.find((c) => c.id === streamingConversationId);
      if (conv) {
        conv.messages = conv.messages.map((m) => m.id === assistantId ? updater(m) : m);
        // Mark as unread so the user sees a badge
        unreadConversations = new Set([...unreadConversations, streamingConversationId]);
      }
    }
  }

  /**
   * Append a message to the stream's conversation.
   * Works like updateStreamMessage — targets the right conversation.
   */
  function pushStreamMessage(msg: ChatMessage) {
    if (!streamingConversationId) {
      messages = [...messages, msg];
      return;
    }

    const isActive = streamingConversationId === activeConversationId;

    if (isActive) {
      messages = [...messages, msg];
    } else {
      const conv = conversations.find((c) => c.id === streamingConversationId);
      if (conv) {
        conv.messages = [...conv.messages, msg];
        unreadConversations = new Set([...unreadConversations, streamingConversationId]);
      }
    }
  }

  let audioRecorder = $state<AudioRecorder | null>(null);
  let isRecording = $state(false);
  let isTranscribing = $state(false);
  let recordingSupported = $state(false);

  $effect(() => {
    recordingSupported = isAudioRecordingSupported();
    if (recordingSupported) {
      audioRecorder = new AudioRecorder();
    }
  });

  async function handleMicClick() {
    if (!audioRecorder || !recordingSupported) return;

    if (isRecording) {
      isRecording = false;
      try {
        const wavBlob = await audioRecorder.stopRecording();
        console.log('[audio] WAV blob size:', wavBlob.size, 'type:', wavBlob.type);
        const audioUrl = URL.createObjectURL(wavBlob);
        isTranscribing = true;
        processingLabel = 'Processing audio...';
        const audioData = await blobToBase64(wavBlob);
        console.log('[audio] base64 length:', audioData.length, 'format: wav');
        await handleSend(audioUrl, audioData, 'wav');
      } catch (e) {
        console.error('Audio processing failed:', e);
      } finally {
        isTranscribing = false;
        processingLabel = '';
      }
    } else {
      try {
        await audioRecorder.startRecording();
        isRecording = true;
      } catch (e) {
        console.error('Failed to start recording:', e);
      }
    }
  }

  async function handleAttachFiles(fileList: FileList | null) {
    if (!fileList) return;
    attachError = '';
    const remaining = MAX_ATTACHMENTS - attachments.length;
    if (remaining <= 0) {
      attachError = `Maximum ${MAX_ATTACHMENTS} attachments allowed`;
      return;
    }
    const files = Array.from(fileList).slice(0, remaining);

    // Convert all files to attachments in parallel for responsiveness
    const results = await Promise.allSettled(files.map((file) => fileToAttachment(file)));
    const newAttachments: Attachment[] = [];
    const imageAttachments: Attachment[] = [];

    for (const result of results) {
      if (result.status === 'fulfilled') {
        const att = result.value;
        newAttachments.push(att);
        if (isImageType(att.mimeType)) {
          imageAttachments.push(att);
        }
      } else {
        attachError = result.reason instanceof Error ? result.reason.message : 'Failed to attach file';
      }
    }

    if (newAttachments.length > 0) {
      attachments = [...attachments, ...newAttachments];
    }

    // Add all image nodes to the graph immediately and start processing in parallel
    for (const att of imageAttachments) {
      const photoNodeId = `${att.name} (Photo)`;
      graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', source_id: att.name });
      if (att.dataUrl) {
        graphStore.setPhotoImage(photoNodeId, att.dataUrl);
        imageProcessingStore.startProcessing(photoNodeId, att.name, att.dataUrl);
      }
    }

    // Process all images concurrently — each gets its own job and SSE stream
    for (const att of imageAttachments) {
      processSingleImage(att);
    }
  }

  function openImagePicker() {
    imageFileInput?.click();
  }

  function openDocumentPicker() {
    docFileInput?.click();
  }

  function removeAttachment(id: string) {
    const att = attachments.find((a) => a.id === id);
    if (att?.thumbnailUrl) URL.revokeObjectURL(att.thumbnailUrl);
    attachments = attachments.filter((a) => a.id !== id);
  }

  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
  }

  function handleDragEnter(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter++;
    if (dragCounter === 1) {
      isDraggingOver = true;
    }
  }

  function handleDragLeave(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter--;
    if (dragCounter === 0) {
      isDraggingOver = false;
    }
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    isDraggingOver = false;
    dragCounter = 0;
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      handleAttachFiles(files);
    }
  }

  let promptTokens = $state<number | null>(null);

  function generateId(): string {
    return crypto.randomUUID();
  }

  function startNewConversation(): string {
    const id = generateId();
    const conv: Conversation = {
      id,
      title: '',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    conversations.unshift(conv);
    activeConversationId = id;
    messages = [];
    syncClient.saveConversation(conv);
    return id;
  }

  function cancelStreaming() {
    // Cancel the active stream if any
    if (streamAbortController) {
      streamAbortController.abort();
      streamAbortController = null;
    }
    // Flush any buffered content BEFORE cancelling the frame,
    // otherwise tokens accumulated since the last frame are lost.
    if (streamingBuffer) {
      const { content, thinking, assistantId } = streamingBuffer;
      updateStreamMessage(assistantId, (m) => ({
        ...m,
        content,
        ...(thinking ? { thinkingContent: thinking } : {}),
      }));
      if (thinking) thinkingContent = thinking;
      streamingBuffer = null;
    }
    if (streamingRafId !== null) {
      cancelAnimationFrame(streamingRafId);
      streamingRafId = null;
    }
    // Finalize any in-flight streaming message — keep whatever content arrived
    if (isStreaming) {
      messages = messages.map((m) =>
        m.isStreaming ? { ...m, isStreaming: false } : m
      );
      // Also finalize in background conversation if different from active
      if (streamingConversationId && streamingConversationId !== activeConversationId) {
        const conv = conversations.find((c) => c.id === streamingConversationId);
        if (conv) {
          conv.messages = conv.messages.map((m) =>
            m.isStreaming ? { ...m, isStreaming: false } : m
          );
        }
      }
      isStreaming = false;
      isProcessing = false;
      processingLabel = '';
      thinkingContent = '';
      tokensPerSecond = null;
      promptTokens = null;
    }
    streamingConversationId = null;
    saveMessagesToConversation();
  }

  async function switchConversation(id: string) {
    // Save current messages (including any in-flight stream content)
    saveMessagesToConversation();

    // If we're leaving a conversation with an active stream, save its state
    // so the stream can continue writing to it in the background.
    if (isStreaming && activeConversationId && activeConversationId !== id) {
      const currentConv = conversations.find((c) => c.id === activeConversationId);
      if (currentConv) {
        currentConv.messages = [...messages];
      }
    }

    activeConversationId = id;
    const conv = conversations.find((c) => c.id === id);
    if (conv) {
      if (conv.messages.length === 0) {
        const loaded = await syncClient.loadConversation(id);
        if (loaded.length > 0) {
          conv.messages = loaded;
          messages = [...loaded];
        } else {
          messages = [...conv.messages];
        }
      } else {
        messages = [...conv.messages];
      }
    } else {
      messages = [];
    }

    // Clear unread badge for this conversation
    if (unreadConversations.has(id)) {
      unreadConversations = new Set([...unreadConversations].filter((cid) => cid !== id));
    }

    chatExpanded = true;
  }

  function saveMessagesToConversation() {
    const conv = conversations.find((c) => c.id === activeConversationId);
    if (conv) {
      conv.messages = [...messages];
      conv.updatedAt = Date.now();
      syncClient.saveConversation(conv);
    }
  }

  /**
   * Stream a KG-augmented chat response directly through LightRAG's /api/chat endpoint.
   * This bypasses the LLM+MCP loop entirely — LightRAG handles retrieval and generation.
   */
  async function streamKGChatResponse() {
    streamingConversationId = activeConversationId;
    streamAbortController = new AbortController();

    const assistantId = generateId();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isStreaming: true,
      mcpToolCalls: [],
    };
    pushStreamMessage(assistantMsg);
    isStreaming = true;
    isProcessing = true;
    processingLabel = 'Searching knowledge graph...';

    try {
      const ollamaMessages: OllamaMessage[] = messages
        .filter((m) => !m.isStreaming)
        .map((m) => ({
          role: m.role as 'user' | 'assistant' | 'system',
          content: m.content,
        }));

      let accumulatedContent = '';
      let gotFirstToken = false;

      function scheduleFlush() {
        if (streamingRafId !== null) return;
        streamingRafId = requestAnimationFrame(() => {
          streamingRafId = null;
          if (streamingBuffer) {
            const buf = streamingBuffer;
            if (streamingConversationId === activeConversationId) {
              messages = messages.map((m) =>
                m.id === assistantId
                  ? { ...m, content: buf.content, isStreaming: true }
                  : m
              );
            }
            streamingBuffer = null;
          }
        });
      }

      for await (const chunk of lightragClient.chatStream(ollamaMessages)) {
        if (!streamAbortController) break;

        if (!gotFirstToken && chunk.message?.content) {
          gotFirstToken = true;
          if (streamingConversationId === activeConversationId) {
            processingLabel = 'Generating...';
          }
        }

        if (chunk.message?.content) {
          accumulatedContent += chunk.message.content;
          streamingBuffer = { content: accumulatedContent, thinking: '', assistantId };
          scheduleFlush();
        }

        if (chunk.done) {
          if (chunk.eval_count && chunk.prompt_eval_count) {
            const totalMs = (chunk.total_duration ?? 0) / 1_000_000;
            const evalMs = totalMs;
            const tokensPerSec = chunk.eval_count > 0 && evalMs > 0
              ? Math.round(chunk.eval_count / (evalMs / 1000))
              : null;
            tokensPerSecond = tokensPerSec;
            promptTokens = chunk.prompt_eval_count;
          }
        }
      }

      if (streamingRafId !== null) {
        cancelAnimationFrame(streamingRafId);
        streamingRafId = null;
      }
      streamingBuffer = null;

      updateStreamMessage(assistantId, (m) => ({
        ...m,
        content: accumulatedContent || '',
        isStreaming: false,
        model: 'lightrag',
      }));

      saveMessagesToConversation();
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;

      const errMsg = err instanceof Error ? err.message : 'Unknown error';
      updateStreamMessage(assistantId, (m) => ({
        ...m,
        content: m.content || `Error: ${errMsg}`,
        isStreaming: false,
      }));
    } finally {
      isStreaming = false;
      isProcessing = false;
      processingLabel = '';
      thinkingContent = '';
      tokensPerSecond = null;
      promptTokens = null;

      if (streamingConversationId) {
        const conv = conversations.find((c) => c.id === streamingConversationId);
        if (conv) {
          if (streamingConversationId !== activeConversationId) {
            conv.messages = conv.messages.map((m) =>
              m.isStreaming ? { ...m, isStreaming: false } : m
            );
          } else {
            conv.messages = [...messages];
          }
          conv.updatedAt = Date.now();
          syncClient.saveConversation(conv);
        }
        if (streamingConversationId !== activeConversationId) {
          unreadConversations = new Set([...unreadConversations, streamingConversationId]);
        }
      }
      streamingConversationId = null;
      requestAnimationFrame(scrollToBottom);
    }
  }

  /**
   * Stream an assistant response from the LLM based on the current `messages` state.
   * This is the core streaming loop — called by both handleSend (new messages) and
   * resendMessage (re-processing).
   */
  async function streamAssistantResponse(sentAttachments: Attachment[] = []) {
     // Pin which conversation this stream belongs to, so it keeps writing
     // to the right place even if the user switches conversations.
     streamingConversationId = activeConversationId;

     const tools = mcpClient.enabledOpenAITools;
     const maxTurns = 10;
     let turn = 0;

      type ContentPart = { type: string; text?: string; image_url?: { url: string }; input_audio?: { data: string; format: string } };
      type ApiMessage = { role: string; content: string | ContentPart[] | null; tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }> } | { role: 'tool'; tool_call_id: string; content: string };
      let apiMessages: ApiMessage[] = [
        { role: 'system', content: configStore.systemPrompt },
        ...messages
          .filter((m) => !m.isStreaming)
          .map((m) => {
            if (m.role === 'user' && (m.imageUrls && m.imageUrls.length > 0 || m.audioData)) {
              const parts: ContentPart[] = [];
              if (m.content.trim()) parts.push({ type: 'text', text: m.content });
              if (m.audioData) parts.push({ type: 'input_audio', input_audio: { data: m.audioData, format: m.audioFormat ?? 'wav' } });
              if (m.imageUrls) for (const url of m.imageUrls) parts.push({ type: 'image_url', image_url: { url } });
              return { role: m.role, content: parts } as ApiMessage;
            }
            return { role: m.role, content: m.content } as ApiMessage;
          })
      ];

      const audioMsg = apiMessages.find((m) => 'content' in m && Array.isArray(m.content) && m.content.some((p: ContentPart) => p.type === 'input_audio'));
      if (audioMsg) {
        const audioPart = (audioMsg.content as ContentPart[]).find((p: ContentPart) => p.type === 'input_audio');
        console.log('[audio] Sending input_audio to model, format:', audioPart?.input_audio?.format, 'data length:', audioPart?.input_audio?.data?.length);
      }

      if (sentAttachments.length > 0) {
        let lastUserMsg = -1;
        for (let i = apiMessages.length - 1; i >= 0; i--) {
          if ('role' in apiMessages[i] && apiMessages[i].role === 'user') { lastUserMsg = i; break; }
        }
        if (lastUserMsg !== -1) {
          const existing = apiMessages[lastUserMsg];
          const textContent = typeof existing.content === 'string' ? existing.content : '';
          const userContent = buildMessageContent(textContent, sentAttachments);
          if (typeof userContent !== 'string') {
            apiMessages[lastUserMsg] = { ...existing, content: userContent } as ApiMessage;
          }
        }
      }

    // Create abort controller for this request cycle
    streamAbortController = new AbortController();

    try {
      while (turn < maxTurns) {
        // If the stream was cancelled between turns, bail out
        if (!streamAbortController) break;
        turn++;
        if (streamingConversationId === activeConversationId) {
          processingLabel = turn === 1 ? 'Processing prompt...' : `Tool call ${turn}...`;
        }

        const assistantId = generateId();
        const assistantMsg: ChatMessage = {
          id: assistantId,
          role: 'assistant',
          content: '',
          timestamp: Date.now(),
          isStreaming: true,
          mcpToolCalls: [],
        };
        pushStreamMessage(assistantMsg);
        isStreaming = true;
        thinkingContent = '';

        const requestBody: Record<string, unknown> = {
          model: selectedModel || undefined,
          messages: apiMessages,
          stream: true,
        };

        if (tools.length > 0) {
          requestBody.tools = tools;
          requestBody.tool_choice = 'auto';
        }

        // If the stream was cancelled between turns, bail out
        if (!streamAbortController) break;

        const response = await fetch(API.llama.chatCompletions, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
          signal: streamAbortController.signal,
        });

        if (!response.ok) {
          const errBody = await response.text().catch(() => '');
          throw new Error(`API ${response.status}: ${errBody.slice(0, 200)}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('No response body');

        const decoder = new TextDecoder();
        let buffer = '';
        let accumulatedContent = '';
        let accumulatedThinking = '';
        let gotFirstToken = false;
        let toolCalls: Array<{ id: string; name: string; arguments: string }> = [];
        let finishReason = '';
        let msgTimings: { promptN?: number; promptMs?: number; predictedN?: number; predictedMs?: number; predictedPerSecond?: number } = {};

        if (streamingConversationId === activeConversationId) {
          processingLabel = 'Generating...';
        }

        // Throttled flush: batches streaming updates to avoid freezing the UI.
        // Instead of updating messages on every single SSE token, we buffer
        // content and flush to state on requestAnimationFrame (~60fps max).
        function scheduleFlush() {
          if (streamingRafId !== null) return; // already scheduled
          streamingRafId = requestAnimationFrame(() => {
            streamingRafId = null;
            if (!streamingBuffer) return;
            const { content, thinking, assistantId: aid } = streamingBuffer;
            streamingBuffer = null;
            updateStreamMessage(aid, (m) => ({
              ...m,
              content,
              ...(thinking ? { thinkingContent: thinking } : {}),
            }));
            // Only update thinkingContent if the stream's conversation is active
            if (thinking && streamingConversationId === activeConversationId) {
              thinkingContent = thinking;
            }
          });
        }

        function updateStreamingState(content: string, thinking: string, aid: string) {
          // Always update the buffer with latest content
          streamingBuffer = { content, thinking, assistantId: aid };
          scheduleFlush();
        }

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmedLine = line.trim();
            if (!trimmedLine || !trimmedLine.startsWith('data: ')) continue;

            const data = trimmedLine.slice(6);
            if (data === '[DONE]') continue;

            try {
              const parsed = JSON.parse(data);
              const choice = parsed.choices?.[0];
              if (!choice) continue;

              const delta = choice.delta;
              if (!delta) continue;

              if (delta.content) {
                if (!gotFirstToken) {
                  gotFirstToken = true;
                  if (streamingConversationId === activeConversationId) {
                    isProcessing = false;
                  }
                }
                accumulatedContent += delta.content;
                // Throttled update: buffer and flush on next animation frame
                updateStreamingState(accumulatedContent, accumulatedThinking, assistantId);
              }

              if (delta.reasoning_content) {
                accumulatedThinking += delta.reasoning_content;
                // Only update global thinking display if viewing the stream's conversation
                if (streamingConversationId === activeConversationId) {
                  thinkingContent = accumulatedThinking;
                }
                // Throttled update for thinking content too
                updateStreamingState(accumulatedContent, accumulatedThinking, assistantId);
              }

              if (delta.tool_calls) {
                for (const tc of delta.tool_calls) {
                  const idx = tc.index ?? toolCalls.length;
                  if (!toolCalls[idx]) {
                    toolCalls[idx] = { id: tc.id || `call_${idx}`, name: '', arguments: '' };
                  }
                  if (tc.id) toolCalls[idx].id = tc.id;
                  if (tc.function?.name) toolCalls[idx].name = tc.function.name;
                  if (tc.function?.arguments) toolCalls[idx].arguments += tc.function.arguments;
                }
              }

              if (choice.finish_reason) {
                finishReason = choice.finish_reason;
              }

              if (parsed.timings) {
                const t = parsed.timings;
                if (t.predicted_per_second) {
                  if (streamingConversationId === activeConversationId) {
                    tokensPerSecond = Math.round(t.predicted_per_second);
                  }
                  msgTimings.predictedPerSecond = t.predicted_per_second;
                }
                if (t.prompt_n) {
                  if (streamingConversationId === activeConversationId) {
                    promptTokens = t.prompt_n;
                  }
                  msgTimings.promptN = t.prompt_n;
                }
                if (t.prompt_ms) msgTimings.promptMs = t.prompt_ms;
                if (t.predicted_n) msgTimings.predictedN = t.predicted_n;
                if (t.predicted_ms) msgTimings.predictedMs = t.predicted_ms;
                // Timings are infrequent — safe to update directly
                updateStreamMessage(assistantId, (m) => ({ ...m, timings: { ...msgTimings } }));
              }

              if (parsed.prompt_progress && streamingConversationId === activeConversationId) {
                processingLabel = `Processing prompt... ${Math.round(parsed.prompt_progress * 100)}%`;
              }
            } catch {
              // skip unparseable lines
            }
          }
        }

        // Flush any remaining buffered content before finalizing
        if (streamingRafId !== null) {
          cancelAnimationFrame(streamingRafId);
          streamingRafId = null;
        }
        streamingBuffer = null;

        // Finalize this assistant message
        updateStreamMessage(assistantId, (m) => ({
          ...m,
          content: accumulatedContent || '',
          isStreaming: false,
          thinkingContent: accumulatedThinking || undefined,
          timings: msgTimings,
          model: selectedModel || undefined,
          mcpToolCalls: toolCalls.length > 0
            ? toolCalls.map((tc) => ({
                id: tc.id,
                toolName: tc.name,
                arguments: JSON.parse(tc.arguments || '{}'),
                timestamp: Date.now(),
              }))
            : m.mcpToolCalls,
        }));

        // If no tool calls, we're done
        if (finishReason !== 'tool_calls' || toolCalls.length === 0) {
          break;
        }

        // Process tool calls via MCP
        const openAIToolCalls = toolCalls.map((tc) => ({
          id: tc.id,
          type: 'function' as const,
          function: { name: tc.name, arguments: tc.arguments },
        }));

        apiMessages.push({
          role: 'assistant',
          content: accumulatedContent || null,
          tool_calls: openAIToolCalls,
        });

        let collectedImageUrls: string[] = [];

        for (const tc of toolCalls) {
          const args = JSON.parse(tc.arguments || '{}');
          let toolResult = '';
          let isToolError = false;

          const toolLabels: Record<string, string> = {
            query_knowledge_graph: 'Searching knowledge graph...',
            query_knowledge_graph_stream: 'Searching knowledge graph...',
            search_entities: 'Searching entities...',
            insert_text: 'Inserting text...',
            edit_entity: 'Editing entity...',
            list_documents: 'Listing documents...',
          };
          if (streamingConversationId === activeConversationId) {
            processingLabel = toolLabels[tc.name] || `Running ${tc.name}...`;
            isProcessing = true;
          }

          try {
            toolResult = await mcpClient.callTool(tc.name, args);
          } catch (err) {
            toolResult = err instanceof Error ? err.message : 'Tool call failed';
            isToolError = true;
          }

          let displayResult = toolResult;
          if (!isToolError && toolResult) {
            const imagePaths = extractImageFilePaths(toolResult);
            for (const imgPath of imagePaths) {
              const url = await lightragClient.resolveImageContentUrl(imgPath);
              if (url) collectedImageUrls.push(url);
            }
            const markerIdx = toolResult.indexOf('---IMAGE_REFS---');
            if (markerIdx !== -1) {
              displayResult = toolResult.slice(0, markerIdx).trimEnd();
              apiMessages.push({
                role: 'tool',
                tool_call_id: tc.id,
                content: displayResult,
              });
            } else {
              apiMessages.push({
                role: 'tool',
                tool_call_id: tc.id,
                content: toolResult,
              });
            }
          } else {
            apiMessages.push({
              role: 'tool',
              tool_call_id: tc.id,
              content: toolResult,
            });
          }

          // Update the message to show tool result
          updateStreamMessage(assistantId, (m) => ({
            ...m,
            mcpToolCalls: m.mcpToolCalls?.map((mtc) =>
              mtc.id === tc.id
                ? { ...mtc, result: displayResult.slice(0, 2000), isError: isToolError }
                : mtc
            ),
          }));

          eventBus.pushEvent({
            id: generateId(),
            type: 'mcp_call',
            title: tc.name,
            description: isToolError ? `Error: ${toolResult.slice(0, 100)}` : toolResult.slice(0, 100),
            timestamp: Date.now(),
            status: isToolError ? 'error' : 'completed',
          });
        }

        if (collectedImageUrls.length > 0) {
          updateStreamMessage(assistantId, (m) => ({
            ...m,
            imageUrls: [...(m.imageUrls || []), ...collectedImageUrls],
          }));
        }

        // Reset for next turn — the loop continues with tool results appended
        isStreaming = false;
        thinkingContent = '';
        tokensPerSecond = null;
        promptTokens = null;
      }
    } catch (err: unknown) {
      // If the stream was aborted (e.g. user switched conversations), don't show an error
      if (err instanceof DOMException && err.name === 'AbortError') {
        // Stream cancelled — the caller (cancelStreaming) already finalized state
        return;
      }
      const errMsg = err instanceof Error ? err.message : 'Unknown error';
      // Look for the streaming message in the stream's conversation
      const conv = streamingConversationId
        ? conversations.find((c) => c.id === streamingConversationId)
        : null;
      const msgSource = (conv && streamingConversationId !== activeConversationId) ? conv.messages : messages;
      const lastStreaming = msgSource.find((m) => m.isStreaming);
      if (lastStreaming) {
        const updater = (m: ChatMessage) =>
          m.id === lastStreaming.id ? { ...m, content: `**Error:** ${errMsg}`, isStreaming: false } : m;
        if (conv && streamingConversationId !== activeConversationId) {
          conv.messages = conv.messages.map(updater);
        } else {
          messages = messages.map(updater);
        }
      }
    } finally {
      streamAbortController = null;
      // Clean up any pending flush
      if (streamingRafId !== null) {
        cancelAnimationFrame(streamingRafId);
        streamingRafId = null;
      }
      streamingBuffer = null;
      isStreaming = false;
      isProcessing = false;
      processingLabel = '';
      thinkingContent = '';
      tokensPerSecond = null;
      promptTokens = null;

      // Save messages for the stream's conversation
      if (streamingConversationId) {
        const conv = conversations.find((c) => c.id === streamingConversationId);
        if (conv) {
          // If stream was in background, messages state points to a different conversation.
          // Sync the background conversation's messages if needed.
          if (streamingConversationId !== activeConversationId) {
            conv.messages = conv.messages.map((m) =>
              m.isStreaming ? { ...m, isStreaming: false } : m
            );
          } else {
            conv.messages = [...messages];
          }
          conv.updatedAt = Date.now();
          syncClient.saveConversation(conv);
        }
        // Mark as unread if stream completed in background
        if (streamingConversationId !== activeConversationId) {
          unreadConversations = new Set([...unreadConversations, streamingConversationId]);
        }
      }
      streamingConversationId = null;

      // If currently viewing the stream's conversation, scroll to bottom
      requestAnimationFrame(scrollToBottom);
    }
  }

  async function handleSend(audioUrl?: string, audioData?: string, audioFormat?: 'wav' | 'mp3', startNew = false) {
    const trimmed = chatInput.trim();
    if ((!trimmed && attachments.length === 0) && !audioData) return;
    if (isActiveConversationStreaming) return;

    chatExpanded = true;
    isProcessing = true;
    processingLabel = 'Sending...';

    if (startNew || !activeConversationId) {
      startNewConversation();
    }

    const imageUrls = attachments
      .filter((a) => isImageType(a.mimeType))
      .map((a) => a.dataUrl);

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
      ...(audioUrl ? { audioUrl } : {}),
      ...(audioData ? { audioData, audioFormat: audioFormat ?? 'wav' } : {}),
      ...(imageUrls.length > 0 ? { imageUrls } : {}),
    };
    messages = [...messages, userMsg];
    saveMessagesToConversation();

    const conv = conversations.find((c) => c.id === activeConversationId);
    if (conv && !conv.title) {
      conv.title = trimmed.slice(0, 50) + (trimmed.length > 50 ? '…' : '');
    }
    conv?.updatedAt && (conv.updatedAt = Date.now());

    const sentAttachments = [...attachments];
    chatInput = '';
    revokeAttachmentUrls(attachments);
    attachments = [];
    attachError = '';
    requestAnimationFrame(() => {
      if (textareaEl) textareaEl.style.height = 'auto';
      scrollToBottom();
    });

    // Images are now processed immediately on attach via processSingleImage
    // Audio requires multimodal content parts, so always route through LLM path
    if (chatMode === 'kg-direct' && !audioData) {
      await streamKGChatResponse();
    } else {
      await streamAssistantResponse(sentAttachments);
    }
  }

  /** Process a single image through the KG pipeline with real-time SSE progress. */
  async function processSingleImage(att: Attachment) {
    const photoNodeId = `${att.name} (Photo)`;
    try {
      const job = await kgApiClient.createJob(att.file, { insert: true });
      imageProcessingStore.startProcessing(photoNodeId, att.name, att.dataUrl ?? '', job.job_id);
      await consumeJobEvents(job.job_id, photoNodeId, att);
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return;
      console.warn(`KG image processing failed for ${att.name}:`, err);
      imageProcessingStore.updateStage(photoNodeId, 'error', err instanceof Error ? err.message : 'Unknown error');
      eventBus.pushEvent({
        id: crypto.randomUUID(),
        type: 'graph_update',
        title: 'Image processing failed',
        description: `${att.name}: ${err instanceof Error ? err.message : 'Unknown error'}`,
        timestamp: Date.now(),
        status: 'error',
      });
    }
  }

  /** Consume SSE events from a job, with automatic reconnect on page reload. */
  async function consumeJobEvents(jobId: string, photoNodeId: string, att: Attachment, afterEventId: number = 0) {
    const { stream, cancel } = kgApiClient.streamJobEvents(jobId, afterEventId);

    for await (const sseEvent of stream) {
      let payload: { event?: string; data?: Record<string, unknown>; timestamp?: number; event_id?: number };
      try {
        payload = JSON.parse(sseEvent.data);
      } catch {
        continue;
      }

      const eventName = payload.event;
      const eventData = payload.data ?? {};
      if (!eventName) continue;

      const mappedStage = imageProcessingStore.mapEventToStage(eventName);
      if (mappedStage) {
        const errorMsg = eventName.endsWith('_failed') || eventName.endsWith('_error') || eventName.endsWith('_timeout')
          ? String(eventData.error ?? eventData.message ?? 'Unknown error') : undefined;
        imageProcessingStore.updateStage(photoNodeId, mappedStage, errorMsg);
      }

      if (eventName === 'exif_complete' && eventData.exif && typeof eventData.exif === 'object') {
        imageProcessingStore.setExifData(photoNodeId, eventData.exif as Record<string, unknown>);
      }

      eventBus.pushEvent({
        id: crypto.randomUUID(),
        type: 'graph_update',
        title: eventName.replace(/_/g, ' '),
        description: String(eventData.name ?? eventData.entity_name ?? eventData.message ?? att.name),
        timestamp: Date.now(),
        status: eventName.endsWith('_complete') || eventName.endsWith('_created') || eventName === 'pipeline_complete'
          ? 'completed'
          : eventName.endsWith('_failed') || eventName.endsWith('_timeout')
            ? 'error'
            : 'running',
        meta: eventData as Record<string, unknown>,
      });

      if (eventName === 'photo_node_created' || eventName === 'exif_node_created') {
        const nodeId = String(eventData.entity_name ?? eventData.name ?? eventData.id ?? '');
        const labels = Array.isArray(eventData.labels) ? eventData.labels : [eventName === 'photo_node_created' ? 'Photo' : 'ExifEntity'];
        graphStore.upsertNode(nodeId, labels, eventData as Record<string, unknown>);
        if (eventName === 'photo_node_created' && att.dataUrl) {
          graphStore.setPhotoImage(nodeId, att.dataUrl);
        }
      } else if (eventName === 'visual_entity_linked') {
        const sourceId = String(eventData.source ?? eventData.photo_name ?? '');
        const targetId = String(eventData.target ?? eventData.entity_name ?? '');
        const edgeType = String(eventData.relation_type ?? 'depicts');
        graphStore.upsertEdge(sourceId, targetId, edgeType, eventData as Record<string, unknown>);
      } else if (eventName === 'exif_relation_created') {
        const sourceId = String(eventData.source ?? '');
        const targetId = String(eventData.target ?? '');
        const edgeType = String(eventData.relation_type ?? 'has_exif');
        graphStore.upsertEdge(sourceId, targetId, edgeType, eventData as Record<string, unknown>);
      }

      if (eventName === 'pipeline_complete' || eventName === 'pipeline_failed') {
        graphStore.pipelineDone = true;
        graphStore.refresh();
        cancel();
        return;
      }
    }
  }

  /** Send image attachments through the KG pipeline with real-time SSE progress. */
  async function processImageAttachments(atts: Attachment[]) {
    const imageFiles = atts.filter((a) => isImageType(a.mimeType) && a.file);
    for (const att of imageFiles) {
      processSingleImage(att);
    }
  }

  /** On page load, reconnect to any in-progress jobs from the server. */
  async function resumeInProgressJobs() {
    try {
      const jobs = await kgApiClient.listJobs('processing');
      for (const job of jobs) {
        const photoNodeId = `${job.file_source} (Photo)`;
        const existing = imageProcessingStore.getByJobId(job.job_id);
        if (existing) {
          await consumeJobEvents(job.job_id, existing.nodeId, { name: job.file_source, dataUrl: '' } as Attachment);
        } else {
          imageProcessingStore.startProcessing(photoNodeId, job.file_source, '', job.job_id);
          graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', source_id: job.file_source });
          await consumeJobEvents(job.job_id, photoNodeId, { name: job.file_source, dataUrl: '' } as Attachment);
        }
      }
    } catch (err) {
      console.warn('Failed to resume in-progress jobs:', err);
    }
  }

  /**
   * Resend a user message: removes the assistant response (and any trailing
   * messages) after the given user message, then re-triggers the LLM stream
   * with the same conversation history up to that point.
   */
  function resendMessage(msgId: string) {
    if (isActiveConversationStreaming) return;
    const msgIdx = messages.findIndex((m) => m.id === msgId);
    if (msgIdx === -1) return;
    const msg = messages[msgIdx];
    if (msg.role !== 'user') return;

    // Truncate everything after this user message
    messages = messages.slice(0, msgIdx + 1);
    saveMessagesToConversation();
    chatExpanded = true;

    isProcessing = true;
    processingLabel = 'Regenerating...';

    // Stream a new assistant response using the existing conversation history
    if (chatMode === 'kg-direct') {
      streamKGChatResponse();
    } else {
      streamAssistantResponse();
    }
  }

  function deleteConversation(id: string) {
    cancelStreaming();
    conversations = conversations.filter((c) => c.id !== id);
    syncClient.deleteConversation(id);
    if (activeConversationId === id) {
      activeConversationId = '';
      messages = [];
      chatExpanded = false;
    }
  }

  function exportConversationToJsonl(id: string) {
    // Sync current messages into the conversation object before exporting,
    // otherwise conv.messages may be stale (the active conversation's
    // messages live in the separate `messages` reactive state).
    if (id === activeConversationId) {
      saveMessagesToConversation();
    }

    const conv = conversations.find((c) => c.id === id);
    if (!conv) return;

    const sessionLine = JSON.stringify({
      type: 'session',
      harness: 'nexus',
      id: conv.id,
      name: conv.title || 'New conversation',
      lastModified: conv.updatedAt,
    });

    const messageLines = conv.messages
      .filter((m) => !m.isStreaming)
      .map((m) => {
        const msg: Record<string, unknown> = {
          id: m.id,
          convId: conv.id,
          type: m.role === 'user' ? 'user' : m.role === 'assistant' ? 'assistant' : 'system',
          timestamp: m.timestamp,
          role: m.role,
          content: m.content,
        };
        if (m.thinkingContent) msg.reasoningContent = m.thinkingContent;
        if (m.model) msg.model = m.model;
        if (m.timings) msg.timings = m.timings;
        if (m.mcpToolCalls && m.mcpToolCalls.length > 0) {
          msg.toolCalls = m.mcpToolCalls.map((tc) => ({
            id: tc.id,
            name: tc.toolName,
            arguments: tc.arguments,
            result: tc.result,
            isError: tc.isError,
          }));
        }
        return JSON.stringify({ type: 'message', message: msg });
      });

    const jsonl = [sessionLine, ...messageLines].join('\n');
    const blob = new Blob([jsonl], { type: 'application/jsonl' });

    const sanitizedName = (conv.title || 'conversation')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/_+/g, '_')
      .slice(0, 50);
    const date = new Date(conv.updatedAt).toISOString().slice(0, 16).replace(/[T:]/g, '-');
    const filename = `${date}_conv_${conv.id.slice(0, 8)}_${sanitizedName}.jsonl`;

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function scrollToBottom() {
    if (messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handlePanelKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handlePanelSend();
    }
  }

  function autoResize() {
    if (!textareaEl) return;
    textareaEl.style.height = 'auto';
    const lineH = parseInt(getComputedStyle(textareaEl).lineHeight) || 24;
    textareaEl.style.height = Math.min(textareaEl.scrollHeight, lineH * 6) + 'px';
  }

  function autoResizePanel() {
    if (!panelTextareaEl) return;
    panelTextareaEl.style.height = 'auto';
    const lineH = parseInt(getComputedStyle(panelTextareaEl).lineHeight) || 24;
    panelTextareaEl.style.height = Math.min(panelTextareaEl.scrollHeight, lineH * 4) + 'px';
  }

  function handlePanelSend() {
    const trimmed = panelChatInput.trim();
    if ((!trimmed && attachments.length === 0) || isActiveConversationStreaming) return;
    chatInput = trimmed;
    panelChatInput = '';
    requestAnimationFrame(() => {
      if (panelTextareaEl) panelTextareaEl.style.height = 'auto';
    });
      handleSend(undefined, undefined, undefined, false);
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
      // models remain empty when backend unavailable
    }
  }

  function closeChat() {
    // Don't cancel streaming — just hide the panel. The stream continues
    // in the background so the response is preserved when you reopen.
    saveMessagesToConversation();
    chatExpanded = false;
  }

  function formatTime(ts: number): string {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function formatModelName(id: string): string {
    const parts = id.split('/');
    return parts[parts.length - 1].replace(/\.gguf$/, '').slice(0, 25);
  }

  // Memoized markdown rendering — caches parsed HTML by content hash to avoid
  // re-parsing the same content on every render during streaming.
  const markdownCache = new Map<string, string>();
  const MAX_MARKDOWN_CACHE = 200;

  function renderMarkdown(text: string): string {
    if (!text) return '';
    // For short/incomplete streaming content, skip heavy parsing and just escape
    // This prevents the UI from freezing on every token during streaming.
    // The full markdown render happens once streaming completes.
    const cached = markdownCache.get(text);
    if (cached !== undefined) return cached;

    const html = marked.parse(text, { async: false }) as string;
    const result = DOMPurify.sanitize(html);

    // Evict old entries if cache is too large
    if (markdownCache.size >= MAX_MARKDOWN_CACHE) {
      const firstKey = markdownCache.keys().next().value;
      if (firstKey !== undefined) markdownCache.delete(firstKey);
    }
    markdownCache.set(text, result);
    return result;
  }

  // Lightweight render for streaming content — just escape HTML, skip markdown parsing
  function renderStreamingContent(text: string): string {
    if (!text) return '';
    const escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>');
    return escaped;
  }

  function formatDuration(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function formatTokens(n: number | undefined): string {
    if (n === undefined) return '';
    return n.toLocaleString();
  }

  function formatJsonSafe(str: string): string {
    try { return JSON.stringify(JSON.parse(str), null, 2); } catch { return str; }
  }

  async function copyToClipboard(text: string) {
    try { await navigator.clipboard.writeText(text); } catch {}
  }

  $effect(() => {
    configStore.load();
    fetchModels();
    syncClient.init().then(() => {
      conversations = [...syncClient.conversations];
      if (conversations.length > 0 && !activeConversationId) {
        activeConversationId = conversations[0].id;
        chatExpanded = true;
        const conv = conversations[0];
        if (conv.messages.length === 0) {
          syncClient.loadConversation(conv.id).then((loaded) => {
            if (loaded.length > 0) {
              conv.messages = loaded;
              messages = [...loaded];
            }
          });
        } else {
          messages = [...conv.messages];
        }
      }
      syncClient.startPeriodicSync(30_000, (updated) => {
        conversations = [...updated];
      });
    });
  });

  $effect(() => {
    conversationStore.conversations = conversations;
    conversationStore.activeConversationId = activeConversationId;
    conversationStore.unreadConversations = unreadConversations;
  });

  let lastNavigatedId = '';
  let lastNavigateCount = 0;
  $effect(() => {
    const storeId = conversationStore.activeConversationId;
    const count = conversationStore.navigateCount;
    if (storeId && (storeId !== lastNavigatedId || count !== lastNavigateCount)) {
      lastNavigatedId = storeId;
      lastNavigateCount = count;
      switchConversation(storeId);
    }
  });
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="relative h-full w-full overflow-hidden"
  ondragover={handleDragOver}
  ondragenter={handleDragEnter}
  ondragleave={handleDragLeave}
  ondrop={handleDrop}
>
  {#if isDraggingOver}
    <div class="pointer-events-none absolute inset-0 z-50 flex items-center justify-center bg-cyber-surface/80 backdrop-blur-sm">
      <div class="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-cyber-cyan/60 px-12 py-8">
        <Icon name="image" size={32} color="var(--color-cyber-cyan)" />
        <div class="text-sm font-medium text-cyber-cyan">Drop images to add to graph</div>
        <div class="text-xs text-cyber-text-dim/60">EXIF data will be extracted automatically</div>
      </div>
    </div>
  {/if}
  {#if $activeTab === 'graph'}
    <div class="absolute inset-0">
      <GraphView />
    </div>

    <!-- Floating chat input -->
    <div class="chat-input-bar">
      <div class="relative rounded-2xl border border-cyber-border/60 bg-cyber-surface/90 shadow-[0_0_30px_rgba(0,212,255,0.08)] backdrop-blur-xl transition-all duration-300 hover:border-cyber-cyan/30 hover:shadow-[0_0_40px_rgba(0,212,255,0.15)]">


        {#if thinkingContent}
          <div class="mx-4 mb-1 rounded-lg border border-cyber-purple/20 bg-cyber-purple/5 px-3 py-1.5">
            <div class="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-cyber-purple">
              <Icon name="zap" size={10} color="var(--color-cyber-purple)" />
              Thinking
            </div>
            <div class="max-h-20 overflow-y-auto text-[11px] leading-relaxed text-cyber-text-dim/70 whitespace-pre-wrap">{thinkingContent.slice(-300)}</div>
          </div>
        {/if}

        {#if attachError}
          <div class="mx-4 mb-1 text-xs text-cyber-red">{attachError}</div>
        {/if}

        <div class="flex items-center gap-2 px-4 py-3">
          <button
            onclick={() => { historyPanelOpen.update((v) => !v); }}
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyber-surface-2/50 text-cyber-text-dim/60 transition-all duration-200 hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
            title="Chat history"
            aria-label="Open chat history"
          >
            <Icon name="clock" size={16} />
          </button>
          <input
            bind:this={imageFileInput}
            type="file"
            accept="image/*"
            multiple
            class="hidden"
            data-testid="image-file-input"
            onchange={(e) => handleAttachFiles((e.target as HTMLInputElement).files)}
          />
          <input
            bind:this={docFileInput}
            type="file"
            accept=".txt,.md,.csv,.json,.html,.htm,.xml,.yaml,.yml,.log"
            multiple
            class="hidden"
            data-testid="doc-file-input"
            onchange={(e) => handleAttachFiles((e.target as HTMLInputElement).files)}
          />
          <AttachmentMenu
            size="sm"
            disabled={isActiveConversationStreaming || attachments.length >= MAX_ATTACHMENTS}
            onPickImage={openImagePicker}
            onPickDocument={openDocumentPicker}
          />
          <button
            onclick={() => { chatMode = chatMode === 'kg-direct' ? 'llm-mcp' : 'kg-direct'; }}
            class="flex h-8 shrink-0 items-center gap-1 rounded-lg px-2 text-[11px] font-medium uppercase tracking-wide transition-all duration-200 {chatMode === 'kg-direct' ? 'bg-cyber-cyan/15 text-cyber-cyan ring-1 ring-cyber-cyan/30' : 'bg-cyber-surface-2/50 text-cyber-text-dim/60 hover:bg-cyber-cyan/10 hover:text-cyber-cyan'}"
            title={chatMode === 'kg-direct' ? 'Direct KG chat — LightRAG handles retrieval and generation' : 'LLM + MCP — LLM calls KG tools via MCP'}
            disabled={isActiveConversationStreaming}
          >
            <Icon name={chatMode === 'kg-direct' ? 'database' : 'cpu'} size={14} />
            <span class="hidden sm:inline">{chatMode === 'kg-direct' ? 'KG' : 'LLM'}</span>
          </button>
          <textarea
            bind:this={textareaEl}
            bind:value={chatInput}
            oninput={autoResize}
            onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(undefined, undefined, undefined, true); } }}
            placeholder={chatMode === 'kg-direct' ? 'Ask your knowledge graph...' : 'Ask me anything...'}
            rows="1"
            data-testid="chat-input"
            class="max-h-[144px] min-h-[24px] flex-1 resize-none bg-transparent text-sm text-cyber-text outline-none placeholder:text-cyber-text-dim/40"
            disabled={isActiveConversationStreaming}
          ></textarea>
          {#if isActiveConversationStreaming}
            <button
              onclick={cancelStreaming}
              class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cyber-red/20 text-cyber-red transition-all duration-200 hover:bg-cyber-red/30 ring-2 ring-cyber-red/40"
              title="Stop generating"
            >
              <Icon name="square" size={14} />
            </button>
          {:else}
            <button
              onclick={() => chatInput.trim() || attachments.length > 0 ? handleSend(undefined, undefined, undefined, true) : handleMicClick()}
              disabled={isTranscribing || (!chatInput.trim() && attachments.length === 0 && !recordingSupported)}
              data-testid="send-button"
              class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-all duration-200 {isRecording ? 'bg-red-500/20 text-red-400 animate-pulse hover:bg-red-500/30 ring-2 ring-red-500/40' : isTranscribing ? 'bg-cyber-cyan/10 text-cyber-cyan animate-pulse ring-2 ring-cyber-cyan/30' : (chatInput.trim() || attachments.length > 0) ? 'bg-cyber-cyan/20 text-cyber-cyan hover:bg-cyber-cyan/30 hover:glow-cyan rounded-lg' : recordingSupported ? 'bg-cyber-surface-2/80 text-cyber-text-dim/80 hover:bg-cyber-cyan/15 hover:text-cyber-cyan ring-1 ring-cyber-border/60' : 'bg-cyber-surface-2/50 text-cyber-text-dim/30 rounded-lg'}"
            >
              {#if isRecording}
                <Icon name="square" size={14} />
              {:else if isTranscribing}
                <div class="h-3.5 w-3.5 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
              {:else if chatInput.trim()}
                <Icon name="send" size={16} />
              {:else if recordingSupported}
                <Icon name="mic" size={18} />
              {:else}
                <Icon name="send" size={16} />
              {/if}
            </button>
          {/if}
        </div>
      </div>
    </div>

    <!-- Chat overlay panel -->
    {#if chatExpanded && activeConversationId}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="chat-overlay-backdrop" onclick={closeChat} role="presentation"></div>
      <div class="chat-overlay-panel">
        <div class="flex items-center justify-between border-b border-cyber-border/50 px-4 py-3">
          <div class="flex items-center gap-2 min-w-0">
            <div class="h-2 w-2 shrink-0 rounded-full {isStreaming ? 'bg-cyber-cyan animate-pulse-glow' : 'bg-cyber-green'}"></div>
            <span class="truncate text-sm font-medium text-cyber-text">{conversations.find(c => c.id === activeConversationId)?.title || 'New conversation'}</span>
          </div>
          <div class="flex items-center gap-2">
            <button
              onclick={() => exportConversationToJsonl(activeConversationId)}
              class="flex h-6 w-6 items-center justify-center rounded text-cyber-text-dim transition-colors hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
              title="Export conversation"
            >
              <Icon name="download" size={14} />
            </button>
            <button
              onclick={() => deleteConversation(activeConversationId)}
              class="flex h-6 w-6 items-center justify-center rounded text-cyber-text-dim transition-colors hover:bg-cyber-red/10 hover:text-cyber-red"
              title="Delete conversation"
            >
              <Icon name="trash-2" size={14} />
            </button>
            <button
              onclick={closeChat}
              class="flex h-7 w-7 items-center justify-center rounded-md text-cyber-text-dim transition-colors hover:bg-cyber-surface-2 hover:text-cyber-text"
            >
              <Icon name="x" size={16} />
            </button>
          </div>
        </div>

        <!-- Messages -->
        <div bind:this={messagesContainer} class="flex-1 overflow-y-auto px-4 py-3">
          {#each messages as msg (msg.id)}
            <div class="group mb-4 animate-fade-in-up">
              {#if msg.role === 'user'}
                <div class="flex justify-end">
                  <div class="max-w-[80%] space-y-1">
                    {#if msg.imageUrls && msg.imageUrls.length > 0}
                      <ImageGallery images={msg.imageUrls} alt="Uploaded image" />
                    {/if}
                    {#if msg.audioUrl}
                      <audio src={msg.audioUrl} controls class="h-8 w-full max-w-[240px] rounded-lg" style="filter: invert(0.7) hue-rotate(180deg);" preload="metadata"></audio>
                    {/if}
                    <div class="rounded-2xl rounded-br-sm bg-cyber-cyan/10 px-4 py-2.5 text-sm text-cyber-text border border-cyber-cyan/20">
                      {msg.content}
                    </div>
                  </div>
                </div>
                <div class="mt-0.5 flex items-center justify-end gap-2">
                  <span class="text-[10px] text-cyber-text-dim/50">{formatTime(msg.timestamp)}</span>
                  {#if !isActiveConversationStreaming}
                    <button
                      onclick={() => resendMessage(msg.id)}
                      class="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 text-[10px] text-cyber-text-dim/50 hover:text-cyber-cyan"
                      title="Regenerate response"
                    >
                      <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                      Regenerate
                    </button>
                  {/if}
                </div>
              {:else}
                <div class="flex justify-start">
                  <div class="max-w-[90%] space-y-1.5">
                    {#if msg.thinkingContent}
                      <details class="group" open={msg.isStreaming}>
                        <summary class="flex cursor-pointer items-center gap-1.5 rounded-lg border border-cyber-purple/25 bg-cyber-purple/5 px-2.5 py-1.5 text-[11px] text-cyber-purple hover:bg-cyber-purple/10 transition-colors">
                          <svg class="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.663 17h4.673M12 3v1m0 16v1m-8-9H3m18 0h-1M5.636 5.636l-.707-.707M18.364 18.364l-.707-.707M5.636 18.364l-.707.707M18.364 5.636l-.707.707" stroke-linecap="round" stroke-linejoin="round"/></svg>
                          <span class="font-medium">Thinking</span>
                          <span class="text-cyber-text-dim/50">{msg.thinkingContent.length} chars</span>
                          <svg class="ml-auto h-3 w-3 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                        </summary>
                        <div class="mt-1 max-h-48 overflow-y-auto rounded-lg border border-cyber-purple/10 bg-cyber-bg/50 p-2.5 text-[11px] leading-relaxed text-cyber-text-dim/80 whitespace-pre-wrap font-mono">{msg.thinkingContent}</div>
                      </details>
                    {/if}

                    <div class="rounded-2xl rounded-bl-sm bg-cyber-surface-2/80 px-4 py-2.5 text-sm text-cyber-text border border-cyber-border/30">
                      {#if msg.isStreaming && isProcessing}
                        <div class="flex items-center gap-2 py-1">
                          <div class="flex-1 h-1.5 rounded-full bg-cyber-border/40 overflow-hidden">
                            <div class="h-full rounded-full bg-cyber-orange animate-pulse" style="width: {processingLabel.includes('%') ? processingLabel.match(/(\d+)%/)?.[1] ?? '0' : '100'}%"></div>
                          </div>
                          <span class="shrink-0 text-[11px] text-cyber-orange">{processingLabel}</span>
                          {#if promptTokens}
                            <span class="shrink-0 font-mono text-[10px] text-cyber-text-dim">{promptTokens} tokens</span>
                          {/if}
                        </div>
                      {/if}
                      {#if msg.content}
                        <div class="prose-cyber">{@html msg.isStreaming ? renderStreamingContent(msg.content) : renderMarkdown(msg.content)}</div>
                      {/if}
                      {#if msg.isStreaming && !msg.content && !isProcessing}
                        <div class="flex items-center gap-2 py-1">
                          <div class="flex gap-1">
                            <span class="inline-block h-2 w-2 animate-bounce rounded-full bg-cyber-cyan" style="animation-delay: 0ms"></span>
                            <span class="inline-block h-2 w-2 animate-bounce rounded-full bg-cyber-cyan" style="animation-delay: 150ms"></span>
                            <span class="inline-block h-2 w-2 animate-bounce rounded-full bg-cyber-cyan" style="animation-delay: 300ms"></span>
                          </div>
                          <span class="text-xs text-cyber-text-dim">Generating...</span>
                          <button
                            onclick={cancelStreaming}
                            class="ml-1 flex items-center gap-1 rounded-md border border-cyber-red/30 bg-cyber-red/5 px-2 py-0.5 text-[10px] text-cyber-red transition-colors hover:border-cyber-red/50 hover:bg-cyber-red/10"
                            title="Stop generating"
                          >
                            <svg class="h-2.5 w-2.5" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1.5" /></svg>
                            Stop
                          </button>
                        </div>
                      {/if}
                      {#if msg.isStreaming && msg.content}
                        <span class="inline-flex items-center gap-1.5">
                          <span class="inline-block h-4 w-1 animate-pulse bg-cyber-cyan align-text-bottom"></span>
                          <button
                            onclick={cancelStreaming}
                            class="inline-flex items-center gap-0.5 rounded-md border border-cyber-red/30 bg-cyber-red/5 px-1.5 py-0.5 text-[10px] text-cyber-red transition-colors hover:border-cyber-red/50 hover:bg-cyber-red/10"
                            title="Stop generating"
                          >
                            <svg class="h-2.5 w-2.5" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1.5" /></svg>
                            Stop
                          </button>
                        </span>
                      {/if}
                      {#if msg.imageUrls && msg.imageUrls.length > 0}
                        <ImageGallery images={msg.imageUrls} alt="Knowledge graph image" />
                      {/if}
                      {#if msg.isStreaming && tokensPerSecond && msg.content}
                        <div class="mt-1.5 flex items-center gap-1.5 text-[10px]">
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-green/10 px-1.5 py-0.5 font-mono text-cyber-green">
                            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                            {tokensPerSecond} t/s
                          </span>
                        </div>
                      {/if}
                    </div>

                    {#if !msg.isStreaming && msg.content}
                      <div class="flex items-center gap-2">
                        {#if msg.role === 'assistant'}
                          <button
                            onclick={() => {
                              // Find the user message right before this assistant message
                              const msgIdx = messages.findIndex((m) => m.id === msg.id);
                              if (msgIdx > 0) {
                                const prevMsg = messages[msgIdx - 1];
                                if (prevMsg.role === 'user') resendMessage(prevMsg.id);
                              }
                            }}
                            class="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 text-[10px] text-cyber-text-dim/50 hover:text-cyber-cyan"
                            title="Regenerate response"
                          >
                            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                            Regenerate
                          </button>
                        {/if}
                        <button
                          onclick={() => copyToClipboard(msg.content)}
                          class="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 text-[10px] text-cyber-text-dim/50 hover:text-cyber-cyan"
                          title="Copy message"
                        >
                          <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                          Copy
                        </button>
                      </div>
                    {/if}

                    {#if msg.mcpToolCalls && msg.mcpToolCalls.length > 0}
                      {#each msg.mcpToolCalls as toolCall (toolCall.id || toolCall.toolName + toolCall.timestamp)}
                        <details class="group" open={true}>
                          <summary class="flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] transition-colors {toolCall.isError ? 'border-cyber-red/30 bg-cyber-red/5 hover:bg-cyber-red/10' : toolCall.result ? 'border-cyber-green/30 bg-cyber-green/5 hover:bg-cyber-green/10' : 'border-cyber-orange/30 bg-cyber-orange/5 hover:bg-cyber-orange/10'}">
                            {#if toolCall.isError}
                              <svg class="h-3.5 w-3.5 shrink-0 text-cyber-red" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                            {:else if toolCall.result}
                              <svg class="h-3.5 w-3.5 shrink-0 text-cyber-green" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
                            {:else}
                              <span class="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-cyber-orange"></span>
                            {/if}
                            <span class="font-mono {toolCall.isError ? 'text-cyber-red' : toolCall.result ? 'text-cyber-green' : 'text-cyber-orange'}">{toolCall.toolName}</span>
                            <span class="ml-auto text-cyber-text-dim/40">{formatTime(toolCall.timestamp)}</span>
                            <svg class="h-3 w-3 text-cyber-text-dim/50 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                          </summary>
                          <div class="mt-1 space-y-1.5 rounded-lg border border-cyber-border/20 bg-cyber-bg/50 p-2.5">
                            <div>
                              <div class="mb-1 text-[10px] font-medium uppercase tracking-wider text-cyber-text-dim/60">Arguments</div>
                              <pre class="max-h-32 overflow-auto rounded bg-cyber-surface-2/50 p-2 font-mono text-[11px] text-cyber-text leading-relaxed">{JSON.stringify(toolCall.arguments, null, 2)}</pre>
                            </div>
                            {#if toolCall.result}
                              <div>
                                <div class="mb-1 text-[10px] font-medium uppercase tracking-wider text-cyber-text-dim/60">Result</div>
                                <pre class="max-h-48 overflow-auto rounded bg-cyber-surface-2/50 p-2 font-mono text-[11px] leading-relaxed {toolCall.isError ? 'text-cyber-red' : 'text-cyber-green'}">{formatJsonSafe(toolCall.result ?? '')}</pre>
                              </div>
                            {/if}
                          </div>
                        </details>
                      {/each}
                    {/if}

                    {#if msg.timings}
                      <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
                        {#if msg.timings.promptN}
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-cyan/10 px-2 py-0.5 font-mono text-cyber-cyan">
                            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 014 4v14a3 3 0 00-3-3H2z"/><path d="M22 3h-6a4 4 0 00-4 4v14a3 3 0 013-3h7z"/></svg>
                            {formatTokens(msg.timings.promptN)} prompt
                          </span>
                        {/if}
                        {#if msg.timings.predictedN}
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-green/10 px-2 py-0.5 font-mono text-cyber-green">
                            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20V10M6 20V4M18 20v-6"/></svg>
                            {formatTokens(msg.timings.predictedN)} out
                          </span>
                        {/if}
                        {#if msg.timings.predictedPerSecond}
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-purple/10 px-2 py-0.5 font-mono text-cyber-purple">
                            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                            {msg.timings.predictedPerSecond.toFixed(1)} t/s
                          </span>
                        {/if}
                        {#if msg.timings.promptMs}
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-border/20 px-2 py-0.5 font-mono text-cyber-text-dim">
                            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                            prompt {formatDuration(msg.timings.promptMs)}
                          </span>
                        {/if}
                        {#if msg.timings.predictedMs}
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-border/20 px-2 py-0.5 font-mono text-cyber-text-dim">
                            gen {formatDuration(msg.timings.predictedMs)}
                          </span>
                        {/if}
                        {#if msg.model}
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-surface-2 px-2 py-0.5 text-cyber-text-dim">
                            {formatModelName(msg.model)}
                          </span>
                        {/if}
                      </div>
                    {/if}

                    <div class="text-[10px] text-cyber-text-dim/50">{formatTime(msg.timestamp)}</div>
                  </div>
                </div>
              {/if}
            </div>
          {/each}
        </div>

        <!-- Input inside overlay -->
        <div class="border-t border-cyber-border/50 px-4 py-3">
          {#if thinkingContent}
            <div class="mb-2 rounded-lg border border-cyber-purple/20 bg-cyber-purple/5 px-3 py-1.5">
              <div class="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-cyber-purple">
                <span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-cyber-purple"></span>
                Thinking...
              </div>
              <div class="mt-1 max-h-16 overflow-y-auto text-[11px] text-cyber-text-dim/70 whitespace-pre-wrap">{thinkingContent.slice(-200)}</div>
            </div>
          {/if}

          <div class="flex items-center gap-2">
            <button
              onclick={() => { historyPanelOpen.update((v) => !v); }}
              class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-cyber-surface-2/50 text-cyber-text-dim/60 transition-all duration-200 hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
              title="Chat history"
              aria-label="Open chat history"
            >
              <Icon name="clock" size={16} />
            </button>
            <AttachmentMenu
              disabled={isActiveConversationStreaming || attachments.length >= MAX_ATTACHMENTS}
              onPickImage={openImagePicker}
              onPickDocument={openDocumentPicker}
            />
            <textarea
              bind:this={panelTextareaEl}
              bind:value={panelChatInput}
              oninput={autoResizePanel}
              onkeydown={handlePanelKeydown}
              placeholder="Continue..."
              rows="1"
              data-testid="panel-chat-input"
              class="max-h-[96px] min-h-[24px] flex-1 resize-none rounded-lg bg-cyber-surface-2/50 px-3 py-2 text-sm text-cyber-text outline-none placeholder:text-cyber-text-dim/40 border border-cyber-border/30 focus:border-cyber-cyan/40 transition-colors"
              disabled={isActiveConversationStreaming}
            ></textarea>
            {#if isActiveConversationStreaming}
              <button
                onclick={cancelStreaming}
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cyber-red/20 text-cyber-red transition-all duration-200 hover:bg-cyber-red/30 ring-2 ring-cyber-red/40"
                title="Stop generating"
              >
                <Icon name="square" size={14} />
              </button>
            {:else}
              <button
              onclick={() => panelChatInput.trim() || attachments.length > 0 ? handlePanelSend() : handleMicClick()}
                disabled={isTranscribing || (!panelChatInput.trim() && attachments.length === 0 && !recordingSupported)}
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-all duration-200 {isRecording ? 'bg-red-500/20 text-red-400 animate-pulse hover:bg-red-500/30 ring-2 ring-red-500/40' : isTranscribing ? 'bg-cyber-cyan/10 text-cyber-cyan animate-pulse ring-2 ring-cyber-cyan/30' : (panelChatInput.trim() || attachments.length > 0) ? 'bg-cyber-cyan/20 text-cyber-cyan hover:bg-cyber-cyan/30 glow-cyan rounded-lg' : recordingSupported ? 'bg-cyber-surface-2/80 text-cyber-text-dim/80 hover:bg-cyber-cyan/15 hover:text-cyber-cyan ring-1 ring-cyber-border/60' : 'bg-cyber-surface-2/30 text-cyber-text-dim/30 rounded-lg'}"
              >
                {#if isRecording}
                  <Icon name="square" size={14} />
                {:else if isTranscribing}
                  <div class="h-3.5 w-3.5 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
                {:else if panelChatInput.trim()}
                  <Icon name="send" size={16} />
                {:else if recordingSupported}
                  <Icon name="mic" size={18} />
                {:else}
                  <Icon name="send" size={16} />
                {/if}
              </button>
            {/if}
          </div>
        </div>
      </div>
    {/if}

    <!-- Node detail overlay -->
    {#if $selectedNodeId}
      {@const selNode = graphStore.nodes.find(n => n.id === $selectedNodeId)}
      {@const nbrEdges = graphStore.edges.filter(e => e.source === $selectedNodeId || e.target === $selectedNodeId)}
      {@const nbrIds = new Set(nbrEdges.flatMap(e => [e.source, e.target]).filter(id => id !== $selectedNodeId))}
      {@const nbrNodes = graphStore.nodes.filter(n => nbrIds.has(n.id))}
      <div class="node-detail-overlay">
        <NodeDetail
          node={selNode ?? null}
          neighbors={{ nodes: nbrNodes, edges: nbrEdges }}
          onclose={() => selectedNodeId.set(null)}
        />
      </div>
    {/if}

  {:else if $activeTab === 'ingestion'}
    <div class="h-full w-full overflow-hidden p-2 md:p-0">
      <IngestionPanel />
    </div>
  {:else if $activeTab === 'activity'}
    <div class="h-full w-full overflow-hidden p-2 md:p-4">
      <ActivityFeed events={eventBus.events} connected={true} />
    </div>
  {/if}
</div>

<style>
  .chat-input-bar {
    position: absolute;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 30;
    width: 100%;
    max-width: 42rem;
    padding: 0 16px;
  }

  @media (max-width: 768px) {
    .chat-input-bar {
      bottom: 64px;
      max-width: 100%;
      padding: 0 8px;
    }
  }

  .chat-overlay-backdrop {
    position: absolute;
    inset: 0;
    z-index: 30;
  }

  .chat-overlay-panel {
    position: absolute;
    top: 0;
    bottom: 0;
    right: 0;
    z-index: 40;
    display: flex;
    width: 100%;
    max-width: 32rem;
    flex-direction: column;
    border-left: 1px solid rgba(0, 212, 255, 0.25);
    background: rgba(10, 14, 23, 0.95);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    animation: slide-in-right 300ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  @media (max-width: 768px) {
    .chat-overlay-panel {
      max-width: 100%;
      inset: 0;
      border-left: none;
    }
  }

  .node-detail-overlay {
    position: absolute;
    right: 16px;
    top: 16px;
    z-index: 20;
    width: 20rem;
  }

  @media (max-width: 768px) {
    .node-detail-overlay {
      right: 8px;
      left: 8px;
      bottom: 120px;
      top: auto;
      width: auto;
      max-height: 50dvh;
      overflow-y: auto;
    }
  }
</style>