<script lang="ts">
  import type { ChatMessage, MCPToolCall, OllamaMessage } from '$lib/constants';
  import { API } from '$lib/constants';
  import { tick, onMount } from 'svelte';
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
  import { kgApiClient, type JobInfo } from '$lib/services/kg-api-client';
  import { syncClient } from '$lib/services/sync-client.svelte';
  import { fileToAttachment, buildMessageContent, revokeAttachmentUrls, isImageType, MAX_ATTACHMENTS, MAX_FILE_SIZE, type Attachment } from '$lib/utils/file-utils';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import { isMobile } from '$lib/composables/use-breakpoint';
  import { createSheetDrag } from '$lib/composables/use-sheet-drag';

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

    /** Return all photo/image nodes from the graph store that are currently
     *  mounted (visible) on the 3D canvas. Falls back to all photo nodes if
     *  the SceneManager isn't available yet. */
    (window as any).__getPhotoNodes = () => {
      const nodes = graphStore.nodes.filter((n: any) => {
        const et = n.properties?.entity_type;
        return (
          (typeof et === 'string' && (et === 'Photo' || et === 'Image')) ||
          (n.id ?? '').includes('(Photo)') ||
          (n.id ?? '').includes('(Image)')
        );
      });
      const sm = (window as any).__sceneManager;
      if (!sm) return nodes;
      // Prefer mounted nodes (visible on canvas) so selection opens the overlay
      const mounted = nodes.filter((n: any) => sm.getCanvasNode(n.id));
      return mounted.length > 0 ? mounted : nodes;
    };

    /** Return all nodes of a given kind (person, location, event, etc.). */
    (window as any).__getNodesByLabel = (label: string) => {
      const l = label.toLowerCase();
      return graphStore.nodes.filter((n: any) =>
        n.labels?.some((lb: string) => lb.toLowerCase() === l)
      );
    };

    /**
     * Select a canvas node by its graph node ID — triggers the same
     * onSelectNode callback path as a real mouse click on the 3D canvas,
     * opening the NodeOverlay without needing to raycast at pixel coords.
     * Returns true if the node exists in the graph store (the overlay will
     * open even if the node's 3D chunk isn't currently mounted).
     */
    (window as any).__selectNode = (nodeId: string) => {
      const sm = (window as any).__sceneManager;
      if (!sm || typeof sm.onSelectNode !== 'function') return false;
      sm.onSelectNode(nodeId);
      return true;
    };

    /** Select the first photo node — convenience for tests. */
    (window as any).__selectFirstPhoto = () => {
      const photos = (window as any).__getPhotoNodes();
      if (!photos.length) return false;
      return (window as any).__selectNode(photos[0].id);
    };

    /** Select a photo node by index — convenience for tests. */
    (window as any).__selectPhotoByIndex = (index: number) => {
      const photos = (window as any).__getPhotoNodes();
      if (index < 0 || index >= photos.length) return false;
      return (window as any).__selectNode(photos[index].id);
    };
  }

  onMount(() => {
    resumeInProgressJobs();
  });
  let activeConversationId = $state('');
  let messages = $state<ChatMessage[]>([]);
  let isStreaming = $state(false);
  let chatInput = $state('');
  let panelChatInput = $state('');
  let chatExpanded = $state(false);
  let suppressCloseChat = false;
  let availableModels = $state<string[]>([]);
  let selectedModel = $state('');
  let textareaEl: HTMLTextAreaElement | undefined = $state();
  let panelTextareaEl: HTMLTextAreaElement | undefined = $state();
  let messagesContainer: HTMLDivElement | undefined = $state();

  // The active conversation object, for the divider label above the active
  // messages. Kept as a derived so the divider re-renders when the active
  // conversation changes or its title updates.
  let activeConvForDivider = $derived(
    conversations.find((c) => c.id === activeConversationId) ?? null
  );
  let showActiveDivider = $derived(
    !!activeConvForDivider && messages.length > 0
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

  // Whether any conversation (active or background) is currently streaming
  // a response. Drives the loading indicator on the mic and lets a tap on
  // the collapsed orb jump straight to that conversation.
  let isStreamActive = $derived(
    isStreaming && streamingConversationId !== null && streamingConversationId !== ''
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
  // Collapsed orb: a tap toggles the sibling "Add images" / "Type a message"
  // options open (pinned), complementing the existing CSS hover reveal.
  let orbOptionsOpen = $state(false);
  // Hover tooltip for the mic button. The native title attribute flashes on
  // focus and can't be styled, so we render a small custom tooltip instead.
  let micTooltipVisible = $state(false);
  let micTooltipMessage = $derived(
    isTranscribing
      ? 'Transcribing…'
      : isStreamActive
        ? 'Open conversation'
        : isRecording
          ? 'Tap to stop & send'
          : 'Tap to record'
  );
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
      handleMicTap();
    }
    window.addEventListener('keydown', onSpacePress);
    return () => window.removeEventListener('keydown', onSpacePress);
  });

  // ── Voice input ───────────────────────────────────────────────
  // Single tap on the mic starts recording immediately. A second tap
  // while recording stops and sends. A double-tap (two taps within
  // DOUBLE_TAP_MS) reveals the orb's sibling options (Type / Add images).
  // Desktop uses pointer events; mobile uses touch events with a
  // holdStartedByTouch guard to prevent the pointer layer from
  // clobbering touch state.
  let holdStartedByTouch = false;
  let suppressPointerTap = false;
  let lastTapTime = 0;
  const DOUBLE_TAP_MS = 300;

  async function handleMicPointerDown(e: PointerEvent) {
    if (!audioRecorder || !recordingSupported) return;
    if (isTranscribing || micBusy) return;
    if (holdStartedByTouch) return;
  }

  function handleMicTouchStart(e: TouchEvent) {
    if (!audioRecorder || !recordingSupported) return;
    if (isTranscribing || micBusy) return;
    holdStartedByTouch = true;
  }

  function handleMicTouchMove(e: TouchEvent) {
    if (isRecording) e.preventDefault();
  }

  function handleMicTouchEnd(e: TouchEvent) {
    if (!holdStartedByTouch) return;
    holdStartedByTouch = false;
    suppressPointerTap = true;
    handleMicTap();
    setTimeout(() => { suppressPointerTap = false; }, 400);
  }

  function handleMicTap() {
    if (!audioRecorder || !recordingSupported) return;
    if (isTranscribing || micBusy) return;
    if (isStreamActive) {
      openStreamingConversation();
      return;
    }
    const now = Date.now();
    if (now - lastTapTime < DOUBLE_TAP_MS) {
      lastTapTime = 0;
      orbOptionsOpen = !orbOptionsOpen;
      return;
    }
    lastTapTime = now;
    if (isRecording) {
      stopAndSendRecording();
    } else {
      startRecording();
    }
  }

  async function handleMicPointerUp() {
    if (holdStartedByTouch || suppressPointerTap) return;
    handleMicTap();
  }

  function startRecording() {
    if (!audioRecorder) return;
    orbOptionsOpen = false;
    audioRecorder.startRecording()
      .then(() => { isRecording = true; })
      .catch((err) => console.error('Failed to start recording:', err));
  }

  function stopAndSendRecording() {
    if (!audioRecorder) return;
    isRecording = false;
    micBusy = true;
    isTranscribing = true;
    audioRecorder.stopRecording()
      .then(async (wavBlob) => {
        const audioUrl = URL.createObjectURL(wavBlob);
        const audioData = await blobToBase64(wavBlob);
        const transcript = await transcribeAudio(wavBlob, API.llama.transcriptions);
        const text = transcript.trim();
        if (text) await handleSend(audioUrl, audioData, 'wav', false, text);
      })
      .catch((err) => console.error('Push-to-talk send failed:', err))
      .finally(() => {
        isTranscribing = false;
        micBusy = false;
      });
  }

  function handleMicPointerLeave() {
    if (holdStartedByTouch) return;
  }

  function handleMicPointerCancel() {
    if (holdStartedByTouch) return;
    holdStartedByTouch = false;
    if (isRecording) {
      isRecording = false;
      micBusy = true;
      audioRecorder?.stopRecording().finally(() => { micBusy = false; });
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

    for (const result of results) {
      if (result.status === 'fulfilled') {
        const att = result.value;
        newAttachments.push(att);
      } else {
        attachError = result.reason instanceof Error ? result.reason.message : 'Failed to attach file';
      }
    }

    if (newAttachments.length > 0) {
      attachments = [...attachments, ...newAttachments];
    }
  }

  /** Process image files picked from the graph page directly through the KG
   *  EXIF pipeline. Does NOT add them to chat attachments, does NOT send them
   *  to the LLM. Uploads all jobs in parallel, then connects SSE with
   *  capped concurrency to avoid exhausting the browser connection pool. */
  async function handleGraphImageFiles(fileList: FileList | null) {
    if (!fileList) return;
    const files = Array.from(fileList);

    const attachments: Attachment[] = [];
    for (const file of files) {
      if (file.size > MAX_FILE_SIZE) {
        console.warn(`${file.name} exceeds ${MAX_FILE_SIZE / 1024 / 1024}MB limit`);
        continue;
      }
      const mimeType = file.type || 'image/jpeg';
      if (!isImageType(mimeType)) continue;
      attachments.push({
        id: crypto.randomUUID(),
        file,
        mimeType,
        dataUrl: '',
        thumbnailUrl: URL.createObjectURL(file),
        name: file.name,
        size: file.size,
      });
    }

    await submitImageBatch(attachments);
    if (imageFileInput) imageFileInput.value = '';
  }

  /** Upload a batch of image attachments as jobs, add photo nodes to the
   *  graph immediately, then connect SSE streams with capped concurrency. */
  const MAX_CONCURRENT_SSE = 3;

  async function submitImageBatch(attachments: Attachment[]) {
    const results = await Promise.allSettled(
      attachments.map((att) => kgApiClient.createJob(att.file, { insert: true, note: '' }).then((job) => ({ att, job })))
    );

    const toTrack: { jobId: string; photoNodeId: string; att: Attachment }[] = [];
    for (const r of results) {
      if (r.status !== 'fulfilled') continue;
      const { att, job } = r.value;
      const photoNodeId = `${att.name} (Photo)`;
      imageProcessingStore.startProcessing(photoNodeId, att.name, att.thumbnailUrl ?? att.dataUrl ?? '', job.job_id);
      graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', source_id: att.name });
      if (att.thumbnailUrl ?? att.dataUrl) {
        graphStore.setPhotoImage(photoNodeId, att.thumbnailUrl ?? att.dataUrl ?? '');
      }
      toTrack.push({ jobId: job.job_id, photoNodeId, att });
    }

    let activeSSE = 0;
    const queue = [...toTrack];
    const next = (): void => {
      while (activeSSE < MAX_CONCURRENT_SSE && queue.length > 0) {
        const item = queue.shift()!;
        activeSSE++;
        consumeJobEvents(item.jobId, item.photoNodeId, item.att).finally(() => {
          activeSSE--;
          next();
        });
      }
    };
    next();
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
      handleGraphImageFiles(files);
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
        if (!syncClient.getCachedMessages(id)) {
          await syncClient.loadConversation(id);
        }
      }
      if (!syncClient.getCachedMessages(id) && conv.messages.length > 0) {
        syncClient.seedCachedMessages(id, conv.messages);
      }
      graphStore.loadConversations();
    } else {
      messages = [];
    }

    // Clear unread badge for this conversation
    if (unreadConversations.has(id)) {
      unreadConversations = new Set([...unreadConversations].filter((cid) => cid !== id));
    }

    chatExpanded = true;
  }

  /** A tap on the mic while a conversation is streaming opens that
   *  conversation so the user can watch the in-flight response. */
  function openStreamingConversation() {
    const id = streamingConversationId;
    if (!id) return;
    // Already viewing it — just make sure the panel is open.
    if (id === activeConversationId) {
      chatExpanded = true;
      return;
    }
    switchConversation(id);
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
     const maxTurns = 3;
     let turn = 0;

       type ContentPart = { type: string; text?: string; image_url?: { url: string } };
       type ApiMessage = { role: string; content: string | ContentPart[] | null; tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }> } | { role: 'tool'; tool_call_id: string; content: string };
       let apiMessages: ApiMessage[] = [
         { role: 'system', content: configStore.systemPrompt.replaceAll('{{CURRENT_DATE}}', new Date().toISOString().slice(0, 10)) },
         ...messages
           .filter((m) => !m.isStreaming)
           .map((m) => {
             return { role: m.role, content: m.content } as ApiMessage;
           })
       ];

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
          processingLabel = 'Thinking...';
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
          // At Q1 quant the model sometimes never emits <|im_end|> on long
          // open-ended turns, falling into paraphrase loops until externally
          // truncated. max_tokens caps generation so the stream always
          // terminates; stop strings are a belt-and-suspenders backstop in
          // case the chat-template EOS isn't honored in streaming.
          max_tokens: 2048,
          stop: ['<|im_end|>'],
        };

        if (tools.length > 0) {
          // Turn 1: force a tool call. The model (Bonsai-27B-Q1_0) reliably
          // skips tool calls under tool_choice='auto' and emits the literal
          // confirmation string ("Saved.") from the system prompt instead.
          // 'required' makes llama-server apply a grammar that guarantees a
          // tool_call is produced, so logging/retrieval always hits the graph.
          // Turn 2+: only offer query tools — the model was calling
          // save_to_knowledge_graph during retrieval queries and re-querying,
          // both wasteful. Strip save + query tools after turn 1 so the model
          // can only produce its final conversational answer.
          const turnTools = turn === 1 ? tools : tools.filter(
            (t: { function: { name: string } }) => {
              const name = t.function?.name;
              return name !== 'save_to_knowledge_graph' && name !== 'query_knowledge_graph' && name !== 'query_knowledge_graph_stream';
            }
          );
          requestBody.tools = turnTools.length > 0 ? turnTools : undefined;
          requestBody.tool_choice = turn === 1 ? 'required' : 'auto';
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
          processingLabel = 'Writing...';
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
            query_knowledge_graph: 'Looking through your memories...',
            query_knowledge_graph_stream: 'Looking through your memories...',
            save_to_knowledge_graph: 'Saving that for you...',
            list_documents: 'Gathering your records...',
          };
          if (streamingConversationId === activeConversationId) {
            processingLabel = toolLabels[tc.name] || `Working on it...`;
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
            const MAX_TOOL_CHARS = 50000;
            let toolContent = parsed.contextText;
            if (toolContent.length > MAX_TOOL_CHARS) {
              const relIdx = toolContent.indexOf('Knowledge Graph Data (Relationship)');
              if (relIdx !== -1 && relIdx < MAX_TOOL_CHARS) {
                const truncated = toolContent.slice(0, MAX_TOOL_CHARS);
                const lastNl = truncated.lastIndexOf('\n');
                toolContent = toolContent.slice(0, lastNl !== -1 ? lastNl : MAX_TOOL_CHARS) + '\n```';
              } else {
                const truncated = toolContent.slice(0, MAX_TOOL_CHARS);
                const lastNl = truncated.lastIndexOf('\n');
                toolContent = truncated.slice(0, lastNl !== -1 ? lastNl : MAX_TOOL_CHARS);
              }
            }
            apiMessages.push({
              role: 'tool',
              tool_call_id: tc.id,
              content: toolContent,
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

    const userMsg: ChatMessage = {
      id: generateId(),
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
      ...(audioUrl ? { audioUrl } : {}),
      ...(audioData ? { audioData, audioFormat: audioFormat ?? 'wav' } : {}),
    };
    messages = [...messages, userMsg];

    const conv = conversations.find((c) => c.id === activeConversationId);
    if (conv && !conv.title) {
      const titleSource = trimmed || (audioData ? 'Voice memo' : '');
      if (titleSource) {
        conv.title = titleSource.slice(0, 50) + (titleSource.length > 50 ? '…' : '');
      }
    }
    conv?.updatedAt && (conv.updatedAt = Date.now());

    saveMessagesToConversation();

    const sentAttachments = [...attachments];
    chatInput = '';
    revokeAttachmentUrls(attachments);
    attachments = [];
    attachError = '';
    requestAnimationFrame(() => {
      if (textareaEl) textareaEl.style.height = 'auto';
      scrollToBottom();
    });

    await streamAssistantResponse(sentAttachments);
  }

  /** Process a single image through the KG pipeline with real-time SSE progress. */
  async function processSingleImage(att: Attachment, note: string = '') {
    const photoNodeId = `${att.name} (Photo)`;
    try {
      const job = await kgApiClient.createJob(att.file, { insert: true, note });
      imageProcessingStore.startProcessing(photoNodeId, att.name, att.thumbnailUrl ?? att.dataUrl ?? '', job.job_id);
      graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', source_id: att.name });
      if (att.thumbnailUrl ?? att.dataUrl) {
        graphStore.setPhotoImage(photoNodeId, att.thumbnailUrl ?? att.dataUrl ?? '');
      }
      consumeJobEvents(job.job_id, photoNodeId, att);
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
            const exif = eventData.exif as Record<string, unknown>;
            imageProcessingStore.setExifData(photoNodeId, exif);
            graphStore.mergeNodeProperties(photoNodeId, ['Photo'], exif);
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

  /** On page load, reconnect to any in-progress or queued jobs from the server
   *  so the processing queue survives refreshes. Sets all store entries
   *  immediately (dots appear), then polls for status updates with limited
   *  concurrency to avoid exhausting the browser's HTTP connection pool. */
  async function resumeInProgressJobs() {
    try {
      const [pending, processing] = await Promise.all([
        kgApiClient.listJobs('pending'),
        kgApiClient.listJobs('processing'),
      ]);

      for (const job of pending) {
        const photoNodeId = `${job.file_source} (Photo)`;
        if (imageProcessingStore.getByJobId(job.job_id)) continue;
        imageProcessingStore.startProcessing(photoNodeId, job.file_source, '', job.job_id);
        graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', source_id: job.file_source });
      }

      for (const job of processing) {
        const photoNodeId = `${job.file_source} (Photo)`;
        const existing = imageProcessingStore.getByJobId(job.job_id);
        if (!existing) {
          const stage = job.stage === 'exif_complete' ? 'queued_for_ai' : 'extracting_exif';
          imageProcessingStore.startProcessing(photoNodeId, job.file_source, '', job.job_id);
          if (stage === 'queued_for_ai') imageProcessingStore.updateStage(photoNodeId, 'queued_for_ai');
          graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', source_id: job.file_source });
        }
      }

      const allJobs = [...pending, ...processing];
      pollJobBatch(allJobs);
    } catch (err) {
      console.warn('Failed to resume in-progress jobs:', err);
    }
  }

  /** Poll a batch of jobs for status updates with limited concurrency.
   *  When a job transitions to 'processing', opens a single SSE stream for it.
    *  Caps concurrent SSE connections to avoid exhausting the browser's HTTP
    *  connection pool (browsers allow ~6 per origin). */

  async function pollJobBatch(jobs: JobInfo[]) {
    const activeSSE = new Set<string>();
    const pollIntervalMs = 5000;

    while (true) {
      await new Promise((r) => setTimeout(r, pollIntervalMs));

      const liveJobs = jobs.filter((j) => {
        const photoNodeId = `${j.file_source} (Photo)`;
        const status = imageProcessingStore.statuses[photoNodeId];
        if (!status) return false;
        return status.stage !== 'complete' && status.stage !== 'error';
      });
      if (liveJobs.length === 0) return;
      jobs = liveJobs;

      for (const job of jobs) {
        const photoNodeId = `${job.file_source} (Photo)`;
        if (activeSSE.has(job.job_id)) continue;

        try {
          const fresh = await kgApiClient.getJob(job.job_id);
          if (fresh.status === 'complete') {
            imageProcessingStore.updateStage(photoNodeId, 'complete');
            graphStore.pipelineDone = true;
            graphStore.refresh();
            continue;
          }
          if (fresh.status === 'failed' || fresh.status === 'cancelled') {
            imageProcessingStore.updateStage(photoNodeId, 'error', fresh.error || `Job ${fresh.status}`);
            continue;
          }
          if (fresh.status === 'processing' && activeSSE.size < MAX_CONCURRENT_SSE) {
            activeSSE.add(job.job_id);
            consumeJobEvents(job.job_id, photoNodeId, { name: job.file_source, dataUrl: '' } as Attachment)
              .finally(() => activeSSE.delete(job.job_id));
          }
        } catch {
          // transient error — keep polling
        }
      }
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

  function handleSelectConversation(id: string) {
    suppressCloseChat = true;
    switchConversation(id);
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

  let chatSheetClosing = $state(false);

  function closeChat() {
    if ($isMobile && chatExpanded && !chatSheetClosing) {
      if (chatSheetEl) chatSheetEl.style.transform = '';
      chatSheetClosing = true;
      saveMessagesToConversation();
      setTimeout(() => {
        chatExpanded = false;
        chatSheetClosing = false;
      }, 280);
      return;
    }
    saveMessagesToConversation();
    chatExpanded = false;
  }

  let chatSheetEl: HTMLDivElement | undefined = $state();
  $effect(() => {
    if (!$isMobile || !chatExpanded || !chatSheetEl) return;
    const handler = createSheetDrag({
      sheet: chatSheetEl,
      onDismiss: () => closeChat(),
    });
    return () => handler.destroy();
  });

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
      if (conversations.length > 0) {
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
    graphStore.setActiveConversation(activeConversationId);
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
    if (suppressCloseChat) {
      suppressCloseChat = false;
      return;
    }
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
      <CanvasView onqueryAbout={handleQueryAbout} onselectconversation={handleSelectConversation} />
    </div>

    <!-- Inline chat overlay (game-style, bottom-right) -->
    {#if $isMobile && chatExpanded && activeConversationId}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="chat-sheet-backdrop" class:backdrop-closing={chatSheetClosing} onclick={closeChat} role="presentation"></div>
    {/if}
    <div class="chat-inline-overlay" class:sheet-closing={chatSheetClosing} bind:this={chatSheetEl} data-testid="chat-inline-overlay">
      <!-- Image input: graph page "Add Image" button → EXIF pipeline only -->
      <input
        bind:this={imageFileInput}
        type="file"
        accept="image/*"
        multiple
        class="hidden"
        data-testid="image-file-input"
        onchange={(e) => handleGraphImageFiles((e.target as HTMLInputElement).files)}
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

      {#if chatExpanded && activeConversationId}
        <div class="chat-sheet-handle" data-testid="chat-sheet-handle"><div class="chat-sheet-handle-bar"></div></div>
        <div class="chat-inline-header" data-testid="chat-inline-header">
          <button
            onclick={closeChat}
            class="chat-inline-close-btn"
            title="Close conversation"
            aria-label="Close conversation"
            data-testid="chat-close-button"
          >
            <Icon name="x" size={18} color="currentColor" />
          </button>
          <span class="chat-inline-header-title">Conversation</span>
          <button
            onclick={() => { historyPanelOpen.update((v) => !v); }}
            class="chat-history-btn flex h-7 w-7 items-center justify-center rounded-md bg-cyber-surface-2/50 text-cyber-text-dim/70 transition-all duration-200 hover:bg-cyber-cyan/10 hover:text-cyber-cyan"
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
        >
          {#if messages.length === 0}
            <div class="chat-empty-state" data-testid="chat-empty-state">
              <div class="chat-empty-state-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
              </div>
              <div class="chat-empty-state-title">New conversation</div>
              <div class="chat-empty-state-hint">Type a message below to begin.</div>
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
                          {#if msg.isStreaming}
                            <span class="text-cyber-text-dim/50 animate-pulse">...</span>
                          {/if}
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
                          <span class="text-xs text-cyber-text-dim">Writing...</span>
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
                        <details class="group" open={false} data-testid="tool-call" data-tool-name={toolCall.toolName} data-tool-call-id={toolCall.id || ''}>
                          <summary class="flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] transition-colors {toolCall.isError ? 'border-cyber-red/30 bg-cyber-red/5 hover:bg-cyber-red/10' : toolCall.result ? 'border-cyber-green/30 bg-cyber-green/5 hover:bg-cyber-green/10' : 'border-cyber-orange/30 bg-cyber-orange/5 hover:bg-cyber-orange/10'}" data-testid="tool-call-summary">
                            {#if toolCall.isError}
                              <svg class="h-3.5 w-3.5 shrink-0 text-cyber-red" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                            {:else if toolCall.result}
                              <svg class="h-3.5 w-3.5 shrink-0 text-cyber-green" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
                            {:else}
                              <span class="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-cyber-orange"></span>
                            {/if}
                            <span class="font-mono {toolCall.isError ? 'text-cyber-red' : toolCall.result ? 'text-cyber-green' : 'text-cyber-orange'}">{isSave ? (isRunning ? 'Saving to knowledge graph' : 'Saved to knowledge graph') : (isRunning ? 'Searching knowledge graph' : 'Searched knowledge graph')}</span>
                            {#if isRunning}
                              <span class="text-[10px] text-cyber-orange/80 animate-pulse">{isSave ? 'saving...' : 'looking...'}</span>
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
                                <span>{isSave ? 'Saving that for you...' : 'Looking through your memories...'}</span>
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

        {#if chatExpanded}
          {#if messages.length > 0}
            <div class="chat-collapse-row mb-1 flex justify-center">
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

          <div class="chat-inline-input-row flex h-12 w-full items-stretch gap-1.5 rounded-full border-0 bg-cyber-surface-2/80 px-2 py-0 pr-0.5 transition-colors focus-within:ring-1 focus-within:ring-cyber-cyan/40">
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
                onpointerdown={handleMicPointerDown}
                onpointerup={handleMicPointerUp}
                onpointerleave={handleMicPointerLeave}
                onpointercancel={handleMicPointerCancel}
                ontouchstart={handleMicTouchStart}
                ontouchmove={handleMicTouchMove}
                ontouchend={handleMicTouchEnd}
                oncontextmenu={(e) => e.preventDefault()}
                onmouseenter={() => { micTooltipVisible = true; }}
                onmouseleave={() => { micTooltipVisible = false; }}
                disabled={isTranscribing || !recordingSupported}
                data-testid="mic-button"
                title={isTranscribing ? 'Transcribing…' : isStreamActive ? 'Open streaming conversation' : isRecording ? 'Tap to stop & send' : 'Tap to record'}
                class="relative flex h-full aspect-square shrink-0 items-center justify-center rounded-full transition-all duration-200 {isRecording ? 'bg-red-500/20 text-red-400 animate-pulse hover:bg-red-500/30 ring-2 ring-red-500/40' : isTranscribing ? 'bg-cyber-cyan/10 text-cyber-cyan animate-pulse ring-2 ring-cyber-cyan/30' : isStreamActive ? 'bg-cyber-purple/15 text-cyber-purple animate-pulse ring-2 ring-cyber-purple/40 hover:bg-cyber-purple/25' : 'bg-cyber-cyan/15 text-cyber-cyan hover:bg-cyber-cyan/25 ring-1 ring-cyber-cyan/40'}"
              >
                {#if isRecording}
                  <Icon name="square" size={16} />
                {:else if isTranscribing}
                  <div class="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
                {:else if isStreamActive}
                  <div class="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
                {:else}
                  <Icon name="mic" size={22} />
                {/if}
                {#if micTooltipVisible && !isRecording}
                  <span class="mic-tooltip mic-tooltip-left" role="tooltip" data-testid="mic-tooltip">{micTooltipMessage}</span>
                {/if}
              </button>
            {/if}
            </div>
        {/if}
      {/if}
    </div>

    {#if !chatExpanded}
      <div class="chat-collapsed-orb-host" data-testid="chat-collapsed-orb">
        <div class="chat-collapsed-orb">
          <div
            class="chat-orb"
            class:recording={isRecording}
            class:options-open={orbOptionsOpen}
            class:streaming={isStreamActive}
            role="button"
            tabindex="0"
            aria-label={isRecording ? 'Stop recording' : isStreamActive ? 'Open streaming conversation' : 'Voice input'}
            data-od-id="chat-orb"
            data-testid="mic-button"
            onpointerdown={handleMicPointerDown}
            onpointerup={handleMicPointerUp}
            onpointerleave={handleMicPointerLeave}
            onpointercancel={handleMicPointerCancel}
            ontouchstart={handleMicTouchStart}
            ontouchmove={handleMicTouchMove}
            ontouchend={handleMicTouchEnd}
            oncontextmenu={(e) => e.preventDefault()}
            onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); if (isStreamActive) { openStreamingConversation(); } else { handleMicTap(); } } }}
            onmouseenter={() => { micTooltipVisible = true; }}
            onmouseleave={() => { micTooltipVisible = false; }}
          >
            {#if isTranscribing}
              <div class="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
            {:else if isStreamActive}
              <div class="h-5 w-5 border-2 border-current border-t-transparent rounded-full animate-spin"></div>
            {:else if isRecording}
              <Icon name="square" size={16} />
            {:else}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
            {/if}
            {#if micTooltipVisible && !isRecording}
              <span class="mic-tooltip" role="tooltip" data-testid="mic-tooltip">{micTooltipMessage}</span>
            {/if}
          </div>
          <div
            class="chat-orb-add"
            class:show={orbOptionsOpen}
            role="button"
            tabindex="0"
            aria-label="Add images"
            data-od-id="chat-orb-add"
            onclick={() => { chatExpanded = true; tick().then(() => imageFileInput?.click()); }}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chatExpanded = true; tick().then(() => imageFileInput?.click()); } }}
          >
            <div class="coa-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
            </div>
            <span>Add images</span>
          </div>
          <div
            class="chat-orb-expand"
            class:show={orbOptionsOpen}
            role="button"
            tabindex="0"
            aria-label="Type a message"
            data-od-id="chat-orb-expand"
            onclick={(e) => { e.stopPropagation(); orbOptionsOpen = false; startNewConversation(); chatExpanded = true; tick().then(() => requestAnimationFrame(() => panelTextareaEl?.focus())); }}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); orbOptionsOpen = false; startNewConversation(); chatExpanded = true; tick().then(() => requestAnimationFrame(() => panelTextareaEl?.focus())); } }}
          >
            <div class="coe-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
            </div>
            <span>Type a message</span>
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
    <div class="h-full w-full overflow-hidden p-2 md:pt-14 md:pl-14 md:pr-4 md:pb-4">
      <IngestionPanel />
    </div>
  {/if}
</div>

<style>
  .chat-inline-overlay {
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    z-index: 30;
    display: flex;
    width: 24rem;
    flex-direction: column;
    align-items: stretch;
    padding: 1rem;
    padding-bottom: 1rem;
    pointer-events: none;
  }

  .chat-inline-overlay > * {
    pointer-events: auto;
  }

  /* ── Collapsed chat orb ── */
  .chat-collapsed-orb-host {
    position: absolute;
    left: 50%;
    bottom: calc(1rem + env(safe-area-inset-bottom, 0px));
    transform: translateX(-50%);
    z-index: 30;
    pointer-events: auto;
  }

  .chat-collapsed-orb {
    display: flex;
    flex-direction: column-reverse;
    align-items: center;
    gap: 0;
    pointer-events: auto;
  }

  .chat-orb {
    width: 64px;
    height: 64px;
    border-radius: 50%;
    background: oklch(18% 0.02 255 / 85%);
    backdrop-filter: blur(24px) saturate(1.5);
    -webkit-backdrop-filter: blur(24px) saturate(1.5);
    border: 1px solid oklch(82% 0.14 210 / 25%);
    box-shadow:
      0 0 0 1px oklch(82% 0.14 210 / 12%),
      0 0 32px oklch(82% 0.14 210 / 20%),
      0 0 64px oklch(82% 0.14 210 / 8%),
      0 12px 40px oklch(0% 0 0 / 50%);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: var(--color-cyber-cyan);
    transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
    position: relative;
    touch-action: none;
    -webkit-user-select: none;
    user-select: none;
    -webkit-touch-callout: none;
    flex-shrink: 0;
  }
  .chat-orb svg {
    width: 26px;
    height: 26px;
    transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .chat-orb::after {
    content: '';
    position: absolute;
    inset: -4px;
    border-radius: 50%;
    border: 1px solid oklch(82% 0.14 210 / 0%);
    transition: border-color 0.4s, inset 0.4s;
    pointer-events: none;
  }
  .chat-collapsed-orb:hover .chat-orb,
  .chat-orb:hover {
    color: var(--color-cyber-cyan);
    border-color: oklch(82% 0.14 210 / 40%);
    box-shadow:
      0 0 0 1px oklch(82% 0.14 210 / 20%),
      0 0 48px oklch(82% 0.14 210 / 30%),
      0 0 96px oklch(82% 0.14 210 / 12%),
      0 16px 48px oklch(0% 0 0 / 60%);
    transform: scale(1.08);
  }
  .chat-orb::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    border: 2px solid oklch(82% 0.14 210 / 0%);
    animation: orbPulse 3s ease-in-out infinite;
    pointer-events: none;
  }
  @keyframes orbPulse {
    0%, 100% { border-color: oklch(82% 0.14 210 / 0%); inset: 0; }
    50% { border-color: oklch(82% 0.14 210 / 15%); inset: -6px; }
  }
  .chat-orb.recording {
    color: oklch(62% 0.20 18);
    border-color: oklch(62% 0.20 18 / 40%);
    background: oklch(20% 0.02 18 / 80%);
    box-shadow:
      0 0 0 1px oklch(62% 0.20 18 / 15%),
      0 0 32px oklch(62% 0.20 18 / 24%),
      0 0 64px oklch(62% 0.20 18 / 10%),
      0 12px 40px oklch(0% 0 0 / 50%);
  }
  .chat-orb.recording::before {
    border-color: oklch(62% 0.20 18 / 30%);
    animation: orbRecording 1s ease-in-out infinite;
  }
  @keyframes orbRecording {
    0%, 100% { border-color: oklch(62% 0.20 18 / 20%); inset: 0; }
    50% { border-color: oklch(62% 0.20 18 / 8%); inset: -12px; }
  }

  /* Loading indicator: a conversation is streaming in the background. The
     orb tints purple and pulses so a tap can jump to that conversation. */
  .chat-orb.streaming {
    color: var(--color-cyber-purple);
    border-color: oklch(60% 0.18 300 / 35%);
    background: oklch(20% 0.04 300 / 80%);
    box-shadow:
      0 0 0 1px oklch(60% 0.18 300 / 15%),
      0 0 32px oklch(60% 0.18 300 / 22%),
      0 0 64px oklch(60% 0.18 300 / 10%),
      0 12px 40px oklch(0% 0 0 / 50%);
  }
  .chat-orb.streaming::before {
    border-color: oklch(60% 0.18 300 / 30%);
    animation: orbStreaming 1.4s ease-in-out infinite;
  }
  @keyframes orbStreaming {
    0%, 100% { border-color: oklch(60% 0.18 300 / 25%); inset: 0; }
    50% { border-color: oklch(60% 0.18 300 / 10%); inset: -10px; }
  }

  .chat-orb-expand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 16px 8px 8px;
    border-radius: 100px;
    background: oklch(16% 0.015 255 / 80%);
    backdrop-filter: blur(24px) saturate(1.5);
    -webkit-backdrop-filter: blur(24px) saturate(1.5);
    border: 1px solid oklch(50% 0.03 255 / 12%);
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 6%),
      0 12px 40px oklch(0% 0 0 / 50%);
    cursor: pointer;
    color: var(--color-cyber-text);
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    opacity: 0;
    transform: translateY(20px) scale(0.9);
    margin-bottom: 10px;
    pointer-events: none;
    transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1) 0.05s;
  }
  .chat-orb-expand .coe-icon {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: oklch(82% 0.14 210 / 12%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-cyber-cyan);
    flex-shrink: 0;
  }
  .chat-orb-expand .coe-icon svg { width: 16px; height: 16px; }
  .chat-collapsed-orb:hover .chat-orb-expand {
    opacity: 1;
    transform: translateY(0) scale(1);
    pointer-events: auto;
  }
  .chat-orb-expand:hover {
    border-color: oklch(82% 0.14 210 / 25%);
    box-shadow:
      0 0 0 1px oklch(82% 0.14 210 / 10%),
      0 0 24px oklch(82% 0.14 210 / 12%),
      0 12px 40px oklch(0% 0 0 / 50%);
  }

  .chat-orb-add {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 16px 8px 8px;
    border-radius: 100px;
    background: oklch(16% 0.02 150 / 80%);
    backdrop-filter: blur(24px) saturate(1.5);
    -webkit-backdrop-filter: blur(24px) saturate(1.5);
    border: 1px solid oklch(50% 0.03 255 / 12%);
    box-shadow:
      0 0 0 1px oklch(50% 0.03 255 / 6%),
      0 12px 40px oklch(0% 0 0 / 50%);
    cursor: pointer;
    color: var(--color-cyber-text);
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    opacity: 0;
    transform: translateY(20px) scale(0.9);
    margin-bottom: 10px;
    pointer-events: none;
    transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1) 0.1s;
  }
  .chat-orb-add .coa-icon {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: oklch(72% 0.15 150 / 12%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: oklch(72% 0.15 150);
    flex-shrink: 0;
  }
  .chat-orb-add .coa-icon svg { width: 16px; height: 16px; }
  .chat-collapsed-orb:hover .chat-orb-add {
    opacity: 1;
    transform: translateY(0) scale(1);
    pointer-events: auto;
  }
  .chat-orb-add:hover {
    border-color: oklch(72% 0.15 150 / 25%);
    box-shadow:
      0 0 0 1px oklch(72% 0.15 150 / 10%),
      0 0 24px oklch(72% 0.15 150 / 12%),
      0 12px 40px oklch(0% 0 0 / 50%);
  }

  /* While recording the sibling option pills stay hidden. */
  .chat-orb.recording ~ .chat-orb-add,
  .chat-orb.recording ~ .chat-orb-expand {
    opacity: 0 !important;
    transform: translateY(20px) scale(0.9) !important;
    pointer-events: none !important;
  }
  /* Tap-to-reveal: pin the sibling options open independent of hover. */
  .chat-orb.options-open ~ .chat-orb-add,
  .chat-orb.options-open ~ .chat-orb-expand {
    opacity: 1;
    transform: translateY(0) scale(1);
    pointer-events: auto;
  }
  .chat-orb-add.show,
  .chat-orb-expand.show {
    opacity: 1;
    transform: translateY(0) scale(1);
    pointer-events: auto;
  }

  /* ── Hover tooltip for the mic button (both orb + panel) ── */
  /* Default: appears to the RIGHT of the button. */
  .mic-tooltip {
    position: absolute;
    left: calc(100% + 10px);
    top: 50%;
    transform: translateY(-50%);
    background: oklch(14% 0.02 255 / 92%);
    backdrop-filter: blur(12px) saturate(1.5);
    -webkit-backdrop-filter: blur(12px) saturate(1.5);
    border: 1px solid oklch(82% 0.14 210 / 25%);
    color: var(--color-cyber-text);
    font-size: 12px;
    font-weight: 500;
    padding: 5px 10px;
    border-radius: 8px;
    white-space: nowrap;
    pointer-events: none;
    z-index: 60;
    box-shadow: 0 8px 24px oklch(0% 0 0 / 50%);
    animation: micTooltipIn 0.15s ease-out;
  }
  .mic-tooltip::after {
    content: '';
    position: absolute;
    right: 100%;
    top: 50%;
    transform: translateY(-50%);
    border: 5px solid transparent;
    border-right-color: oklch(82% 0.14 210 / 25%);
  }
  /* On the expanded chat panel the mic button sits flush against the right
   * edge of the input row, so a right-side tooltip would overflow the panel.
   * Flip it to the LEFT there. */
  .mic-tooltip-left {
    left: auto;
    right: calc(100% + 10px);
  }
  .mic-tooltip-left::after {
    right: auto;
    left: 100%;
    border-right-color: transparent;
    border-left-color: oklch(82% 0.14 210 / 25%);
  }
  @keyframes micTooltipIn {
    from { opacity: 0; transform: translateY(-50%) translateX(-4px); }
    to { opacity: 1; transform: translateY(-50%) translateX(0); }
  }

  .chat-orb:focus-visible,
  .chat-orb-expand:focus-visible,
  .chat-orb-add:focus-visible {
    outline: 2px solid var(--color-cyber-cyan);
    outline-offset: 2px;
    border-radius: inherit;
  }

  .chat-inline-messages {
    width: 100%;
    max-width: 42rem;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overflow-anchor: none;
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

  /* Close button — only shown in the mobile bottom-sheet toolbar. */
  .chat-inline-close-btn {
    display: none;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 8px;
    color: var(--color-cyber-text-dim);
    background: transparent;
    transition: background 0.2s, color 0.2s;
  }

  .chat-inline-close-btn:hover {
    background: rgba(255, 120, 120, 0.12);
    color: var(--color-cyber-text);
  }

  /* Drag handle — only shown at the top of the mobile bottom-sheet. */
  .chat-sheet-handle {
    display: none;
  }

  .chat-inline-messages {
    border-radius: 0 0 24px 24px;
    border-top: 0;
  }

  .chat-inline-input-row {
    width: 36rem;
    max-width: 100%;
  }

  .chat-empty-state {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 2rem 1rem;
    text-align: center;
  }

  .chat-empty-state-icon {
    color: rgba(0, 212, 255, 0.35);
    margin-bottom: 4px;
  }

  .chat-empty-state-icon svg {
    width: 28px;
    height: 28px;
  }

  .chat-empty-state-title {
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--color-cyber-text-dim);
  }

  .chat-empty-state-hint {
    font-size: 12px;
    color: rgba(145, 163, 184, 0.55);
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
      top: auto;
      bottom: calc(64px + env(safe-area-inset-bottom, 0px));
      transform: none;
      width: auto;
      max-width: 100%;
    }

    .chat-inline-messages {
      max-height: 56dvh;
    }
  }

  /* Mobile: conversation panel opens as a swipeable bottom-sheet drawer,
     matching the HistoryPanel pattern. A backdrop dims the graph; the sheet
     anchors to the bottom with rounded top corners, a drag handle, and a
     dismiss-on-swipe-down gesture. */
  @media (max-width: 768px) {
    .chat-sheet-backdrop {
      position: fixed;
      inset: 0;
      z-index: 60;
      background: rgba(0, 0, 0, 0.55);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      animation: chat-backdrop-in 220ms ease-out;
      transition: opacity 280ms ease-out;
    }

    .chat-sheet-backdrop.backdrop-closing {
      opacity: 0;
    }

    .chat-inline-overlay {
      position: fixed;
      left: 0;
      right: 0;
      top: auto;
      bottom: 0;
      width: 100%;
      max-width: 100%;
      max-height: 92dvh;
      padding: 0;
      z-index: 61;
      flex-direction: column;
      background: var(--color-cyber-bg, #0a0e17);
      border-radius: 20px 20px 0 0;
      border-top: 1px solid var(--color-cyber-border, rgba(0, 212, 255, 0.15));
      box-shadow: 0 -12px 48px rgba(0, 0, 0, 0.7), 0 -2px 24px rgba(0, 212, 255, 0.06);
      animation: chat-sheet-in 320ms cubic-bezier(0.16, 1, 0.3, 1);
      transition: transform 320ms cubic-bezier(0.16, 1, 0.3, 1);
      will-change: transform;
      overflow: hidden;
    }

    .chat-inline-overlay.sheet-dragging {
      transition: none;
    }

    .chat-inline-overlay.sheet-closing {
      transform: translateY(100%);
    }

    .chat-sheet-handle {
      display: flex;
      justify-content: center;
      padding: 10px 0 6px;
      flex-shrink: 0;
    }

    .chat-sheet-handle-bar {
      width: 40px;
      height: 5px;
      border-radius: 3px;
      background: rgba(200, 214, 229, 0.25);
    }

    .chat-inline-header {
      max-width: 100%;
      padding: 4px 8px 10px;
      background: transparent;
      border: 0;
      border-radius: 0;
      flex-shrink: 0;
    }

    .chat-inline-header-title {
      flex: 1;
      text-align: center;
      font-size: 13px;
      font-weight: 600;
      text-transform: none;
      letter-spacing: 0;
      color: var(--color-cyber-text);
    }

    .chat-inline-close-btn {
      display: flex;
      width: 36px;
      height: 36px;
      border-radius: 10px;
    }

    .chat-inline-header .chat-history-btn {
      width: 36px;
      height: 36px;
      border-radius: 10px;
    }

    .chat-inline-messages {
      max-width: 100%;
      max-height: none;
      flex: 1;
      border-radius: 0;
      border: 0;
      background: transparent;
      padding: 4px 16px 8px;
      -webkit-overflow-scrolling: touch;
      overscroll-behavior-y: contain;
    }

    .chat-collapse-row {
      display: none;
    }

    .chat-inline-input-row {
      width: 100%;
      max-width: 100%;
      margin: 0;
      padding: 8px 12px calc(10px + env(safe-area-inset-bottom, 0px));
      background: var(--color-cyber-bg, #0a0e17);
      flex-shrink: 0;
      border-radius: 22px;
      min-height: 48px;
      height: auto;
    }

    .chat-inline-input-row > textarea {
      min-height: 40px;
      line-height: 1.5;
      padding: 10px 14px;
      font-size: 16px;
      -webkit-appearance: none;
      border-radius: 20px;
    }

    .chat-inline-input-row > button {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      touch-action: none;
      -webkit-user-select: none;
      user-select: none;
      -webkit-touch-callout: none;
    }

    .chat-inline-input-row > textarea {
      touch-action: auto;
    }

    .chat-orb-add,
    .chat-orb-expand {
      padding: 12px 20px 12px 12px;
      font-size: 15px;
      gap: 12px;
      margin-bottom: 12px;
    }

    .chat-orb-add .coa-icon,
    .chat-orb-expand .coe-icon {
      width: 40px;
      height: 40px;
    }

    .chat-orb-add .coa-icon svg,
    .chat-orb-expand .coe-icon svg {
      width: 20px;
      height: 20px;
    }
  }

  @keyframes chat-backdrop-in { from { opacity: 0; } to { opacity: 1; } }
  @keyframes chat-sheet-in { from { transform: translateY(100%); } to { transform: translateY(0); } }

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