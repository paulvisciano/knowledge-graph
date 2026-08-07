<script lang="ts">
  import type { ChatMessage, MCPToolCall } from '$lib/constants';
  import { API } from '$lib/constants';
  import { tick, onMount } from 'svelte';
  import { graphStore } from '$lib/stores/graph.svelte';
  import { activeTab, selectedNodeId, navDrawerOpen, historyPanelOpen } from '$lib/stores/ui';
  import { mcpClient } from '$lib/services/mcp-client.svelte';
  import { connectionStore } from '$lib/stores/connection.svelte';
  import { configStore } from '$lib/stores/config.svelte';
  import { conversationStore } from '$lib/stores/conversation.svelte';

  import Icon from '$lib/components/ui/Icon.svelte';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import { AudioRecorder, blobToBase64, isAudioRecordingSupported, transcribeAudio } from '$lib/utils/audio-recording';
  import ImageGallery from '$lib/components/ui/ImageGallery.svelte';
  import AudioPlayer from '$lib/components/ui/AudioPlayer.svelte';

  import AttachmentMenu from '$lib/components/ui/AttachmentMenu.svelte';
  import AttachmentPreview from '$lib/components/ui/AttachmentPreview.svelte';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient, type JobInfo } from '$lib/services/kg-api-client';
  import { syncClient } from '$lib/services/sync-client.svelte';
  import { sseClient, type LlmEvent } from '$lib/services/sse-client.svelte';
  import { fileToAttachment, revokeAttachmentUrls, isImageType, MAX_ATTACHMENTS, MAX_FILE_SIZE, type Attachment } from '$lib/utils/file-utils';
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
      const nodes = graphStore.nodes.filter((n: any) =>
        n.labels?.some((l: string) => l === 'Photo' || l === 'Image')
      );
      return nodes;
    };
    (window as any).__selectNode = (id: string) => {
      return (window as any).__getPhotoNodes?.().find((n: any) => n.id === id);
    };
  }

  onMount(() => {
    resumeInProgressJobs();

    // ── SSE connection for real-time LLM events ──
    sseClient.connect();

    // On new conversation created on the server (e.g. by another client)
    sseClient.on('new_conversation', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; id?: string; name?: string; title?: string; created_at?: number };
      const convId = data?.conv_id ?? data?.id ?? event.convId;
      if (!convId) return;
      if (conversations.find((c) => c.id === convId)) return;
      const conv: Conversation = {
        id: convId,
        title: data?.name ?? data?.title ?? '',
        messages: [],
        createdAt: data?.created_at ?? Date.now(),
        updatedAt: data?.created_at ?? Date.now(),
      };
      conversations = [conv, ...conversations];
      graphStore.upsertNode(conv.id, ['Conversation'], { entity_type: 'Conversation', name: conv.title || conv.id });
    });

    // On new message in a conversation (user or assistant)
    sseClient.on('new_message', (event: LlmEvent) => {
      const data = event.data as { id?: string; conv_id?: string; role?: string; content?: string; timestamp?: number; is_streaming?: boolean; message_id?: string };
      const convId = data?.conv_id ?? event.convId;
      if (!convId) return;

      const msgId = data?.message_id ?? data?.id ?? '';
      const msg: ChatMessage = {
        id: msgId,
        role: (data?.role ?? 'user') as 'user' | 'assistant' | 'system',
        content: data?.content ?? '',
        timestamp: data?.timestamp ?? Date.now(),
        isStreaming: data?.is_streaming ?? false,
      };

      // If we don't have this conversation locally, create it
      let conv = conversations.find((c) => c.id === convId);
      if (!conv) {
        conv = {
          id: convId,
          title: data?.content ? data.content.slice(0, 50) + (data.content.length > 50 ? '…' : '') : '',
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        conversations = [conv, ...conversations];
        graphStore.upsertNode(conv.id, ['Conversation'], { entity_type: 'Conversation', name: conv.title || conv.id });
      }

      // Don't duplicate if we already have this message by ID
      if (conv.messages.find((m) => m.id === msgId)) return;
      if (convId === activeConversationId && messages.find((m) => m.id === msgId)) return;

      // Content-based dedup: if the server echoes back a user message we already
      // added optimistically (same role, content, and timestamp within 5 s), update
      // the existing message's ID to the server-assigned one instead of adding a duplicate.
      const existing = (convId === activeConversationId ? messages : conv.messages)
        .find((m) =>
          m.role === msg.role &&
          m.content === msg.content &&
          Math.abs(m.timestamp - msg.timestamp) < 5000
        );
      if (existing) {
        existing.id = msgId;
        if (convId === activeConversationId) {
          messages = [...messages];
        } else {
          conv.messages = [...conv.messages];
        }
        return;
      }

      if (convId === activeConversationId) {
        messages = [...messages, msg];
      } else {
        conv.messages = [...conv.messages, msg];
        unreadConversations = new Set([...unreadConversations, convId]);
      }
      conv.updatedAt = Date.now();
      requestAnimationFrame(scrollToBottom);
    });

    // On stream start — create a placeholder assistant message for token appending
    sseClient.on('start', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; model?: string; message_id?: string };
      const convId = data?.conv_id ?? event.convId;
      const msgId = data?.message_id;
      if (!convId || !msgId) return;

      sseStreamingConvId = convId;
      sseStreamingMsgId = msgId;
      streamingConvIds = new Set([...streamingConvIds, convId]);
      graphStore.setStreamingConversations(streamingConvIds);
      isStreaming = true;
      isPending = false;
      if (convId === activeConversationId) {
        processingLabel = 'Thinking...';
      }

      // Create a placeholder assistant message for this conversation
      const placeholder: ChatMessage = {
        id: msgId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        isStreaming: true,
      };

      const conv = conversations.find((c) => c.id === convId);
      if (convId === activeConversationId) {
        if (!messages.find((m) => m.id === msgId)) {
          messages = [...messages, placeholder];
        }
      } else if (conv) {
        if (!conv.messages.find((m) => m.id === msgId)) {
          conv.messages = [...conv.messages, placeholder];
          unreadConversations = new Set([...unreadConversations, convId]);
        }
      }
      requestAnimationFrame(scrollToBottom);
    });

    // On token from a streaming assistant response
    sseClient.on('token', (event: LlmEvent) => {
      const data = event.data as { token: string; conv_id?: string; job_id?: string; message_id?: string; thinking?: boolean };
      const convId = data.conv_id ?? event.convId ?? sseStreamingConvId;
      const msgId = data.message_id ?? sseStreamingMsgId;
      if (!convId || !msgId) return;

      // Track which conversation/message we're streaming into
      if (sseStreamingConvId !== convId) {
        sseStreamingConvId = convId;
      }
      if (sseStreamingMsgId !== msgId) {
        sseStreamingMsgId = msgId;
      }

      isStreaming = true;
      isPending = false;
      if (convId === activeConversationId) {
        processingLabel = 'Streaming...';
      }

      const isActive = convId === activeConversationId;
      const targetMessages = isActive ? messages : conversations.find((c) => c.id === convId)?.messages;

      // If we don't have the streaming message yet (e.g. reconnect mid-stream),
      // create a placeholder so we can append tokens
      if (targetMessages && !targetMessages.find((m) => m.id === msgId)) {
        const placeholder: ChatMessage = {
          id: msgId,
          role: 'assistant',
          content: '',
          timestamp: Date.now(),
          isStreaming: true,
        };
        if (isActive) {
          messages = [...messages, placeholder];
        } else {
          const conv = conversations.find((c) => c.id === convId);
          if (conv) {
            conv.messages = [...conv.messages, placeholder];
          }
        }
      }

      if (data.thinking) {
        // Append to thinking content
        const updateThinking = (m: ChatMessage): ChatMessage => {
          const existing = m.thinkingContent ?? '';
          return { ...m, thinkingContent: existing + data.token };
        };
        if (isActive) {
          messages = messages.map((m) => m.id === msgId ? updateThinking(m) : m);
        } else {
          const conv = conversations.find((c) => c.id === convId);
          if (conv) {
            conv.messages = conv.messages.map((m) => m.id === msgId ? updateThinking(m) : m);
          }
        }
      } else {
        const appendToken = (m: ChatMessage): ChatMessage => {
          const existing = m.content ?? '';
          const newContent = existing + data.token;
          return { ...m, content: newContent };
        };
        if (isActive) {
          messages = messages.map((m) => m.id === msgId ? appendToken(m) : m);
        } else {
          const conv = conversations.find((c) => c.id === convId);
          if (conv) {
            conv.messages = conv.messages.map((m) => m.id === msgId ? appendToken(m) : m);
          }
        }
      }

      // Update tokens/second indicator
      if (isActive && sseStreamingMsgId) {
        const msg = messages.find((m) => m.id === sseStreamingMsgId);
        if (msg?.timings?.prompt_tokens != null && msg?.timings?.completion_tokens != null) {
          const elapsed = (Date.now() - msg.timestamp) / 1000;
          if (elapsed > 0.5) {
            tokensPerSecond = Math.round((msg.timings.completion_tokens ?? 0) / elapsed);
          }
        }
      }

      requestAnimationFrame(scrollToBottom);
    });

    // On model info from a streaming response
    sseClient.on('model_info', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; model?: string; message_id?: string };
      const convId = data?.conv_id ?? event.convId;
      const msgId = data?.message_id ?? sseStreamingMsgId;
      if (!convId || !msgId) return;

      const updateModel = (m: ChatMessage): ChatMessage => ({ ...m, model: data.model });
      if (convId === activeConversationId) {
        messages = messages.map((m) => m.id === msgId ? updateModel(m) : m);
      } else {
        const conv = conversations.find((c) => c.id === convId);
        if (conv) {
          conv.messages = conv.messages.map((m) => m.id === msgId ? updateModel(m) : m);
        }
      }
    });

    // On timings info from a completed response
    sseClient.on('timings', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; time_to_first_token_ms?: number; message_id?: string };
      const convId = data?.conv_id ?? event.convId;
      const msgId = data?.message_id ?? sseStreamingMsgId;
      if (!convId || !msgId) return;

      const updateTimings = (m: ChatMessage): ChatMessage => ({
        ...m,
        timings: {
          prompt_tokens: data.prompt_tokens ?? m.timings?.prompt_tokens,
          completion_tokens: data.completion_tokens ?? m.timings?.completion_tokens,
          total_tokens: data.total_tokens ?? m.timings?.total_tokens,
          time_to_first_token_ms: data.time_to_first_token_ms ?? m.timings?.time_to_first_token_ms,
        },
      });
      if (convId === activeConversationId) {
        messages = messages.map((m) => m.id === msgId ? updateTimings(m) : m);
      } else {
        const conv = conversations.find((c) => c.id === convId);
        if (conv) {
          conv.messages = conv.messages.map((m) => m.id === msgId ? updateTimings(m) : m);
        }
      }
    });

    // On tool_calls from a streaming response
    sseClient.on('tool_calls', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; job_id?: string; message_id?: string; tool_calls?: { id: string; name: string; arguments: string }[] };
      const convId = data?.conv_id ?? event.convId;
      const msgId = data?.message_id ?? sseStreamingMsgId;
      if (!convId || !msgId || !data?.tool_calls) return;

      const toolCalls: MCPToolCall[] = data.tool_calls.map((tc) => ({
        id: tc.id,
        toolName: tc.name,
        arguments: JSON.parse(tc.arguments || '{}'),
        timestamp: Date.now(),
      }));

      const updateMsg = (m: ChatMessage): ChatMessage => {
        const existing = m.mcpToolCalls ?? [];
        return { ...m, mcpToolCalls: [...existing, ...toolCalls] };
      };

      if (convId === activeConversationId) {
        const idx = messages.findIndex((m) => m.id === msgId);
        if (idx !== -1) messages = messages.map((m) => m.id === msgId ? updateMsg(m) : m);
      } else {
        const conv = conversations.find((c) => c.id === convId);
        if (conv) {
          const idx = conv.messages.findIndex((m) => m.id === msgId);
          if (idx !== -1) conv.messages = conv.messages.map((m) => m.id === msgId ? updateMsg(m) : m);
        }
      }
      requestAnimationFrame(scrollToBottom);
    });

    // On tool_call_start — tool execution has begun
    sseClient.on('tool_call_start', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; job_id?: string; message_id?: string; tool_name?: string; tool_call_id?: string };
      const convId = data?.conv_id ?? event.convId;
      const msgId = data?.message_id ?? sseStreamingMsgId;
      if (!convId || !msgId) return;

      processingLabel = data?.tool_name === 'save_to_knowledge_graph' ? 'Saving to knowledge graph...' : 'Searching knowledge graph...';

      const updateMsg = (m: ChatMessage): ChatMessage => {
        const existing = m.mcpToolCalls ?? [];
        const alreadyHas = existing.find((tc) => tc.id === data?.tool_call_id);
        if (alreadyHas) return m;
        return {
          ...m,
          mcpToolCalls: [...existing, { id: data?.tool_call_id, toolName: data?.tool_name ?? '', arguments: {}, timestamp: Date.now() }],
        };
      };

      if (convId === activeConversationId) {
        const idx = messages.findIndex((m) => m.id === msgId);
        if (idx !== -1) messages = messages.map((m) => m.id === msgId ? updateMsg(m) : m);
      } else {
        const conv = conversations.find((c) => c.id === convId);
        if (conv) {
          const idx = conv.messages.findIndex((m) => m.id === msgId);
          if (idx !== -1) conv.messages = conv.messages.map((m) => m.id === msgId ? updateMsg(m) : m);
        }
      }
    });

    // On tool_call_result — tool execution finished
    sseClient.on('tool_call_result', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; job_id?: string; message_id?: string; tool_name?: string; tool_call_id?: string; result?: string; is_error?: boolean };
      const convId = data?.conv_id ?? event.convId;
      const msgId = data?.message_id ?? sseStreamingMsgId;
      if (!convId || !msgId) return;

      const updateMsg = (m: ChatMessage): ChatMessage => {
        const existing = m.mcpToolCalls ?? [];
        const tcIdx = existing.findIndex((tc) => tc.id === data?.tool_call_id);
        if (tcIdx === -1) return m;
        const updated = [...existing];
        updated[tcIdx] = { ...updated[tcIdx], result: data?.result, isError: data?.is_error };
        return { ...m, mcpToolCalls: updated };
      };

      if (convId === activeConversationId) {
        messages = messages.map((m) => m.id === msgId ? updateMsg(m) : m);
      } else {
        const conv = conversations.find((c) => c.id === convId);
        if (conv) conv.messages = conv.messages.map((m) => m.id === msgId ? updateMsg(m) : m);
      }
    });

    // On error from the server
    sseClient.on('error', (event: LlmEvent) => {
      const data = event.data as { message?: string; conv_id?: string };
      const errorMessage = data?.message ?? (data as any)?.error ?? 'Unknown error';
      console.error('SSE error:', errorMessage);
      const convId = data?.conv_id ?? event.convId;
      if (convId) {
        streamingConvIds = new Set([...streamingConvIds].filter((id) => id !== convId));
        graphStore.setStreamingConversations(streamingConvIds);
      }
      isStreaming = false;
      isPending = false;
      isProcessing = false;
      processingLabel = '';

      // Show error in the streaming message if we have one
      if (convId && sseStreamingMsgId) {
        if (convId === activeConversationId) {
          messages = messages.map((m) =>
            m.id === sseStreamingMsgId
              ? { ...m, content: `**Error:** ${errorMessage}`, isStreaming: false }
              : m
          );
        } else {
          const conv = conversations.find((c) => c.id === convId);
          if (conv) {
            conv.messages = conv.messages.map((m) =>
              m.id === sseStreamingMsgId
                ? { ...m, content: `**Error:** ${errorMessage}`, isStreaming: false }
                : m
            );
          }
        }
        sseStreamingConvId = null;
        sseStreamingMsgId = null;
      }
    });

    // On status_change from server — update streaming/pending state for conversations
    sseClient.on('status_change', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string; status?: string };
      const convId = data?.conv_id ?? event.convId;
      if (!convId) return;

      if (data?.status === 'streaming' || data?.status === 'pending') {
        streamingConvIds = new Set([...streamingConvIds, convId]);
      } else {
        streamingConvIds = new Set([...streamingConvIds].filter((id) => id !== convId));
        // Finalize the message
        if (convId === activeConversationId) {
          messages = messages.map((m) => m.isStreaming ? { ...m, isStreaming: false } : m);
        } else {
          const conv = conversations.find((c) => c.id === convId);
          if (conv) {
            conv.messages = conv.messages.map((m) => m.isStreaming ? { ...m, isStreaming: false } : m);
          }
        }
        isStreaming = false;
        isPending = false;
        isProcessing = false;
        processingLabel = '';
        tokensPerSecond = null;
      }
      graphStore.setStreamingConversations(streamingConvIds);
    });

    // On done — stream complete
    sseClient.on('done', (event: LlmEvent) => {
      const data = event.data as { conv_id?: string };
      const convId = data?.conv_id ?? event.convId;
      if (convId) {
        streamingConvIds = new Set([...streamingConvIds].filter((id) => id !== convId));
        graphStore.setStreamingConversations(streamingConvIds);
      }
      isStreaming = false;
      isPending = false;
      isProcessing = false;
      processingLabel = '';
      tokensPerSecond = null;

      if (convId === activeConversationId) {
        messages = messages.map((m) => m.isStreaming ? { ...m, isStreaming: false } : m);
      } else if (convId) {
        const conv = conversations.find((c) => c.id === convId);
        if (conv) {
          conv.messages = conv.messages.map((m) => m.isStreaming ? { ...m, isStreaming: false } : m);
        }
      }
      saveMessagesToConversation();
    });

    // Restore streaming/pending statuses from the server for refresh resilience
    const convIds = conversations.map((c) => c.id);
    if (convIds.length > 0) {
      sseClient.fetchStatuses(convIds).then((statuses) => {
        const streamingIds = new Set<string>();
        for (const s of statuses) {
          if (s.status === 'streaming' || s.status === 'pending') {
            streamingIds.add(s.conv_id);
          }
        }
        streamingConvIds = streamingIds;
        graphStore.setStreamingConversations(streamingConvIds);
      }).catch((err) => {
        console.warn('Failed to fetch conversation statuses on mount:', err);
      });
    }

    return () => {
      sseClient.disconnect();
    };
  });

  let activeConversationId = $state('');
  let messages = $state<ChatMessage[]>([]);
  let isStreaming = $state(false);
  let isPending = $state(false);
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
    !!activeConvForDivider
  );

  let thinkingContent = $state('');
  let isProcessing = $state(false);
  let processingLabel = $state('');
  let tokensPerSecond = $state<number | null>(null);
  let promptTokens = $state<number | null>(null);
  let attachments = $state<Attachment[]>([]);
  let attachError = $state('');
  let imageFileInput: HTMLInputElement | undefined = $state();
  let docFileInput: HTMLInputElement | undefined = $state();

  // Click-vs-pan discrimination: record pointer-down position; treat the
  // pointerup as a click only if it stayed within a small radius.
  let pointerDownXY: { x: number; y: number } | null = null;
  const CLICK_MAX_DRIFT = 6;

  // Tracks which conversation the local SSE token handler is currently
  // appending tokens to, so conversation switches during streaming don't
  // redirect writes to the wrong messages array.
  let sseStreamingConvId: string | null = null;
  let sseStreamingMsgId: string | null = null;

  // Set of conversation IDs that the SSE server reports as streaming/pending.
  let streamingConvIds = $state<Set<string>>(new Set());

  // Unread activity indicators
  let unreadConversations = $state<Set<string>>(new Set());

  // Whether the currently-viewed conversation is the one being streamed to.
  let isActiveConversationStreaming = $derived(
    streamingConvIds.has(activeConversationId)
  );

  // Whether any conversation (active or background) is currently streaming
  let isStreamActive = $derived(
    streamingConvIds.size > 0
  );

  function updateStreamMessage(
    assistantId: string,
    updater: (m: ChatMessage) => ChatMessage
  ) {
    if (!sseStreamingConvId) {
      messages = messages.map((m) => m.id === assistantId ? updater(m) : m);
      return;
    }

    const isActive = sseStreamingConvId === activeConversationId;

    if (isActive) {
      messages = messages.map((m) => m.id === assistantId ? updater(m) : m);
    } else {
      const conv = conversations.find((c) => c.id === sseStreamingConvId);
      if (conv) {
        conv.messages = conv.messages.map((m) => m.id === assistantId ? updater(m) : m);
        unreadConversations = new Set([...unreadConversations, sseStreamingConvId]);
      }
    }
  }

  function pushStreamMessage(msg: ChatMessage) {
    if (!sseStreamingConvId) {
      messages = [...messages, msg];
      return;
    }

    const isActive = sseStreamingConvId === activeConversationId;

    if (isActive) {
      messages = [...messages, msg];
    } else {
      const conv = conversations.find((c) => c.id === sseStreamingConvId);
      if (conv) {
        conv.messages = [...conv.messages, msg];
        unreadConversations = new Set([...unreadConversations, sseStreamingConvId]);
      }
    }
  }

  let audioRecorder = $state<AudioRecorder | null>(null);
  let isRecording = $state(false);
  let isTranscribing = $state(false);
  let recordingSupported = $state(false);
  let micBusy = $state(false);
  const HOLD_TO_RECORD_MS = 3000;
  const HOLD_TICK_MS = 50;
  const HOLD_START_DELAY_MS = 300;
  let holdActive = $state(false);
  let holdElapsed = $state(0);
  let holdCountdown = $derived(
    Math.max(1, Math.ceil((HOLD_TO_RECORD_MS - holdElapsed) / 1000))
  );
  let holdTimer: ReturnType<typeof setInterval> | null = null;
  let holdStartDelay: ReturnType<typeof setTimeout> | null = null;
  let orbOptionsOpen = $state(false);
  let micTooltipVisible = $state(false);
  let micTooltipMessage = $derived(
    isTranscribing
      ? 'Transcribing…'
      : isRecording
        ? 'Release to send'
        : 'Hold to record'
  );

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
      if (isTranscribing || micBusy) return;
      e.preventDefault();
      handleMicTap();
    }
    window.addEventListener('keydown', onSpacePress);
    return () => window.removeEventListener('keydown', onSpacePress);
  });

  // ── Push-to-talk ──
  let holdStartedByTouch = false;
  let suppressPointerTap = false;

  async function handleMicPointerDown(e: PointerEvent) {
    if (!audioRecorder || !recordingSupported) return;
    if (isTranscribing || micBusy) return;
    if (holdStartedByTouch) return;
    if (holdStartDelay || holdActive) return;
    startHoldTimer();
  }

  function handleMicTouchStart(e: TouchEvent) {
    if (!audioRecorder || !recordingSupported) return;
    if (isTranscribing || micBusy) return;
    holdStartedByTouch = true;
    if (holdStartDelay || holdActive) return;
    startHoldTimer();
  }

  function handleMicTouchMove(e: TouchEvent) {
    if (holdActive) e.preventDefault();
  }

  function handleMicTouchEnd(e: TouchEvent) {
    if (!holdStartedByTouch) return;
    holdStartedByTouch = false;
    suppressPointerTap = true;
    finishHold();
  }

  function startHoldTimer() {
    holdElapsed = 0;
    holdStartDelay = setTimeout(() => {
      holdStartDelay = null;
      holdActive = true;
      startRecording();
      holdTimer = setInterval(() => {
        holdElapsed += HOLD_TICK_MS;
        if (holdElapsed >= HOLD_TO_RECORD_MS) {
          finishHold();
        }
      }, HOLD_TICK_MS);
    }, HOLD_START_DELAY_MS);
  }

  async function startRecording() {
    if (!audioRecorder) return;
    try {
      await audioRecorder.startRecording();
      isRecording = true;
    } catch (err) {
      console.error('Failed to start recording:', err);
      holdActive = false;
      holdElapsed = 0;
      micBusy = false;
    }
  }

  async function handleMicTap() {
    if (isTranscribing || micBusy) return;
    if (isStreamActive && !isActiveConversationStreaming) {
      openStreamingConversation();
      return;
    }
    if (isActiveConversationStreaming) {
      cancelStreaming();
      return;
    }
    orbOptionsOpen = false;
    startNewConversation();
    chatExpanded = true;
    await tick();
    requestAnimationFrame(() => panelTextareaEl?.focus());
  }

  async function stopAndSendRecording() {
    const fromOrb = !chatExpanded;
    isRecording = false;
    micBusy = true;
    isTranscribing = true;
    audioRecorder?.stopRecording()
      .then(async (wavBlob) => {
        const audioUrl = URL.createObjectURL(wavBlob);
        const audioData = await blobToBase64(wavBlob);
        const transcript = await transcribeAudio(wavBlob, API.llama.transcriptions);
        isTranscribing = false;
        micBusy = false;
        const text = transcript.trim();
        if (text) void handleSend(audioUrl, audioData, 'wav', fromOrb, text, fromOrb);
      })
      .catch((err) => {
        console.error('Push-to-talk send failed:', err);
        isTranscribing = false;
        micBusy = false;
      });
  }

  function finishHold() {
    const wasHolding = holdActive;
    holdActive = false;
    holdElapsed = 0;
    if (holdTimer) { clearInterval(holdTimer); holdTimer = null; }
    if (holdStartDelay) { clearTimeout(holdStartDelay); holdStartDelay = null; }

    if (!wasHolding) {
      if (isRecording) {
        stopAndSendRecording();
      } else if (isStreamActive) {
        openStreamingConversation();
      } else {
        orbOptionsOpen = !orbOptionsOpen;
      }
    }
  }

  async function handleMicPointerUp() {
    if (holdStartedByTouch || suppressPointerTap) return;
    finishHold();
  }

  function handleMicPointerLeave() {
    if (!holdActive && !holdStartDelay) return;
    if (holdStartedByTouch) return;
    holdActive = false;
    holdElapsed = 0;
    if (holdTimer) { clearInterval(holdTimer); holdTimer = null; }
    if (holdStartDelay) { clearTimeout(holdStartDelay); holdStartDelay = null; }
  }

  function handleMicPointerCancel() {
    if (holdStartedByTouch) return;
    holdActive = false;
    holdElapsed = 0;
    if (holdTimer) { clearInterval(holdTimer); holdTimer = null; }
    if (holdStartDelay) { clearTimeout(holdStartDelay); holdStartDelay = null; }
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
    const results = await Promise.allSettled(files.map((file) => fileToAttachment(file)));
    const newAttachments: Attachment[] = [];
    for (const result of results) {
      if (result.status === 'fulfilled') {
        newAttachments.push(result.value);
      } else {
        attachError = result.reason instanceof Error ? result.reason.message : 'Failed to attach file';
      }
    }
    if (newAttachments.length > 0) {
      attachments = [...attachments, ...newAttachments];
    }
  }

  function openDocumentPicker() {
    docFileInput?.click();
  }

  function removeAttachment(id: string) {
    const att = attachments.find((a) => a.id === id);
    if (att) {
      if (att.thumbnailUrl) URL.revokeObjectURL(att.thumbnailUrl);
      if (att.dataUrl) URL.revokeObjectURL(att.dataUrl);
    }
    attachments = attachments.filter((a) => a.id !== id);
    attachError = '';
  }

  /** Process image files picked from the graph page directly through the KG
   *  EXIF pipeline. Does NOT add them to chat attachments, does NOT send them
   *  to the LLM. Uploads all jobs in parallel, then connects SSE with
   *  capped concurrency to avoid exhausting the browser connection pool. */
  async function handleGraphImageFiles(fileList: FileList | null) {
    if (!fileList) return;
    const files = Array.from(fileList);
    const atts: Attachment[] = [];
    for (const file of files) {
      if (file.size > MAX_FILE_SIZE) {
        console.warn(`${file.name} exceeds ${MAX_FILE_SIZE / 1024 / 1024}MB limit`);
        continue;
      }
      const mimeType = file.type || 'image/jpeg';
      if (!isImageType(mimeType)) continue;
      atts.push({
        id: crypto.randomUUID(),
        file,
        mimeType,
        dataUrl: '',
        thumbnailUrl: URL.createObjectURL(file),
        name: file.name,
        size: file.size,
      });
    }
    await submitImageBatch(atts);
    if (imageFileInput) imageFileInput.value = '';
  }

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
      graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', name: att.name, source: 'upload' });
      toTrack.push({ jobId: job.job_id, photoNodeId, att });
    }
    // Connect SSE streams with capped concurrency
    let nextIdx = 0;
    async function runNext() {
      while (nextIdx < toTrack.length) {
        const { jobId, photoNodeId, att } = toTrack[nextIdx++];
        await processSingleImage(att, '', jobId);
      }
    }
    const workers = Array.from({ length: Math.min(MAX_CONCURRENT_SSE, toTrack.length) }, () => runNext());
    await Promise.all(workers);
  }

  async function processSingleImage(att: Attachment, note: string, existingJobId?: string) {
    const photoNodeId = `${att.name} (Photo)`;
    let jobId = existingJobId;
    if (!jobId) {
      const job = await kgApiClient.createJob(att.file, { insert: true, note });
      jobId = job.job_id;
      if (!imageProcessingStore.getByJobId(jobId)) {
        imageProcessingStore.startProcessing(photoNodeId, att.name, att.thumbnailUrl ?? att.dataUrl ?? '', jobId);
        graphStore.upsertNode(photoNodeId, ['Photo'], { entity_type: 'Photo', name: att.name, source: 'upload' });
      }
    }

    const MAX_RECONNECTS = 3;
    const MAX_RECONNECT_DELAY_MS = 10_000;
    let reconnectDelayMs = 1000;
    let streamEnded = false;

    const finish = () => {
      imageProcessingStore.updateStage(photoNodeId, 'complete');
    };

    const pollStatus = async () => {
      try {
        const status = await kgApiClient.getJobStatus(jobId!);
        if (status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled') {
          return true;
        }
      } catch {}
      return false;
    };

    for (let attempt = 0; attempt <= MAX_RECONNECTS; attempt++) {
      streamEnded = false;
      try {
        await sseClient.streamJobEvents(jobId!, (event) => {
          const eventName = event.event;
          const eventData = event.data as Record<string, unknown>;

          if (eventName === 'progress') {
            const stage = String(eventData.stage ?? '');
            if (stage) {
              imageProcessingStore.updateStage(photoNodeId, stage);
            }
            const progress = Number(eventData.progress ?? 0);
            imageProcessingStore.updateProgress(photoNodeId, progress);
          } else if (eventName === 'exif_complete') {
            imageProcessingStore.updateStage(photoNodeId, 'queued_for_ai');
          } else if (eventName === 'node_created' || eventName === 'edge_created') {
            const sourceId = String(eventData.source ?? eventData.entity_name ?? '');
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
        });
        streamEnded = true;
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          finish();
          return;
        }
        streamEnded = true;
      }

      if (streamEnded) {
        const terminal = await pollStatus();
        if (terminal) { finish(); return; }
        if (attempt < MAX_RECONNECTS) {
          await new Promise((r) => setTimeout(r, reconnectDelayMs));
          reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
        }
      }
    }
    for (let i = 0; i < 3; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      if (!(jobId in imageProcessingStore.statuses)) { finish(); return; }
      if (await pollStatus()) { finish(); return; }
    }
    finish();
  }

  async function processImageAttachments(atts: Attachment[], note: string = '') {
    const imageFiles = atts.filter((a) => isImageType(a.mimeType) && a.file);
    for (const att of imageFiles) {
      processSingleImage(att, note);
    }
  }

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
      }
      for (const job of processing) {
        const photoNodeId = `${job.file_source} (Photo)`;
        const existing = imageProcessingStore.getByJobId(job.job_id);
        if (!existing) {
          const stage = job.stage === 'exif_complete' ? 'queued_for_ai' : 'extracting_exif';
          imageProcessingStore.startProcessing(photoNodeId, job.file_source, '', job.job_id);
          if (stage === 'queued_for_ai') imageProcessingStore.updateStage(photoNodeId, 'queued_for_ai');
        }
      }
      const allJobs = [...pending, ...processing];
      pollJobBatch(allJobs);
    } catch (err) {
      console.warn('Failed to resume in-progress jobs:', err);
    }
  }

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
      if (liveJobs.length === 0) break;

      for (const job of liveJobs) {
        if (activeSSE.has(job.job_id)) continue;
        if (activeSSE.size >= MAX_CONCURRENT_SSE) break;
        const photoNodeId = `${job.file_source} (Photo)`;
        activeSSE.add(job.job_id);
        processSingleImage({ id: '', file: undefined as any, mimeType: '', dataUrl: '', name: job.file_source, size: 0 }, '', job.job_id)
          .finally(() => { activeSSE.delete(job.job_id); });
      }
    }
  }

  function startNewConversation() {
    const id = crypto.randomUUID();
    const conv: Conversation = {
      id,
      title: '',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    conversations = [conv, ...conversations];
    activeConversationId = id;
    messages = [];
    graphStore.upsertNode(id, ['Conversation'], { entity_type: 'Conversation', name: 'New conversation' });
    syncClient.createConversation(id);
  }

  function deleteMessage(msgId: string) {
    messages = messages.filter((m) => m.id !== msgId);
  }

  async function resendMessage(msgId: string) {
    const msg = messages.find((m) => m.id === msgId);
    if (!msg || msg.role !== 'user') return;
    messages = messages.filter((m) => m.id !== msgId);
    chatInput = msg.content;
    handleSend(undefined, undefined, undefined, false);
  }

  function cancelStreaming() {
    if (sseStreamingConvId) {
      sseClient.send({ event: 'cancel', data: { conv_id: sseStreamingConvId } });
    }
    isStreaming = false;
    isPending = false;
    isProcessing = false;
    processingLabel = '';
    tokensPerSecond = null;

    if (sseStreamingConvId === activeConversationId) {
      messages = messages.map((m) => m.isStreaming ? { ...m, isStreaming: false } : m);
    } else if (sseStreamingConvId) {
      const conv = conversations.find((c) => c.id === sseStreamingConvId);
      if (conv) {
        conv.messages = conv.messages.map((m) => m.isStreaming ? { ...m, isStreaming: false } : m);
      }
    }

    streamingConvIds = new Set([...streamingConvIds].filter((id) => id !== sseStreamingConvId!));
    graphStore.setStreamingConversations(streamingConvIds);
    sseStreamingConvId = null;
    sseStreamingMsgId = null;
    saveMessagesToConversation();
  }

  function switchConversation(id: string) {
    saveMessagesToConversation();
    activeConversationId = id;
    const conv = conversations.find((c) => c.id === id);
    if (conv) {
      if (conv.messages.length === 0) {
        const loaded = syncClient.loadConversation(id);
        // syncClient.loadConversation is now async in Svelte 5 — handle promise
        Promise.resolve(loaded).then((msgs: ChatMessage[]) => {
          if (msgs.length > 0) {
            conv.messages = msgs;
            messages = [...msgs];
          } else {
            messages = [...conv.messages];
          }
        });
      } else {
        messages = [...conv.messages];
        if (!syncClient.getCachedMessages(id)) {
          syncClient.loadConversation(id);
        }
      }
      if (!syncClient.getCachedMessages(id) && conv.messages.length > 0) {
        syncClient.seedCachedMessages(id, conv.messages);
      }
      graphStore.loadConversations();
    } else {
      messages = [];
    }

    if (unreadConversations.has(id)) {
      unreadConversations = new Set([...unreadConversations].filter((cid) => cid !== id));
    }

    chatExpanded = true;
  }

  function openStreamingConversation() {
    const id = [...streamingConvIds][0];
    if (!id) return;
    if (id === activeConversationId) {
      chatExpanded = true;
      return;
    }
    switchConversation(id);
  }

  function saveMessagesToConversation({ optimisticOnly = false }: { optimisticOnly?: boolean } = {}) {
    const conv = conversations.find((c) => c.id === activeConversationId);
    if (conv) {
      conv.messages = [...messages];
      conv.updatedAt = Date.now();
      syncClient.saveConversation(conv, { optimisticOnly });
    }
  }

  async function handleSend(audioUrl?: string, audioData?: string, audioFormat?: 'wav' | 'mp3', startNew = false, transcript?: string, fromOrb = false) {
    const messageText = transcript ?? chatInput.trim();
    const trimmed = messageText.trim();
    if ((!trimmed && attachments.length === 0) && !audioData) return;

    const wasChatExpanded = chatExpanded;
    if (!fromOrb) chatExpanded = true;
    isProcessing = true;
    isPending = true;
    processingLabel = 'Sending...';

    if (startNew || !wasChatExpanded || !activeConversationId) {
      startNewConversation();
    }

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
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

    saveMessagesToConversation({ optimisticOnly: true });

    const sentAttachments = [...attachments];
    chatInput = '';
    revokeAttachmentUrls(attachments);
    attachments = [];
    attachError = '';
    requestAnimationFrame(() => {
      if (textareaEl) textareaEl.style.height = 'auto';
      scrollToBottom();
    });

    const convId = activeConversationId;
    try {
      const res = await fetch(API.chat.messages(convId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: trimmed,
          attachments: sentAttachments.map((a) => ({
            name: a.name,
            mimeType: a.mimeType,
            data: a.dataUrl?.split(',')[1] ?? '',
          })),
          ...(audioData ? { audio: { data: audioData, format: audioFormat ?? 'wav' } } : {}),
          ...(selectedModel ? { model: selectedModel } : {}),
        }),
      });

      if (!res.ok) {
        const errText = await res.text();
        console.error('Chat send failed:', res.status, errText);
        isProcessing = false;
        isPending = false;
        processingLabel = '';
        return;
      }

      // If image attachments were included, kick off the KG processing pipeline
      if (sentAttachments.length > 0) {
        processImageAttachments(sentAttachments);
      }
    } catch (err) {
      console.error('Chat send error:', err);
      isProcessing = false;
      isPending = false;
      processingLabel = '';
    }
  }

  function deleteConversation(id: string) {
    conversations = conversations.filter((c) => c.id !== id);
    if (id === activeConversationId) {
      activeConversationId = '';
      messages = [];
      chatExpanded = false;
    }
    syncClient.deleteConversation(id);
  }

  function exportConversationToJsonl(id: string) {
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
    if (!trimmed && attachments.length === 0) return;
    chatInput = trimmed;
    panelChatInput = '';
    requestAnimationFrame(() => {
      if (panelTextareaEl) panelTextareaEl.style.height = 'auto';
    });
    handleSend(undefined, undefined, undefined, false);
  }

  // ── Expose methods for parent via bind:this ──
  export function handleQueryAbout(node: { id: string; labels?: string[]; properties?: Record<string, unknown> }) {
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

  export function handleSelectConversation(id: string) {
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

  export function closeChat() {
    if ($isMobile) {
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

  const markdownCache = new Map<string, string>();
  const MAX_MARKDOWN_CACHE = 200;

  function renderMarkdown(text: string): string {
    if (!text) return '';
    const cached = markdownCache.get(text);
    if (cached !== undefined) return cached;

    const html = marked.parse(text, { async: false }) as string;
    const result = DOMPurify.sanitize(html);

    if (markdownCache.size >= MAX_MARKDOWN_CACHE) {
      const firstKey = markdownCache.keys().next().value;
      if (firstKey !== undefined) markdownCache.delete(firstKey);
    }
    markdownCache.set(text, result);
    return result;
  }

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
            } else {
              messages = [...conv.messages];
            }
          });
        } else {
          messages = [...conv.messages];
        }
      }
      syncClient.startPeriodicSync(30_000, (updated) => {
        for (const synced of updated) {
          const existing = conversations.find((c) => c.id === synced.id);
          if (existing && existing.messages.length > 0) {
            synced.messages = existing.messages;
          } else {
            const cached = syncClient.getCachedMessages(synced.id);
            if (cached && cached.length > 0) {
              synced.messages = cached;
            }
          }
        }
        conversations = [...updated];
        for (const conv of updated) {
          graphStore.upsertNode(conv.id, ['Conversation'], { entity_type: 'Conversation', name: conv.title || conv.id });
        }
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
                  <ImageGallery images={msg.imageUrls ?? []} alt="Uploaded image" />
                {/if}
                {#if msg.audioUrl}
                  <AudioPlayer src={msg.audioUrl} label="Voice message" />
                {/if}
                {#if msg.content}
                  <div class="rounded-2xl rounded-br-sm msg-user-bubble px-4 py-2.5 text-sm" data-testid="user-message-text">
                    {msg.content}
                  </div>
                {/if}
              </div>
            </div>
            <div class="mt-0.5 flex items-center justify-end gap-2">
              <span class="text-[10px] text-cyber-text-dim/50">{formatTime(msg.timestamp)}</span>
              {#if !isActiveConversationStreaming}
                <button
                  onclick={() => { if (confirm('Delete this message?')) deleteMessage(msg.id); }}
                  class="msg-action-btn msg-action-danger opacity-0 group-hover:opacity-100"
                  title="Delete message"
                  data-testid="delete-message-button"
                >
                  <Icon name="trash-2" size={12} />
                  Delete
                </button>
                <button
                  onclick={() => resendMessage(msg.id)}
                  class="msg-action-btn msg-action-accent opacity-0 group-hover:opacity-100"
                  title="Regenerate response"
                  data-testid="regenerate-button"
                >
                  <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                  Regenerate
                </button>
              {/if}
            </div>
          {:else}
            <div class="flex justify-start">
              <div class="max-w-[95%] space-y-1.5">
                {#if msg.thinkingContent}
                  <details class="group" open>
                    <summary class="msg-thinking-summary flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition-colors">
                      <svg class="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.663 17h4.673M12 3v1m0 16v1m-8-9H3m18 0h-1M5.636 5.636l-.707-.707M18.364 18.364l-.707-.707M5.636 18.364l-.707.707M18.364 5.636l-.707.707" stroke-linecap="round" stroke-linejoin="round"/></svg>
                      <span class="font-medium">Thinking</span>
                      <svg class="ml-auto h-3 w-3 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                    </summary>
                    <div class="msg-thinking-content mt-1 max-h-96 overflow-y-auto rounded-lg p-2.5 text-[12px] leading-relaxed whitespace-pre-wrap font-mono">{msg.thinkingContent}</div>
                  </details>
                {/if}

                {#if msg.content || (msg.isStreaming && !msg.mcpToolCalls?.length)}
                <div class="rounded-2xl rounded-bl-sm msg-assistant-bubble px-4 py-2.5 text-sm" data-testid="assistant-message">
                  {#if msg.isStreaming && isProcessing}
                    <div class="flex items-center gap-2 py-1" data-testid="processing-indicator">
                      <div class="flex-1 h-1.5 rounded-full bg-cyber-border/40 overflow-hidden">
                        <div class="h-full rounded-full animate-pulse" style="width: {processingLabel.includes('%') ? processingLabel.match(/(\d+)%/)?.[1] ?? '0' : '100'}%; background: oklch(82% 0.10 70);"></div>
                      </div>
                      <span class="shrink-0 text-[11px] font-medium" style="color: oklch(82% 0.10 70);" data-testid="processing-label">{processingLabel}</span>
                      {#if promptTokens}
                        <span class="shrink-0 font-mono text-[10px] text-cyber-text-dim">{promptTokens} tokens</span>
                      {/if}
                    </div>
                  {:else if msg.isStreaming && !msg.content}
                    <div class="flex items-center gap-2 py-1" data-testid="generating-indicator">
                      <button
                        onclick={cancelStreaming}
                        class="stop-btn"
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
                      <span class="stream-cursor"></span>
                      <button
                        onclick={cancelStreaming}
                        class="stop-btn"
                        title="Stop generating"
                      >
                        <svg class="h-2.5 w-2.5" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1.5" /></svg>
                        Stop
                      </button>
                    </span>
                  {/if}
                  {#if msg.isStreaming && tokensPerSecond && msg.content}
                    <div class="mt-1.5 flex items-center gap-1.5 text-[10px]">
                      <span class="token-badge">
                        <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                        {tokensPerSecond} t/s
                      </span>
                    </div>
                  {/if}
                </div>
                {/if}

                {#if !msg.isStreaming && msg.content}
                  <div class="flex items-center gap-2">
                    <button
                      onclick={() => { if (confirm('Delete this message?')) deleteMessage(msg.id); }}
                      class="msg-action-btn msg-action-danger opacity-0 group-hover:opacity-100"
                      title="Delete message"
                      data-testid="delete-message-button"
                    >
                      <Icon name="trash-2" size={12} />
                      Delete
                    </button>
                    {#if msg.role === 'assistant'}
                      <button
                        onclick={() => {
                          const msgIdx = messages.findIndex((m) => m.id === msg.id);
                          if (msgIdx > 0) {
                            const prevMsg = messages[msgIdx - 1];
                            if (prevMsg.role === 'user') resendMessage(prevMsg.id);
                          }
                        }}
                        class="msg-action-btn msg-action-accent opacity-0 group-hover:opacity-100"
                        title="Regenerate response"
                      >
                        <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                        Regenerate
                      </button>
                    {/if}
                    <button
                      onclick={() => copyToClipboard(msg.content)}
                      class="msg-action-btn msg-action-accent opacity-0 group-hover:opacity-100"
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
                      <summary class="msg-tool-summary flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] transition-colors" data-testid="tool-call-summary">
                        {#if toolCall.isError}
                          <svg class="h-3.5 w-3.5 shrink-0" style="color: var(--danger)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                        {:else if toolCall.result}
                          <svg class="h-3.5 w-3.5 shrink-0" style="color: var(--success)" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L7 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
                        {:else}
                          <div class="h-3.5 w-3.5 shrink-0 rounded-full border-2 border-cyber-orange animate-spin" style="border-top-color: transparent;"></div>
                        {/if}
                        <span class="font-medium">{toolCall.toolName}</span>
                        {#if isSave && savedText}
                          <span class="text-cyber-text-dim/50 truncate max-w-[200px]">— {savedText.slice(0, 60)}{(savedText.length > 60 ? '…' : '')}</span>
                        {:else if parsed && (entityCount || relCount || photoCount)}
                          <span class="text-cyber-text-dim/50">— {entityCount} {entityCount === 1 ? 'entity' : 'entities'}, {relCount} {relCount === 1 ? 'relationship' : 'relationships'}{photoCount ? `, ${photoCount} photo${photoCount > 1 ? 's' : ''}` : ''}</span>
                        {/if}
                        <svg class="ml-auto h-3 w-3 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                      </summary>
                      <div class="mt-1 text-[11px]">
                        {#if toolCall.result}
                          {@const resultText = typeof toolCall.result === 'string' ? toolCall.result : JSON.stringify(toolCall.result, null, 2)}
                          <div class="rounded-lg border border-cyber-green/20 bg-cyber-green/5 p-2 text-cyber-green/80 whitespace-pre-wrap font-mono max-h-60 overflow-y-auto">
                            {#if isSave && parsed}
                              {#each parsed.entities as entity}
                                <div class="mb-0.5">
                                  <span class="text-cyber-cyan/70">{entity.entity_type ?? 'Entity'}</span>:
                                  <span class="text-cyber-text">{entity.name ?? entity.id ?? 'unknown'}</span>
                                </div>
                              {/each}
                              {#each parsed.relationships as rel}
                                <div class="mb-0.5">
                                  <span class="text-cyber-purple/70">{rel.relation_type ?? 'rel'}</span>:
                                  <span class="text-cyber-text">{rel.source} → {rel.target}</span>
                                </div>
                              {/each}
                            {:else}
                              {resultText}
{/if}

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

  .orb-options-backdrop {
    position: fixed;
    inset: 0;
    z-index: 29;
    background: rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    animation: orb-backdrop-in 200ms ease-out;
  }

  @keyframes orb-backdrop-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }

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

  .chat-orb.holding {
    border-color: oklch(82% 0.14 210 / 50%);
    box-shadow:
      0 0 0 1px oklch(82% 0.14 210 / 25%),
      0 0 48px oklch(82% 0.14 210 / 35%),
      0 12px 40px oklch(0% 0 0 / 50%);
    transform: scale(1.06);
  }
  .chat-orb .hold-countdown {
    font-size: 22px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: var(--color-cyber-cyan);
    line-height: 1;
    z-index: 2;
  }
  .chat-orb .hold-progress {
    position: absolute;
    border-radius: 50%;
    border: 2px solid oklch(82% 0.14 210 / 35%);
    pointer-events: none;
    transition: inset 0.05s linear;
    z-index: 1;
  }
  .chat-orb.holding ~ .chat-orb-add,
  .chat-orb.holding ~ .chat-orb-expand,
  .chat-orb.recording ~ .chat-orb-add,
  .chat-orb.recording ~ .chat-orb-expand {
    opacity: 0 !important;
    transform: translateY(20px) scale(0.9) !important;
    pointer-events: none !important;
  }
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
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: inherit;
  }

  .msg-action-btn:focus-visible,
  .chat-conversation-divider-btn:focus-visible,
  .stop-btn:focus-visible,
  .chat-inline-close-btn:focus-visible,
  .chat-history-btn:focus-visible,
  .chat-collapse-row button:focus-visible,
  details summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: 4px;
  }

  /* ── OD prototype new classes ── */

  .msg-user-bubble {
    background: var(--accent-dim);
    border: 1px solid oklch(82% 0.14 210 / 12%);
    color: var(--fg);
  }

  .msg-assistant-bubble {
    background: oklch(16% 0.015 255 / 80%);
    border: 1px solid var(--hairline);
    color: var(--fg);
  }

  .msg-thinking-summary {
    border: 1px solid oklch(72% 0.16 295 / 20%);
    background: oklch(72% 0.16 295 / 5%);
    color: var(--accent-purple);
  }
  .msg-thinking-summary:hover {
    background: oklch(72% 0.16 295 / 10%);
  }
  .msg-thinking-content {
    border: 1px solid oklch(72% 0.16 295 / 8%);
    background: oklch(16% 0.015 255 / 50%);
    color: var(--muted);
  }

  .msg-tool-summary {
    border: 1px solid oklch(72% 0.15 150 / 25%);
    background: oklch(72% 0.15 150 / 5%);
  }
  .msg-tool-summary:hover {
    background: oklch(72% 0.15 150 / 10%);
  }

  .msg-action-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 10px;
    color: var(--faint);
    background: transparent;
    border: none;
    cursor: pointer;
    transition: color 0.15s ease;
    padding: 0;
  }
  .msg-action-btn:hover {
    color: var(--muted);
  }
  .msg-action-btn.msg-action-danger:hover {
    color: var(--danger);
  }
  .msg-action-btn.msg-action-accent:hover {
    color: var(--accent);
  }
  .msg-action-btn:focus-visible {
    outline: 1.5px solid var(--accent);
    outline-offset: 2px;
    border-radius: 3px;
  }

  .stream-cursor {
    display: inline-block;
    width: 2px;
    height: 16px;
    background: var(--accent);
    animation: pulse 1.2s ease-in-out infinite;
  }

  .stop-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    border: 1px solid oklch(62% 0.20 18 / 25%);
    background: oklch(62% 0.20 18 / 5%);
    color: oklch(62% 0.20 18);
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 10px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }
  .stop-btn:hover {
    border-color: oklch(62% 0.20 18 / 50%);
    background: oklch(62% 0.20 18 / 10%);
  }

  .token-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    border-radius: 9999px;
    background: oklch(72% 0.15 150 / 8%);
    padding: 2px 6px;
    font-family: var(--font-mono, ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace);
    font-size: 10px;
    color: var(--success);
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
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
    border: 1px solid var(--hairline);
    border-right: 0;
    scrollbar-width: thin;
    scrollbar-color: oklch(50% 0.03 255 / 20%) transparent;
  }

  .chat-inline-messages::-webkit-scrollbar {
    width: 6px;
  }

  .chat-inline-messages::-webkit-scrollbar-thumb {
    background: oklch(50% 0.03 255 / 20%);
    border-radius: 3px;
  }

  .chat-inline-header {
    width: 100%;
    max-width: 42rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 16px 6px 20px;
    background: oklch(8% 0.02 260 / 90%);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border-radius: 18px 18px 0 0;
    border: 1px solid var(--hairline);
    border-bottom: 0;
    pointer-events: auto;
  }

  .chat-inline-header-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
  }

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
    background: oklch(62% 0.20 18 / 10%);
    color: var(--fg);
  }

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
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
  }

  .chat-empty-state-hint {
    font-size: 12px;
    color: var(--faint);
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
      oklch(50% 0.03 255 / 15%) 12%,
      oklch(50% 0.03 255 / 30%) 50%,
      oklch(50% 0.03 255 / 15%) 88%,
      transparent 100%
    );
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
    background: var(--bg);
    border: 1px solid oklch(82% 0.14 210 / 25%);
    color: var(--accent);
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin: 0 auto;
    z-index: 1;
    box-shadow:
      0 0 8px oklch(82% 0.14 210 / 8%);
    text-shadow: 0 0 4px oklch(82% 0.14 210 / 15%);
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
    border-color: oklch(82% 0.14 210 / 35%);
    box-shadow:
      0 0 12px oklch(82% 0.14 210 / 12%);
    text-shadow: 0 0 6px oklch(82% 0.14 210 / 20%);
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
    color: var(--muted);
    cursor: pointer;
    opacity: 0.55;
    transition: opacity 0.15s ease, background 0.15s ease, transform 0.15s ease;
  }

  .chat-conversation-divider-btn:hover {
    opacity: 1;
    background: var(--accent-dim);
    transform: translateY(-1px);
  }

  .chat-conversation-divider-btn:focus-visible {
    outline: 1.5px solid oklch(82% 0.14 210 / 70%);
    outline-offset: 1px;
    opacity: 1;
  }

  .chat-conversation-divider-delete:hover {
    background: oklch(62% 0.20 18 / 10%);
    color: oklch(62% 0.20 18);
  }

  .chat-conversation-divider-delete:focus-visible {
    outline-color: oklch(62% 0.20 18 / 60%);
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

  .record-countdown-overlay {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    background: oklch(8% 0.02 260 / 94%);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    color: var(--color-cyber-cyan);
    font-size: 64px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    text-shadow: 0 0 32px rgba(0, 212, 255, 0.4);
    animation: fade-in-up 200ms ease-out;
  }

  .record-countdown-sub {
    font-size: 14px;
    font-weight: 500;
    color: var(--color-cyber-text-dim);
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .record-countdown-ring {
    position: relative;
    width: 140px;
    height: 140px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .record-countdown-svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    transform: rotate(-90deg);
  }

  .record-countdown-track {
    fill: none;
    stroke: rgba(255, 255, 255, 0.12);
    stroke-width: 4;
  }

  .record-countdown-progress {
    fill: none;
    stroke: var(--accent);
    stroke-width: 4;
    stroke-linecap: round;
    stroke-dasharray: 339.292;
    transition: stroke-dashoffset 0.1s linear;
    filter: drop-shadow(0 0 8px oklch(82% 0.14 210 / 50%));
  }

  .record-countdown-number {
    font-size: 48px;
    font-weight: 700;
    color: var(--fg);
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  .record-countdown-label {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
  }

  @keyframes fade-in-up {
    0% { opacity: 0; transform: translateY(10px); }
    100% { opacity: 1; transform: translateY(0); }
  }
</style>
                          </div>
                        {:else if toolCall.isError}
                          <div class="rounded-lg border border-cyber-red/20 bg-cyber-red/5 p-2 text-cyber-red/80 whitespace-pre-wrap font-mono">{typeof toolCall.isError === 'string' ? toolCall.isError : 'Error'}</div>
                        {:else}
                          <div class="rounded-lg border border-cyber-orange/20 bg-cyber-orange/5 p-2 text-cyber-orange/80">
                            Running…
                          </div>
                        {/if}
                      </div>
                    </details>
                  {/each}
                {/if}

                {#if msg.model && !msg.isStreaming}
                  <div class="flex items-center gap-2 text-[10px] text-cyber-text-dim/50">
                    <span>{formatModelName(msg.model)}</span>
                    {#if msg.timings}
                      <span>·</span>
                      <span>{formatTokens(msg.timings.prompt_tokens)}↑ {formatTokens(msg.timings.completion_tokens)}↓</span>
                      {#if msg.timings.time_to_first_token_ms}
                        <span>·</span>
                        <span>TTFT {formatDuration(msg.timings.time_to_first_token_ms)}</span>
                      {/if}
                    {/if}
                  </div>
                {/if}
              </div>
            </div>
          {/if}
        </div>
      {/snippet}

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
              <Icon name="download" size={13} color="var(--muted)" />
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
              <Icon name="trash-2" size={13} color="var(--muted)" />
            </button>
          </span>
          <span class="chat-conversation-divider-line"></span>
        </div>
      {/if}

      {#each messages as msg (msg.id)}
        {@render messageRow(msg)}
      {/each}

      {#if isPending}
        <div class="flex items-center gap-2 px-4 py-3" data-testid="pending-indicator">
          <div class="flex items-center gap-2">
            <div class="h-2 w-2 rounded-full bg-cyber-purple animate-pulse"></div>
            <span class="text-xs text-cyber-purple">Queued — waiting for response…</span>
          </div>
        </div>
      {/if}
    </div>
  {/if}

  <!-- Input row (always visible when a conversation exists) -->
  {#if activeConversationId}
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
        <button
          onclick={handlePanelSend}
          disabled={isActiveConversationStreaming || (!panelChatInput.trim() && attachments.length === 0)}
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cyber-cyan/20 text-cyber-cyan transition-all duration-200 hover:bg-cyber-cyan/30 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Send message"
          aria-label="Send message"
          data-testid="send-button"
        >
          <Icon name="send" size={18} />
        </button>
      </div>
    {:else}
      <!-- Collapsed chat orb (bottom center) -->
      <div class="chat-collapsed-orb-host" data-testid="chat-collapsed-orb">
        <div class="chat-collapsed-orb">
          {#if orbOptionsOpen}
            <div class="orb-options-backdrop" onclick={() => { orbOptionsOpen = false; }} role="presentation"></div>
          {/if}
          <div
            class="chat-orb-add"
            class:show={orbOptionsOpen}
            role="button"
            tabindex="0"
            aria-label="Add images"
            data-od-id="chat-orb-add"
            onclick={(e) => { e.stopPropagation(); orbOptionsOpen = false; imageFileInput?.click(); }}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); orbOptionsOpen = false; imageFileInput?.click(); } }}
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

          <!-- Orb mic button -->
          <div
            class="chat-orb {isRecording ? 'recording' : ''} {isStreamActive ? 'streaming' : ''} {holdActive ? 'holding' : ''} {orbOptionsOpen ? 'options-open' : ''}"
            role="button"
            tabindex="0"
            aria-label="Voice input"
            data-od-id="chat-orb"
            data-testid="mic-button"
            onclick={handleMicTap}
            onpointerdown={handleMicPointerDown}
            onpointerup={handleMicPointerUp}
            onpointerleave={handleMicPointerLeave}
            onpointercancel={handleMicPointerCancel}
            ontouchstart={handleMicTouchStart}
            ontouchmove={handleMicTouchMove}
            ontouchend={handleMicTouchEnd}
          >
            {#if holdActive && !isRecording}
              <span class="hold-countdown">{holdCountdown}</span>
              <div class="hold-progress" style="inset: {4 + (holdElapsed / HOLD_TO_RECORD_MS) * 22}px;"></div>
            {:else if isTranscribing}
              <svg class="h-6 w-6 animate-pulse" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><line x1="19" y1="10" x2="19" y2="12" /><line x1="5" y1="10" x2="5" y2="12" /></svg>
            {:else if isRecording}
              <div class="h-5 w-5 rounded-full bg-cyber-red"></div>
            {:else}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
            {/if}
            {#if micTooltipVisible && !holdActive && !isRecording && !isTranscribing}
              <div class="mic-tooltip">{micTooltipMessage}</div>
            {/if}
          </div>
        </div>
      </div>

      <!-- Collapsed input bar (one-line, for quick messages) -->
      <div class="chat-inline-input-row flex h-12 w-full items-stretch gap-1.5 rounded-full border-0 bg-cyber-surface-2/80 px-2 py-0 pr-0.5 transition-colors focus-within:ring-1 focus-within:ring-cyber-cyan/40">
        <textarea
          bind:this={textareaEl}
          bind:value={chatInput}
          oninput={autoResize}
          onkeydown={handleKeydown}
          placeholder="Ask me anything..."
          rows="1"
          data-testid="chat-input"
          class="max-h-[140px] min-h-12 flex-1 resize-none bg-transparent px-4 py-0 text-base leading-[3rem] text-cyber-text outline-none placeholder:text-cyber-text-dim/70 border-0 transition-colors"
          disabled={isActiveConversationStreaming}
        ></textarea>
        <AttachmentMenu
          disabled={isActiveConversationStreaming || attachments.length >= MAX_ATTACHMENTS}
          onPickDocument={openDocumentPicker}
        />
        <button
          onclick={() => handleSend(undefined, undefined, undefined, false)}
          disabled={isActiveConversationStreaming || (!chatInput.trim() && attachments.length === 0)}
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cyber-cyan/20 text-cyber-cyan transition-all duration-200 hover:bg-cyber-cyan/30 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Send message"
          aria-label="Send message"
          data-testid="send-button"
        >
          <Icon name="send" size={18} />
        </button>
      </div>
    {/if}
  {/if}

  </div>

{#if holdActive && !isRecording}
  <div class="record-countdown-overlay" data-testid="record-countdown">
    <div class="record-countdown-ring">
      <svg viewBox="0 0 120 120" class="record-countdown-svg">
        <circle class="record-countdown-track" cx="60" cy="60" r="54" />
        <circle
          class="record-countdown-progress"
          cx="60"
          cy="60"
          r="54"
          style="stroke-dashoffset: {339.292 * (1 - holdElapsed / HOLD_TO_RECORD_MS)}"
        />
      </svg>
      <span class="record-countdown-number">{holdCountdown}</span>
    </div>
    <span class="record-countdown-label">Hold to record</span>
  </div>
{/if}