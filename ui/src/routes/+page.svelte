<script lang="ts">
  import type { ChatMessage, MCPToolCall, OllamaMessage } from '$lib/constants';
  import { API } from '$lib/constants';
  import { tick } from 'svelte';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { activeTab, selectedNodeId, navDrawerOpen, historyPanelOpen } from '$lib/stores/ui';
  import { mcpClient } from '$lib/services/mcp-client.svelte';
  import { connectionStore } from '$lib/stores/connection.svelte';
  import { configStore } from '$lib/stores/config.svelte';
  import { conversationStore } from '$lib/stores/conversation.svelte';
  import CanvasView from '$lib/components/canvas/CanvasView.svelte';
  import IngestionPanel from '$lib/components/ingestion/IngestionPanel.svelte';
  import NodeDetail from '$lib/components/graph/NodeDetail.svelte';

  import Icon from '$lib/components/ui/Icon.svelte';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import { AudioRecorder, blobToBase64, isAudioRecordingSupported, transcribeAudio } from '$lib/utils/audio-recording';
  import { parseKGResult } from '$lib/utils/parse-kg-result';
  import ImageGallery from '$lib/components/ui/ImageGallery.svelte';
  import AudioPlayer from '$lib/components/ui/AudioPlayer.svelte';

  import AttachmentMenu from '$lib/components/ui/AttachmentMenu.svelte';
  import AttachmentPreview from '$lib/components/ui/AttachmentPreview.svelte';
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
  let availableModels = $state<string[]>([]);
  let selectedModel = $state('');
  let textareaEl: HTMLTextAreaElement | undefined = $state();
  let panelTextareaEl: HTMLTextAreaElement | undefined = $state();
  let messagesContainer: HTMLDivElement | undefined = $state();

  // Continuous scroll buffer — lets the user scroll up from the active
  // conversation into previous conversations without manually switching.
  // `olderConversationIds` is ordered oldest-loaded-first, newest-loaded-last
  // (i.e. the conversation immediately above the active one is the last
  // element). `olderConversationMessages` caches loaded messages per id.
  let olderConversationIds = $state<string[]>([]);
  let olderConversationMessages = $state<Record<string, ChatMessage[]>>({});
  let isLoadingOlder = $state(false);
  let hasNoMoreOlder = $state(false);
  // Suppresses the next scroll event so programmatic scroll-position
  // restoration after loading an older conversation doesn't re-trigger
  // the load handler in a loop.
  let suppressScrollLoad = false;

  // --- Scroll-to-load-previous state ---
  // When the user scrolls to the top, a sticky indicator appears telling
  // them to keep scrolling up. A wheel/scroll-up gesture at scrollTop===0
  // triggers loading the previous conversation.
  let nearTop = $state(false);
  let atTop = $state(false);
  // Whether there are older conversations available to load.
  let hasOlderAvailable = $derived(!hasNoMoreOlder && (() => {
    const activeIdx = conversations.findIndex((c) => c.id === activeConversationId);
    if (activeIdx === -1) return false;
    let frontierIdx = activeIdx;
    if (olderConversationIds.length > 0) {
      const frontierId = olderConversationIds[olderConversationIds.length - 1];
      frontierIdx = conversations.findIndex((c) => c.id === frontierId);
    }
    return frontierIdx >= 0 && frontierIdx + 1 < conversations.length;
  })());

  function resetScrollBuffer() {
    olderConversationIds = [];
    olderConversationMessages = {};
    hasNoMoreOlder = false;
    nearTop = false;
    atTop = false;
  }

  /**
   * Find the next older conversation (one position older than the oldest
   * currently loaded in the buffer, or one position older than the active
   * conversation if the buffer is empty) and load its messages, prepending
   * to the buffer while preserving the user's scroll position so the view
   * appears to extend upward seamlessly.
   */
  async function loadOlderConversation() {
    if (isLoadingOlder || hasNoMoreOlder || !activeConversationId) return;

    // `conversations` is newest-first. Find the active conversation's index,
    // then walk toward older conversations. The "frontier" is the oldest
    // conversation we've already loaded into the buffer (or the active one
    // if the buffer is empty). The next older conversation is at index+1.
    const activeIdx = conversations.findIndex((c) => c.id === activeConversationId);
    if (activeIdx === -1) return;

    let frontierId = activeConversationId;
    if (olderConversationIds.length > 0) {
      frontierId = olderConversationIds[olderConversationIds.length - 1];
    }
    const frontierIdx = conversations.findIndex((c) => c.id === frontierId);
    if (frontierIdx === -1) {
      hasNoMoreOlder = true;
      return;
    }
    const nextIdx = frontierIdx + 1;
    if (nextIdx >= conversations.length) {
      hasNoMoreOlder = true;
      return;
    }
    const nextConv = conversations[nextIdx];
    if (!nextConv) {
      hasNoMoreOlder = true;
      return;
    }

    isLoadingOlder = true;
    try {
      let loaded = nextConv.messages;
      if (loaded.length === 0) {
        loaded = await syncClient.loadConversation(nextConv.id);
        nextConv.messages = loaded;
      }
      const prevScrollHeight = messagesContainer?.scrollHeight ?? 0;
      const prevScrollTop = messagesContainer?.scrollTop ?? 0;

      suppressScrollLoad = true;
      olderConversationIds = [...olderConversationIds, nextConv.id];
      olderConversationMessages = {
        ...olderConversationMessages,
        [nextConv.id]: loaded,
      };

      await tick();
      if (messagesContainer) {
        const delta = messagesContainer.scrollHeight - prevScrollHeight;
        messagesContainer.scrollTop = prevScrollTop + delta;
      }
      requestAnimationFrame(() => {
        if (messagesContainer) {
          const delta = messagesContainer.scrollHeight - prevScrollHeight;
          messagesContainer.scrollTop = prevScrollTop + delta;
        }
        requestAnimationFrame(() => {
          suppressScrollLoad = false;
          isLoadingOlder = false;
        });
      });
    } catch {
      isLoadingOlder = false;
      suppressScrollLoad = false;
    }
  }

  function handleMessagesScroll() {
    if (suppressScrollLoad) {
      suppressScrollLoad = false;
      return;
    }
    if (!messagesContainer) return;
    atTop = messagesContainer.scrollTop === 0;
    nearTop = messagesContainer.scrollTop < 80;
    if (
      messagesContainer.scrollTop < 120 &&
      !isLoadingOlder &&
      !hasNoMoreOlder &&
      hasOlderAvailable
    ) {
      loadOlderConversation();
    }
  }

  // The active conversation object, for the divider label above the active
  // messages. Kept as a derived so the divider re-renders when the active
  // conversation changes or its title updates.
  let activeConvForDivider = $derived(
    conversations.find((c) => c.id === activeConversationId) ?? null
  );
  let showActiveDivider = $derived(
    !!activeConvForDivider &&
    (olderConversationIds.length > 0 || messages.length > 0)
  );

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

  // Click-vs-pan discrimination: record pointer-down position; treat the
  // pointerup as a click only if it stayed within a small radius. Prevents
  // graph panning from collapsing the inline chat overlay.
  let pointerDownXY: { x: number; y: number } | null = null;
  const CLICK_MAX_DRIFT = 6;

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
  // Guard against rapid space-bar presses racing start/stop against each other.
  // Without this, a second press before startRecording()'s async getUserMedia
  // resolves sees isRecording=false and starts ANOTHER recording, or a press
  // during stopRecording()'s await re-enters and throws "No active recording".
  let micBusy = $state(false);
  let promptEditing = $state(false);
  let promptDraft = $state('');
  let promptSaving = $state(false);
  let promptSaveStatus = $state<'idle' | 'saved' | 'error'>('idle');

  $effect(() => {
    recordingSupported = isAudioRecordingSupported();
    if (recordingSupported) {
      audioRecorder = new AudioRecorder();
      return () => audioRecorder?.destroy();
    }
  });

  $effect(() => {
    function onSpacePress(e: KeyboardEvent) {
      if (e.key !== ' ' && e.code !== 'Space') return;
      const target = e.target as HTMLElement;
      if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' || target.isContentEditable)) return;
      if (isActiveConversationStreaming || isTranscribing || micBusy) return;
      e.preventDefault();
      handleMicClick();
    }
    window.addEventListener('keydown', onSpacePress);
    return () => window.removeEventListener('keydown', onSpacePress);
  });

  async function handleMicClick() {
    if (!audioRecorder || !recordingSupported) return;
    if (micBusy) return;
    micBusy = true;
    try {
      if (isRecording) {
        isRecording = false;
        isTranscribing = true;
        try {
          const wavBlob = await audioRecorder.stopRecording();
          console.log('[audio] WAV blob size:', wavBlob.size, 'type:', wavBlob.type);
          const audioUrl = URL.createObjectURL(wavBlob);
          // Persist the audio as durable base64 data so the waveform can be
          // reconstructed on reload — blob: URLs are session-only and don't
          // survive a page reload. sync-client regenerates audioUrl from
          // audioData via fromSyncMsg; without this the AudioPlayer vanishes
          // when the conversation is reloaded.
          const audioData = await blobToBase64(wavBlob);

          const transcript = await transcribeAudio(wavBlob, API.llama.transcriptions);
          console.log('[audio] transcript:', transcript);
          const text = transcript.trim();
          if (!text) {
            console.warn('[audio] empty transcript, ignoring');
            return;
          }

          await handleSend(audioUrl, audioData, 'wav', false, text);
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
    } finally {
      micBusy = false;
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

    // Add all image nodes to the graph immediately (no processing yet — that
    // happens on send, so the user's text note can be threaded through as context).
    for (const att of imageAttachments) {
      const photoNodeId = `${att.name} (Photo)`;
      graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', source_id: att.name });
      if (att.dataUrl) {
        graphStore.setPhotoImage(photoNodeId, att.thumbnailUrl ?? att.dataUrl);
      }
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
    resetScrollBuffer();
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
    resetScrollBuffer();
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
   * Stream an assistant response from the LLM based on the current `messages` state.
   * This is the core streaming loop — called by both handleSend (new messages) and
   * resendMessage (re-processing).
   */
   /**
    * Resolve image file paths to renderable URLs concurrently and attach them
    * to the streaming assistant message as they become available, so the gallery
    * renders incrementally while the next LLM turn is in flight.
    */
   function resolveImagePaths(
     paths: string[],
     assistantId: string,
     collected: string[],
   ) {
     for (const p of paths) {
       const directUrl = lightragClient.photoImageUrl(p);
       collected.push(directUrl);
       updateStreamMessage(assistantId, (m) => ({
         ...m,
         imageUrls: [...(m.imageUrls || []), directUrl],
       }));
     }
   }

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
        { role: 'system', content: configStore.systemPrompt.replaceAll('{{CURRENT_DATE}}', new Date().toISOString().slice(0, 10)) },
        ...messages
          .filter((m) => !m.isStreaming)
          .map((m) => {
            if (m.role === 'user' && m.audioData) {
              const parts: ContentPart[] = [];
              if (m.content.trim()) parts.push({ type: 'text', text: m.content });
              parts.push({ type: 'input_audio', input_audio: { data: m.audioData, format: m.audioFormat ?? 'wav' } });
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

        // Finalize this assistant message — keep isStreaming true if tool calls are
        // pending so the progress bar / chip rendering stays visible during tool exec.
        const pendingToolCalls = toolCalls.length > 0
          ? toolCalls.map((tc) => ({
              id: tc.id,
              toolName: tc.name,
              arguments: JSON.parse(tc.arguments || '{}'),
              timestamp: Date.now(),
            }))
          : [];

        updateStreamMessage(assistantId, (m) => ({
          ...m,
          content: accumulatedContent || '',
          isStreaming: finishReason === 'tool_calls' && toolCalls.length > 0,
          thinkingContent: accumulatedThinking || undefined,
          timings: msgTimings,
          model: selectedModel || undefined,
          mcpToolCalls: pendingToolCalls.length > 0 ? pendingToolCalls : m.mcpToolCalls,
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
            save_to_knowledge_graph: 'Saving to knowledge graph...',
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
          let parsedKG: MCPToolCall['parsedKG'] = undefined;
          if (!isToolError && toolResult) {
            const parsed = parseKGResult(toolResult);
            parsedKG = parsed;
            const markerIdx = toolResult.indexOf('---IMAGE_REFS---');
            displayResult = markerIdx !== -1 ? toolResult.slice(0, markerIdx).trimEnd() : toolResult;
            apiMessages.push({
              role: 'tool',
              tool_call_id: tc.id,
              content: parsed.contextText,
            });

            if (parsed.imagePaths.length > 0) {
              resolveImagePaths(parsed.imagePaths, assistantId, collectedImageUrls);
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
                ? { ...mtc, result: displayResult.slice(0, 2000), isError: isToolError, parsedKG }
                : mtc
            ),
          }));
        }

        // Finalize this assistant message — the tool turn is done, the next
        // loop iteration creates a fresh streaming assistant message.
        updateStreamMessage(assistantId, (m) => ({ ...m, isStreaming: false }));

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

  async function handleSend(audioUrl?: string, audioData?: string, audioFormat?: 'wav' | 'mp3', startNew = false, transcript?: string) {
    const messageText = transcript ?? chatInput.trim();
    const trimmed = messageText.trim();
    if ((!trimmed && attachments.length === 0) && !audioData) return;
    if (isActiveConversationStreaming) return;

    // Capture whether the chat was already open before this send: a send from
    // the main page (collapsed chat) should start a new conversation, while a
    // send from inside an open conversation should append to it.
    const wasChatExpanded = chatExpanded;
    chatExpanded = true;
    isProcessing = true;
    processingLabel = 'Sending...';

    if (startNew || !wasChatExpanded || !activeConversationId) {
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

    // Kick off image processing for all image attachments, with the user's note as context.
    const messageNote = trimmed || transcript || '';
    const imageAttachments = sentAttachments.filter((a) => isImageType(a.mimeType));
    if (imageAttachments.length > 0) {
      for (const att of imageAttachments) {
        processSingleImage(att, messageNote);
      }
    }

    await streamAssistantResponse(sentAttachments);
  }

  /** Process a single image through the KG pipeline with real-time SSE progress. */
  async function processSingleImage(att: Attachment, note: string = '') {
    const photoNodeId = `${att.name} (Photo)`;
    try {
      const job = await kgApiClient.createJob(att.file, { insert: true, note });
      imageProcessingStore.startProcessing(photoNodeId, att.name, att.thumbnailUrl ?? att.dataUrl ?? '', job.job_id);
      await consumeJobEvents(job.job_id, photoNodeId, att);
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return;
      console.warn(`KG image processing failed for ${att.name}:`, err);
      imageProcessingStore.updateStage(photoNodeId, 'error', err instanceof Error ? err.message : 'Unknown error');
    }
  }

  /** Consume SSE events from a job, with automatic reconnect + polling backstop. */
  async function consumeJobEvents(jobId: string, photoNodeId: string, att: Attachment, afterEventId: number = 0) {
    let lastEventId = afterEventId;
    const activeJobs = new Set<string>([jobId]);

    // Polling backstop: if the SSE stream ends without a terminal event, poll the
    // job status endpoint to force-reconcile the stage. Stops once the job reaches
    // a terminal state or is removed from the store.
    const pollStatus = async () => {
      try {
        const job = await kgApiClient.getJob(jobId);
        if (job.status === 'complete') {
          imageProcessingStore.updateStage(photoNodeId, 'complete');
          graphStore.pipelineDone = true;
          graphStore.refresh();
          return true;
        }
        if (job.status === 'failed' || job.status === 'cancelled') {
          imageProcessingStore.updateStage(photoNodeId, 'error', job.error || `Job ${job.status}`);
          return true;
        }
        return false;
      } catch {
        return false;
      }
    };

    // Cleanup helper used by every terminal path.
    let currentCancel: (() => void) | null = null;
    const finish = () => {
      if (currentCancel) currentCancel();
      currentCancel = null;
      activeJobs.delete(jobId);
    };

    let reconnectDelayMs = 1000;
    const MAX_RECONNECT_DELAY_MS = 15000;
    const MAX_RECONNECTS = 5;

    for (let attempt = 0; attempt <= MAX_RECONNECTS; attempt++) {
      const { stream, cancel } = kgApiClient.streamJobEvents(jobId, lastEventId);
      currentCancel = cancel;
      let streamEnded = false;

      try {
        for await (const sseEvent of stream) {
          let payload: { event?: string; data?: Record<string, unknown>; timestamp?: number; event_id?: number };
          try {
            payload = JSON.parse(sseEvent.data);
          } catch {
            continue;
          }

          if (payload.event_id != null && payload.event_id > lastEventId) {
            lastEventId = payload.event_id;
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

          if (eventName === 'photo_node_created' || eventName === 'exif_node_created') {
            const nodeId = String(eventData.entity_name ?? eventData.name ?? eventData.id ?? '');
            const labels = Array.isArray(eventData.labels) ? eventData.labels : [eventName === 'photo_node_created' ? 'Photo' : 'ExifEntity'];
            graphStore.upsertNode(nodeId, labels, eventData as Record<string, unknown>);
            if (eventName === 'photo_node_created' && (att.thumbnailUrl ?? att.dataUrl)) {
              graphStore.setPhotoImage(nodeId, att.thumbnailUrl ?? att.dataUrl);
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
            finish();
            return;
          }
        }
        streamEnded = true;
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          finish();
          return;
        }
        // Network error — fall through to reconnect/reconcile below.
        streamEnded = true;
      }

      // Stream ended without a terminal event. Reconcile via the status endpoint
      // before deciding whether to reconnect: the job may have already completed.
      if (streamEnded) {
        const terminal = await pollStatus();
        if (terminal) {
          finish();
          return;
        }
        if (attempt < MAX_RECONNECTS) {
          await new Promise((r) => setTimeout(r, reconnectDelayMs));
          reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
        }
      }
    }

    // Exhausted reconnects and the job isn't terminal — poll a couple more times
    // as a last resort, then leave the store as-is so the user can see the stall.
    for (let i = 0; i < 3; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      if (!(jobId in imageProcessingStore.statuses)) { finish(); return; }
      if (await pollStatus()) { finish(); return; }
    }
    finish();
  }

  /** Send image attachments through the KG pipeline with real-time SSE progress. */
  async function processImageAttachments(atts: Attachment[], note: string = '') {
    const imageFiles = atts.filter((a) => isImageType(a.mimeType) && a.file);
    for (const att of imageFiles) {
      processSingleImage(att, note);
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

    streamAssistantResponse();
  }

  async function deleteConversation(id: string) {
    cancelStreaming();
    conversations = conversations.filter((c) => c.id !== id);
    syncClient.deleteConversation(id);
    if (activeConversationId === id) {
      const next = conversations[0];
      if (next) {
        activeConversationId = next.id;
        resetScrollBuffer();
        if (next.messages.length === 0) {
          const loaded = await syncClient.loadConversation(next.id);
          if (loaded.length > 0) {
            next.messages = loaded;
            messages = [...loaded];
          } else {
            messages = [...next.messages];
          }
        } else {
          messages = [...next.messages];
        }
      } else {
        activeConversationId = '';
        messages = [];
        chatExpanded = false;
        resetScrollBuffer();
      }
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

  function handleQueryAbout(node: { id: string; labels?: string[]; properties?: Record<string, unknown> }) {
    if (isActiveConversationStreaming) return;
    const name = (node.properties?.name as string) ?? node.id;
    chatInput = `Tell me about ${name}`;
    chatExpanded = true;
    requestAnimationFrame(() => {
      if (textareaEl) {
        textareaEl.style.height = 'auto';
        textareaEl.style.height = Math.min(textareaEl.scrollHeight, (parseInt(getComputedStyle(textareaEl).lineHeight) || 24) * 6) + 'px';
      }
    });
    handleSend(undefined, undefined, undefined, true);
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

  /** Formats a conversation's updatedAt for divider labels — relative for
   *  recent conversations, absolute date+time for older ones. */
  function formatConversationDate(ts: number): string {
    const d = new Date(ts);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const isYesterday = d.toDateString() === yesterday.toDateString();
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (sameDay) return `Today, ${time}`;
    if (isYesterday) return `Yesterday, ${time}`;
    const sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
      year: sameYear ? undefined : 'numeric',
    }) + `, ${time}`;
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
      resetScrollBuffer();
      if (conversations.length > 0 && !activeConversationId) {
        activeConversationId = conversations[0].id;
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
    if (messagesContainer && messages.length > 0) {
      nearTop = messagesContainer.scrollTop < 80;
      atTop = messagesContainer.scrollTop === 0;
    }
  });

  $effect(() => {
    conversationStore.conversations = conversations;
    conversationStore.activeConversationId = activeConversationId;
    conversationStore.unreadConversations = unreadConversations;
  });

  let lastNavigatedId = '';
  let lastNavigateCount = 0;
  let firstLoadSeen = false;
  $effect(() => {
    const storeId = conversationStore.activeConversationId;
    const count = conversationStore.navigateCount;
    // Skip the initial auto-selection on load so the chat doesn't auto-expand.
    // The first time a non-empty storeId appears (from init loading conversations),
    // record it but don't call switchConversation (which expands the chat).
    if (!firstLoadSeen) {
      if (storeId) {
        firstLoadSeen = true;
        lastNavigatedId = storeId;
        lastNavigateCount = count;
      }
      return;
    }
    if (storeId && (storeId !== lastNavigatedId || count !== lastNavigateCount)) {
      lastNavigatedId = storeId;
      lastNavigateCount = count;
      switchConversation(storeId);
    }
  });
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  class="relative h-full w-full overflow-hidden"
  ondragover={handleDragOver}
  ondragenter={handleDragEnter}
  ondragleave={handleDragLeave}
  ondrop={handleDrop}
  onpointerdown={(e) => { pointerDownXY = { x: e.clientX, y: e.clientY }; }}
  onclick={(e) => {
    if (!chatExpanded) return;
    if (pointerDownXY) {
      const dx = e.clientX - pointerDownXY.x;
      const dy = e.clientY - pointerDownXY.y;
      if (dx * dx + dy * dy > CLICK_MAX_DRIFT * CLICK_MAX_DRIFT) return;
    }
    if (!(e.target as Node).closest('[data-testid="chat-inline-overlay"]')) {
      closeChat();
    }
  }}
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
      <CanvasView onqueryAbout={handleQueryAbout} />
    </div>

    <!-- Inline chat overlay (game-style, bottom-right) -->
    <div class="chat-inline-overlay" data-testid="chat-inline-overlay">
      <!-- Hidden file inputs (used by both input rows) -->
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

      <!-- Recent messages (faded top/bottom, no header) -->
      {#if chatExpanded && activeConversationId && messages.length > 0}
        <div class="chat-inline-header" data-testid="chat-inline-header">
          <span class="chat-inline-header-title">Conversation</span>
          <button
            onclick={() => { historyPanelOpen.update((v) => !v); }}
            class="flex h-7 w-7 items-center justify-center rounded-md bg-cyber-surface-2/50 text-cyber-text-dim/70 transition-all duration-200 hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
            title="Chat history"
            aria-label="Open chat history"
            data-testid="chat-history-button"
          >
            <Icon name="clock" size={15} />
          </button>
        </div>
        <div
          bind:this={messagesContainer}
          class="chat-inline-messages"
          data-testid="messages-container"
          onscroll={handleMessagesScroll}
        >
          {#if nearTop && !isLoadingOlder && !hasNoMoreOlder && hasOlderAvailable}
            <div class="chat-scroll-hint" data-testid="scroll-load-hint">
              <Icon name="chevron-down" size={14} color="var(--color-cyber-cyan)" />
              <span>Keep scrolling up for previous conversation</span>
            </div>
          {/if}
          {#if isLoadingOlder}
            <div class="chat-scroll-hint chat-scroll-hint-loading" data-testid="scroll-load-hint">
              <div class="chat-pull-spinner" data-testid="pull-loading">
                <Icon name="refresh-cw" size={14} color="var(--color-cyber-cyan)" />
              </div>
              <span>Loading previous conversation…</span>
            </div>
          {/if}
          {#if hasNoMoreOlder && nearTop}
            <div class="chat-scroll-hint chat-scroll-hint-done" data-testid="scroll-load-hint">
              <span>No older conversations</span>
            </div>
          {/if}

          {#snippet messageRow(msg: ChatMessage)}
            <div class="group mb-4" data-testid="message" data-message-id={msg.id} data-message-role={msg.role}>
              {#if msg.role === 'user'}
                <div class="flex justify-end">
                  <div class="max-w-[95%] space-y-1">
                    {#if msg.imageUrls && msg.imageUrls.length > 0}
                      <ImageGallery images={msg.imageUrls} alt="Uploaded image" />
                    {/if}
                    {#if msg.audioUrl}
                      <AudioPlayer src={msg.audioUrl} label="Voice message" />
                    {/if}
                    {#if msg.content}
                      <div class="rounded-2xl rounded-br-sm bg-cyber-cyan/10 px-4 py-2.5 text-sm text-cyber-text border border-cyber-cyan/20" data-testid="user-message-text">
                        {msg.content}
                      </div>
                    {/if}
                  </div>
                </div>
                <div class="mt-0.5 flex items-center justify-end gap-2">
                  <span class="text-[10px] text-cyber-text-dim/50">{formatTime(msg.timestamp)}</span>
                  {#if !isActiveConversationStreaming}
                    <button
                      onclick={() => resendMessage(msg.id)}
                      class="flex items-center gap-1 rounded-md border border-cyber-border/50 bg-cyber-surface-2/60 px-2 py-1 text-[11px] font-medium text-cyber-text-dim transition-all duration-200 hover:border-cyber-cyan/50 hover:bg-cyber-cyan/10 hover:text-cyber-cyan hover:glow-cyan focus:outline-none focus:ring-1 focus:ring-cyber-cyan/40 active:scale-95"
                      title="Regenerate response"
                      data-testid="regenerate-button"
                    >
                      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                      Regenerate
                    </button>
                  {/if}
                </div>
              {:else}
                <div class="flex justify-start">
                  <div class="max-w-[95%] space-y-1.5">
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

                    {#if msg.content || (msg.isStreaming && !msg.mcpToolCalls?.length)}
                    <div class="rounded-2xl rounded-bl-sm bg-cyber-surface-2/80 px-4 py-2.5 text-sm text-cyber-text border border-cyber-border/30" data-testid="assistant-message">
                      {#if msg.isStreaming && isProcessing}
                        <div class="flex items-center gap-2 py-1" data-testid="processing-indicator">
                          <div class="flex-1 h-1.5 rounded-full bg-cyber-border/40 overflow-hidden">
                            <div class="h-full rounded-full bg-cyber-orange animate-pulse" style="width: {processingLabel.includes('%') ? processingLabel.match(/(\d+)%/)?.[1] ?? '0' : '100'}%"></div>
                          </div>
                          <span class="shrink-0 text-[11px] text-cyber-orange" data-testid="processing-label">{processingLabel}</span>
                          {#if promptTokens}
                            <span class="shrink-0 font-mono text-[10px] text-cyber-text-dim">{promptTokens} tokens</span>
                          {/if}
                        </div>
                      {:else if msg.isStreaming && !msg.content}
                        <div class="flex items-center gap-2 py-1" data-testid="generating-indicator">
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
                            data-testid="stop-button-inline"
                          >
                            <svg class="h-2.5 w-2.5" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1.5" /></svg>
                            Stop
                          </button>
                        </div>
                      {/if}
                      {#if msg.content}
                        <div class="prose-cyber" data-testid="assistant-message-text">{@html msg.isStreaming ? renderStreamingContent(msg.content) : renderMarkdown(msg.content)}</div>
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
                      {#if msg.isStreaming && tokensPerSecond && msg.content}
                        <div class="mt-1.5 flex items-center gap-1.5 text-[10px]">
                          <span class="inline-flex items-center gap-1 rounded-full bg-cyber-green/10 px-1.5 py-0.5 font-mono text-cyber-green">
                            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                            {tokensPerSecond} t/s
                          </span>
                        </div>
                      {/if}
                    </div>
                    {/if}

                    {#if !msg.isStreaming && msg.content}
                      <div class="flex items-center gap-2">
                        {#if msg.role === 'assistant'}
                          <button
                            onclick={() => {
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
                          data-testid="copy-button"
                        >
                          <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                          Copy
                        </button>
                      </div>
                    {/if}

                    {#if msg.mcpToolCalls && msg.mcpToolCalls.length > 0}
                      {#each msg.mcpToolCalls as toolCall (toolCall.id || toolCall.toolName + toolCall.timestamp)}
                        {@const parsed = toolCall.parsedKG}
                        {@const photoCount = parsed ? parsed.imagePaths.length : 0}
                        {@const entityCount = parsed ? parsed.entities.length : 0}
                        {@const relCount = parsed ? parsed.relationships.length : 0}
                        {@const isRunning = !toolCall.result && !toolCall.isError}
                        {@const hasPhotos = parsed && parsed.imagePaths.length > 0 && msg.imageUrls && msg.imageUrls.length > 0}
                        {@const isSave = toolCall.toolName === 'save_to_knowledge_graph'}
                        {@const savedText = isSave ? String(toolCall.arguments?.text ?? '') : ''}
                        <details class="group" open={true} data-testid="tool-call" data-tool-name={toolCall.toolName} data-tool-call-id={toolCall.id || ''}>
                          <summary class="flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] transition-colors {toolCall.isError ? 'border-cyber-red/30 bg-cyber-red/5 hover:bg-cyber-red/10' : toolCall.result ? 'border-cyber-green/30 bg-cyber-green/5 hover:bg-cyber-green/10' : 'border-cyber-orange/30 bg-cyber-orange/5 hover:bg-cyber-orange/10'}" data-testid="tool-call-summary">
                            {#if toolCall.isError}
                              <svg class="h-3.5 w-3.5 shrink-0 text-cyber-red" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                            {:else if toolCall.result}
                              <svg class="h-3.5 w-3.5 shrink-0 text-cyber-green" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
                            {:else}
                              <span class="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-cyber-orange"></span>
                            {/if}
                            <span class="font-mono {toolCall.isError ? 'text-cyber-red' : toolCall.result ? 'text-cyber-green' : 'text-cyber-orange'}">{toolCall.toolName}</span>
                            {#if isRunning}
                              <span class="text-[10px] text-cyber-orange/80 animate-pulse">{isSave ? 'saving to knowledge graph...' : 'searching knowledge graph...'}</span>
                            {:else if isSave && savedText}
                              <span class="truncate text-[10px] text-cyber-text-dim/70">{savedText}</span>
                            {:else if parsed}
                              <span class="flex items-center gap-1 text-[10px] text-cyber-text-dim/70">
                                <span class="inline-flex items-center gap-0.5 rounded-full bg-cyber-cyan/10 px-1.5 py-0.5 text-cyber-cyan">{entityCount} entities</span>
                                <span class="inline-flex items-center gap-0.5 rounded-full bg-cyber-purple/10 px-1.5 py-0.5 text-cyber-purple">{relCount} relations</span>
                                {#if photoCount > 0}
                                  <span class="inline-flex items-center gap-0.5 rounded-full bg-cyber-green/10 px-1.5 py-0.5 text-cyber-green">
                                    <svg class="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                                    {photoCount} photos
                                  </span>
                                {/if}
                              </span>
                            {/if}
                            <span class="ml-auto text-cyber-text-dim/40">{formatTime(toolCall.timestamp)}</span>
                            <svg class="h-3 w-3 text-cyber-text-dim/50 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                          </summary>
                          <div class="mt-1 space-y-2 rounded-lg border border-cyber-border/20 bg-cyber-bg/50 p-2.5" data-testid="tool-call-body">
                            {#if isRunning}
                              <div class="flex items-center gap-2 py-3 text-[11px] text-cyber-orange" data-testid="tool-call-running">
                                <svg class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 11-6.219-8.56" stroke-linecap="round" stroke-linejoin="round"/></svg>
                                <span>{isSave ? 'Saving to knowledge graph...' : 'Querying knowledge graph...'}</span>
                              </div>
                            {:else if isSave}
                              {#if savedText}
                                <div data-testid="tool-call-inserted-text">
                                  <div class="mb-1 text-[10px] font-medium uppercase tracking-wider text-cyber-text-dim/60">Saved text</div>
                                  <pre class="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-cyber-surface-2/50 p-2 font-mono text-[11px] leading-relaxed text-cyber-text">{savedText}</pre>
                                </div>
                              {/if}
                              {#if toolCall.result}
                                <div>
                                  <div class="mb-1 text-[10px] font-medium uppercase tracking-wider text-cyber-text-dim/60">Result</div>
                                  <pre class="max-h-48 overflow-auto rounded bg-cyber-surface-2/50 p-2 font-mono text-[11px] leading-relaxed {toolCall.isError ? 'text-cyber-red' : 'text-cyber-green'}">{formatJsonSafe(toolCall.result ?? '')}</pre>
                                </div>
                              {/if}
                            {:else if parsed}
                              {#if hasPhotos}
                                <div data-testid="tool-call-photos">
                                  <div class="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-cyber-green/80">
                                    <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                                    Photos
                                  </div>
                                  <ImageGallery images={msg.imageUrls} alt="Knowledge graph photo" />
                                </div>
                              {/if}
                              {#if parsed.relationships.length > 0}
                                <details class="group/rels" data-testid="tool-call-relationships">
                                  <summary class="mb-1.5 flex cursor-pointer items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-cyber-purple/80 hover:text-cyber-purple transition-colors">
                                    <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>
                                    Relationships
                                    <span class="text-cyber-text-dim/40">({relCount})</span>
                                    <svg class="ml-0.5 h-3 w-3 transition-transform group-open/rels:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                                  </summary>
                                  <div class="space-y-1">
                                    {#each parsed.relationships.slice(0, 12) as rel}
                                      <div class="rounded-md border border-cyber-border/30 bg-cyber-surface-2/40 px-2 py-1.5 text-[11px]">
                                        <div class="flex flex-wrap items-center gap-1">
                                          <span class="font-medium text-cyber-text">{rel.entity1}</span>
                                          <svg class="h-2.5 w-2.5 shrink-0 text-cyber-text-dim/50" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
                                          <span class="font-medium text-cyber-text">{rel.entity2}</span>
                                        </div>
                                        {#if rel.description}
                                          <p class="mt-0.5 text-cyber-text-dim/60 leading-relaxed">{rel.description}</p>
                                        {/if}
                                      </div>
                                    {/each}
                                    {#if parsed.relationships.length > 12}
                                      <div class="text-center text-[10px] text-cyber-text-dim/50">+{parsed.relationships.length - 12} more</div>
                                    {/if}
                                  </div>
                                </details>
                              {/if}
                              {#if parsed.entities.length > 0}
                                <details class="group/entities" data-testid="tool-call-entities">
                                  <summary class="mb-1.5 flex cursor-pointer items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-cyber-cyan/80 hover:text-cyber-cyan transition-colors">
                                    <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 000 20M12 2a14.5 14.5 0 010 20"/></svg>
                                    Entities
                                    <span class="text-cyber-text-dim/40">({entityCount})</span>
                                    <svg class="ml-0.5 h-3 w-3 transition-transform group-open/entities:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                                  </summary>
                                  <div class="space-y-1">
                                    {#each parsed.entities.slice(0, 12) as ent}
                                      <div class="rounded-md border border-cyber-border/30 bg-cyber-surface-2/40 px-2 py-1.5 text-[11px]">
                                        <div class="flex items-center gap-1.5">
                                          <span class="font-medium text-cyber-text">{ent.entity}</span>
                                          {#if ent.type}
                                            <span class="rounded-full bg-cyber-purple/10 px-1.5 py-0.5 text-[9px] text-cyber-purple">{ent.type}</span>
                                          {/if}
                                        </div>
                                        {#if ent.description}
                                          <p class="mt-0.5 line-clamp-2 text-cyber-text-dim/70 leading-relaxed">{ent.description}</p>
                                        {/if}
                                      </div>
                                    {/each}
                                    {#if parsed.entities.length > 12}
                                      <div class="text-center text-[10px] text-cyber-text-dim/50">+{parsed.entities.length - 12} more</div>
                                    {/if}
                                  </div>
                                </details>
                              {/if}
                            {:else}
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
          {/snippet}

          {#each olderConversationIds as convId (convId)}
            {@const conv = conversations.find((c) => c.id === convId)}
            {@const convMsgs = olderConversationMessages[convId] ?? []}
            {#if conv}
              <div class="chat-conversation-divider" data-testid="conversation-divider" data-conversation-id={convId}>
                <span class="chat-conversation-divider-line"></span>
                <span class="chat-conversation-divider-label">
                  <span class="chat-conversation-divider-date">{formatConversationDate(conv.createdAt)}</span>
                  <span class="chat-conversation-divider-divider-dots" aria-hidden="true"></span>
                  <button
                    type="button"
                    class="chat-conversation-divider-btn"
                    title="Export conversation"
                    aria-label="Export conversation"
                    onclick={(e) => {
                      e.stopPropagation();
                      exportConversationToJsonl(convId);
                    }}
                  >
                    <Icon name="download" size={13} color="var(--color-cyber-cyan)" />
                  </button>
                  <button
                    type="button"
                    class="chat-conversation-divider-btn chat-conversation-divider-delete"
                    title="Delete conversation"
                    aria-label="Delete conversation"
                    onclick={(e) => {
                      e.stopPropagation();
                      if (confirm('Delete this conversation? This cannot be undone.')) {
                        deleteConversation(convId);
                      }
                    }}
                  >
                    <Icon name="trash-2" size={13} color="var(--color-cyber-cyan)" />
                  </button>
                </span>
                <span class="chat-conversation-divider-line"></span>
              </div>
            {/if}
            {#each convMsgs as msg (msg.id)}
              {@render messageRow(msg)}
            {/each}
          {/each}

          {#if configStore.systemPrompt.trim()}
            <details class="group mb-4 rounded-lg border border-cyber-border/60 bg-cyber-surface-2/40">
              <summary class="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs text-cyber-text-dim transition-colors hover:bg-cyber-surface-2/60">
                <Icon name="terminal" size={13} color="var(--color-cyber-cyan)" />
                <span class="font-medium">System Prompt</span>
                <span class="text-cyber-text-dim/50">{configStore.systemPrompt.length} chars</span>
                <svg class="ml-auto h-3 w-3 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
              </summary>
              <div class="prose-cyber max-h-64 overflow-y-auto border-t border-cyber-border/40 px-3 py-2.5 text-[12px] leading-relaxed text-cyber-text/90">{@html renderMarkdown(configStore.systemPrompt)}</div>
            </details>
          {/if}

          {#if showActiveDivider}
            <div class="chat-conversation-divider chat-conversation-divider-active" data-testid="conversation-divider-active" data-conversation-id={activeConversationId}>
              <span class="chat-conversation-divider-line"></span>
              <span class="chat-conversation-divider-label">
                <span class="chat-conversation-divider-date">{formatConversationDate(activeConvForDivider?.createdAt ?? Date.now())}</span>
                <span class="chat-conversation-divider-divider-dots" aria-hidden="true"></span>
                <button
                  type="button"
                  class="chat-conversation-divider-btn"
                  title="Export conversation"
                  aria-label="Export conversation"
                  onclick={(e) => {
                    e.stopPropagation();
                    exportConversationToJsonl(activeConversationId);
                  }}
                >
                  <Icon name="download" size={13} color="var(--color-cyber-cyan)" />
                </button>
                <button
                  type="button"
                  class="chat-conversation-divider-btn chat-conversation-divider-delete"
                  title="Delete conversation"
                  aria-label="Delete conversation"
                  onclick={(e) => {
                    e.stopPropagation();
                    if (confirm('Delete this conversation? This cannot be undone.')) {
                      deleteConversation(activeConversationId);
                    }
                  }}
                >
                  <Icon name="trash-2" size={13} color="var(--color-cyber-cyan)" />
                </button>
              </span>
              <span class="chat-conversation-divider-line"></span>
            </div>
          {/if}

          {#each messages as msg (msg.id)}
            {@render messageRow(msg)}
          {/each}
        </div>
      {/if}

      <!-- Input row (always visible when a conversation exists) -->
      {#if activeConversationId}
        {#if thinkingContent}
          <div class="mb-2 rounded-lg border border-cyber-purple/20 bg-cyber-purple/5 px-3 py-1.5">
            <div class="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-cyber-purple">
              <span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-cyber-purple"></span>
              Thinking...
            </div>
            <div class="mt-1 max-h-16 overflow-y-auto text-[11px] text-cyber-text-dim/70 whitespace-pre-wrap">{thinkingContent.slice(-200)}</div>
          </div>
        {/if}

        <AttachmentPreview attachments={attachments} onRemove={removeAttachment} />

        {#if messages.length > 0}
          <div class="mb-1 flex justify-center">
            <button
              onclick={() => (chatExpanded ? closeChat() : (chatExpanded = true))}
              class="flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-medium uppercase tracking-wider text-cyber-text-dim/45 transition-colors duration-200 hover:text-cyber-text-dim/80"
              title={chatExpanded ? 'Collapse chat' : 'Expand chat'}
              aria-label={chatExpanded ? 'Collapse chat' : 'Expand chat'}
              data-testid="chat-toggle"
            >
              <span class="inline-flex {chatExpanded ? '' : 'rotate-180'}">
                <Icon name="chevron-down" size={16} />
              </span>
              {chatExpanded ? 'Collapse' : 'Expand'}
            </button>
          </div>
        {/if}

        <div class="chat-inline-input-row mx-auto flex h-12 w-[36rem] items-stretch gap-1.5 rounded-full border-0 bg-cyber-surface-2/80 px-2 py-0 pr-0.5 transition-colors focus-within:ring-1 focus-within:ring-cyber-cyan/40">
          <textarea
            bind:this={panelTextareaEl}
            bind:value={panelChatInput}
            oninput={autoResizePanel}
            onkeydown={handlePanelKeydown}
            placeholder={chatExpanded ? 'Continue...' : 'Ask me anything...'}
            rows="1"
            data-testid="chat-input"
            class="max-h-[140px] min-h-12 flex-1 resize-none bg-transparent px-4 py-0 text-base leading-[3rem] text-cyber-text outline-none placeholder:text-cyber-text-dim/70 border-0 transition-colors"
            disabled={isActiveConversationStreaming}
          ></textarea>
          <AttachmentMenu
            fluid
            disabled={isActiveConversationStreaming || attachments.length >= MAX_ATTACHMENTS}
            onPickImage={openImagePicker}
            onPickDocument={openDocumentPicker}
          />
          {#if isActiveConversationStreaming}
            <button
              onclick={cancelStreaming}
              class="flex h-full aspect-square shrink-0 items-center justify-center rounded-full bg-cyber-red/20 text-cyber-red transition-all duration-200 hover:bg-cyber-red/30 ring-2 ring-cyber-red/40"
              title="Stop generating"
              data-testid="stop-button"
            >
              <Icon name="square" size={14} />
            </button>
          {:else}
            <button
              onclick={handleMicClick}
              disabled={isTranscribing || !recordingSupported}
              data-testid="mic-button"
              title={isTranscribing ? 'Transcribing…' : isRecording ? 'Stop recording' : 'Voice input'}
              class="flex h-full aspect-square shrink-0 items-center justify-center rounded-full transition-all duration-200 {isRecording ? 'bg-red-500/20 text-red-400 animate-pulse hover:bg-red-500/30 ring-2 ring-red-500/40' : isTranscribing ? 'bg-cyber-cyan/10 text-cyber-cyan animate-pulse ring-2 ring-cyber-cyan/30' : 'bg-cyber-cyan/15 text-cyber-cyan hover:bg-cyber-cyan/25 ring-1 ring-cyber-cyan/40'}"
            >
              {#if isRecording}
                <Icon name="square" size={16} />
              {:else if isTranscribing}
                <div class="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
              {:else}
                <Icon name="mic" size={22} />
              {/if}
            </button>
          {/if}
          </div>
      {/if}
    </div>

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
    <div class="h-full w-full overflow-hidden p-2 md:pt-14 md:pl-14 md:pr-4 md:pb-4">
      <IngestionPanel />
    </div>
  {/if}
</div>

<style>
  .chat-inline-overlay {
    position: absolute;
    left: 50%;
    bottom: 0;
    transform: translateX(-50%);
    z-index: 30;
    display: flex;
    width: 100%;
    max-width: 44rem;
    flex-direction: column;
    align-items: center;
    padding-bottom: 1rem;
    padding-left: 1rem;
    padding-right: 1rem;
    pointer-events: none;
  }

  .chat-inline-overlay > * {
    pointer-events: auto;
  }

  .chat-inline-messages {
    width: 100%;
    max-width: 42rem;
    max-height: 60dvh;
    overflow-y: auto;
    /* Prevent the browser's scroll anchoring from adjusting scrollTop
     * when older conversations are prepended — we handle restoration
     * manually in loadOlderConversation() via tick() + scrollTop delta. */
    overflow-anchor: none;
    /* Prevent scroll chaining so pull-to-refresh gesture isn't hijacked
     * by the parent when the container is at scrollTop=0. */
    overscroll-behavior-y: contain;
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    background: rgb(8, 11, 19);
    border: 1px solid rgba(0, 212, 255, 0.12);
    border-right: 0;
    scrollbar-width: thin;
    scrollbar-color: rgba(0, 212, 255, 0.25) transparent;
  }

  .chat-inline-messages::-webkit-scrollbar {
    width: 6px;
  }

  .chat-inline-messages::-webkit-scrollbar-thumb {
    background: rgba(0, 212, 255, 0.25);
    border-radius: 3px;
  }

  .chat-scroll-hint {
    position: sticky;
    top: 0;
    z-index: 5;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 6px 12px;
    flex-shrink: 0;
    background: linear-gradient(
      to bottom,
      rgba(0, 212, 255, 0.08) 0%,
      transparent 100%
    );
    color: var(--color-cyber-cyan);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
    opacity: 0.7;
    white-space: nowrap;
    animation: chat-hint-fade-in 0.2s ease-out;
  }

  .chat-scroll-hint svg {
    transform: rotate(180deg);
    opacity: 0.6;
    animation: chat-hint-bounce 1.5s ease-in-out infinite;
  }

  .chat-scroll-hint-loading svg {
    transform: none;
    opacity: 1;
    animation: none;
  }

  .chat-scroll-hint-loading {
    opacity: 0.9;
  }

  .chat-scroll-hint-done {
    opacity: 0.35;
  }

  @keyframes chat-hint-fade-in {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 0.7; transform: translateY(0); }
  }

  @keyframes chat-hint-bounce {
    0%, 100% { transform: rotate(180deg) translateY(0); }
    50% { transform: rotate(180deg) translateY(-3px); }
  }

  .chat-pull-spinner {
    display: flex;
    align-items: center;
    justify-content: center;
    animation: chat-pull-spin 0.8s linear infinite;
  }

  @keyframes chat-pull-spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  .chat-inline-header {
    width: 100%;
    max-width: 42rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 16px 6px 20px;
    background: rgba(8, 11, 19, 0.9);
    border-radius: 18px 18px 0 0;
    border: 1px solid rgba(0, 212, 255, 0.12);
    border-bottom: 0;
    pointer-events: auto;
  }

  .chat-inline-header-title {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--color-cyber-text-dim);
  }

  .chat-inline-messages {
    border-radius: 0 0 24px 24px;
    border-top: 0;
  }

  .chat-inline-input-row {
    width: 36rem;
    max-width: 100%;
  }

  .chat-conversation-divider {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 26px 0 18px;
    user-select: none;
    position: relative;
  }

  .chat-conversation-divider::before {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    top: 50%;
    height: 1px;
    transform: translateY(-50%);
    background: linear-gradient(
      to right,
      transparent 0%,
      rgba(0, 212, 255, 0.18) 12%,
      rgba(0, 212, 255, 0.42) 50%,
      rgba(0, 212, 255, 0.18) 88%,
      transparent 100%
    );
    box-shadow: 0 0 6px rgba(0, 212, 255, 0.14);
  }

  .chat-conversation-divider-line {
    display: none;
  }

  .chat-conversation-divider-label {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 14px;
    padding: 5px 10px 5px 16px;
    border-radius: 999px;
    background: rgb(8, 11, 19);
    border: 1px solid rgba(0, 212, 255, 0.35);
    color: var(--color-cyber-cyan);
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin: 0 auto;
    z-index: 1;
    box-shadow:
      0 0 12px rgba(0, 212, 255, 0.14),
      inset 0 0 6px rgba(0, 212, 255, 0.04);
    text-shadow: 0 0 6px rgba(0, 212, 255, 0.3);
  }

  .chat-conversation-divider-date {
    line-height: 1;
  }

  .chat-conversation-divider-divider-dots {
    width: 1px;
    height: 14px;
    background: linear-gradient(
      to bottom,
      transparent,
      rgba(0, 212, 255, 0.4) 50%,
      transparent
    );
    flex: 0 0 auto;
  }

  .chat-conversation-divider-active .chat-conversation-divider-label {
    border-color: rgba(0, 212, 255, 0.6);
    box-shadow:
      0 0 18px rgba(0, 212, 255, 0.28),
      inset 0 0 8px rgba(0, 212, 255, 0.08);
    text-shadow: 0 0 8px rgba(0, 212, 255, 0.5);
  }

  .chat-conversation-divider-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    padding: 0;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--color-cyber-cyan);
    cursor: pointer;
    opacity: 0.55;
    transition: opacity 0.15s ease, background 0.15s ease, transform 0.15s ease;
  }

  .chat-conversation-divider-btn:hover {
    opacity: 1;
    background: rgba(0, 212, 255, 0.14);
    transform: translateY(-1px);
  }

  .chat-conversation-divider-btn:focus-visible {
    outline: 1.5px solid rgba(0, 212, 255, 0.7);
    outline-offset: 1px;
    opacity: 1;
  }

  .chat-conversation-divider-delete:hover {
    background: rgba(255, 87, 87, 0.16);
    color: rgb(255, 120, 120);
  }

  .chat-conversation-divider-delete:focus-visible {
    outline-color: rgba(255, 120, 120, 0.7);
  }


  @media (max-width: 1024px) {
    .chat-inline-overlay {
      left: 8px;
      right: 8px;
      bottom: calc(64px + env(safe-area-inset-bottom, 0px));
      transform: none;
      max-width: 100%;
    }

    .chat-inline-messages {
      max-height: 56dvh;
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