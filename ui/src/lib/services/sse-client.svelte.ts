import { API } from '$lib/constants';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Event types emitted by the server-side SSE endpoint. */
export type SseEventType =
  | 'new_conversation'
  | 'new_message'
  | 'token'
  | 'tool_calls'
  | 'tool_call_start'
  | 'tool_call_result'
  | 'status_change'
  | 'complete'
  | 'error'
  | 'start'
  | 'navigate';

/** A single parsed SSE event dispatched to subscribers. */
export interface LlmEvent {
  /** Server-assigned sequential event id (for Last-Event-ID reconnection). */
  id: string | null;
  /** The event type — determines the shape of `data`. */
  type: SseEventType;
  /** Conversation id this event belongs to (may be absent for global events). */
  convId: string | null;
  /** LLM job id this event belongs to (may be absent). */
  jobId: string | null;
  /** The event payload — shape varies by `type`. */
  data: unknown;
  /** Monotonic timestamp when the client received the event. */
  receivedAt: number;
}

/** Callback signature for event subscribers. */
type SseEventHandler = (event: LlmEvent) => void;

/** Conversation status returned by the REST endpoint. */
export interface ConversationStatus {
  conv_id: string;
  status: string;
  job_id: string | null;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_EVENTS = 100;
const BACKOFF_INITIAL_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
const BACKOFF_MULTIPLIER = 2;

// ---------------------------------------------------------------------------
// SseClient
// ---------------------------------------------------------------------------

class SseClient {
  // --- Reactive state ($state runes) ---

  connected = $state(false);
  lastEventId = $state<string | null>(null);
  events = $state<LlmEvent[]>([]);

  // --- Private state ---

  #eventSource: EventSource | null = null;
  #subscribers = new Map<SseEventType, Set<SseEventHandler>>();
  #globalSubscribers = new Set<SseEventHandler>();
  #reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  #backoffMs = BACKOFF_INITIAL_MS;
  #disposed = false;

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  /** Open an SSE connection to the server. Idempotent — no-op if already connected. */
  connect(): void {
    if (this.#eventSource) return; // already connected / connecting

    const url = API.chat.events;
    const es = new EventSource(url);
    this.#eventSource = es;

    es.onopen = () => {
      this.connected = true;
      this.#backoffMs = BACKOFF_INITIAL_MS; // reset backoff on successful connect
    };

    es.onmessage = (msg: MessageEvent) => {
      this.#handleMessage(msg);
    };

    es.onerror = () => {
      // EventSource auto-closes on fatal errors; we handle reconnect below.
      // The `readyState` may already be CLOSED at this point.
      this.connected = false;
      this.#scheduleReconnect();
    };
  }

  /** Close the SSE connection and stop auto-reconnect. */
  disconnect(): void {
    this.#clearReconnectTimer();
    if (this.#eventSource) {
      this.#eventSource.close();
      this.#eventSource = null;
    }
    this.connected = false;
  }

  /** Disconnect and reconnect, sending `Last-Event-ID` so the server can
   *  replay missed events. */
  reconnect(): void {
    this.disconnect();
    this.connect();
  }

  /** Subscribe to events of a specific `type`.
   *  Returns an unsubscribe function. */
  on(type: SseEventType, handler: SseEventHandler): () => void {
    let set = this.#subscribers.get(type);
    if (!set) {
      set = new Set();
      this.#subscribers.set(type, set);
    }
    set.add(handler);
    return () => set!.delete(handler);
  }

  /** Subscribe to ALL events regardless of type.
   *  Returns an unsubscribe function. */
  onAny(handler: SseEventHandler): () => void {
    this.#globalSubscribers.add(handler);
    return () => this.#globalSubscribers.delete(handler);
  }

  /** Fetch the current pending/streaming status for the given conversation IDs. */
  async fetchStatuses(convIds: string[]): Promise<ConversationStatus[]> {
    if (convIds.length === 0) return [];

    const params = new URLSearchParams({ conv_ids: convIds.join(',') });
    const url = `${API.chat.statuses}?${params.toString()}`;

    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Failed to fetch conversation statuses: ${res.status}`);
    }
    return res.json() as Promise<ConversationStatus[]>;
  }

  // -----------------------------------------------------------------------
  // Private helpers
  // -----------------------------------------------------------------------

  #handleMessage(msg: MessageEvent): void {
    // Track the SSE event id for reconnection
    if (msg.lastEventId) {
      this.lastEventId = msg.lastEventId;
    }

    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(msg.data);
    } catch {
      // Not JSON — ignore malformed events
      return;
    }

    const type = (parsed.type as SseEventType) ?? 'message';
    const convId = (parsed.conv_id as string) ?? null;
    const jobId = (parsed.job_id as string) ?? null;

    const event: LlmEvent = {
      id: msg.lastEventId ?? null,
      type,
      convId,
      jobId,
      data: parsed.data ?? parsed,
      receivedAt: Date.now(),
    };

    // Push to reactive events array (FIFO capped at MAX_EVENTS)
    this.events = [...this.events.slice(-(MAX_EVENTS - 1)), event];

    // Dispatch to typed subscribers
    const typed = this.#subscribers.get(type);
    if (typed) {
      for (const handler of typed) handler(event);
    }

    // Dispatch to global subscribers
    for (const handler of this.#globalSubscribers) handler(event);
  }

  #scheduleReconnect(): void {
    if (this.#disposed) return;

    // Clean up the old EventSource if still present
    if (this.#eventSource) {
      this.#eventSource.close();
      this.#eventSource = null;
    }

    this.#clearReconnectTimer();

    // Use Last-Event-ID on reconnect so the server replays missed events
    // Unfortunately, the native EventSource API doesn't let us set headers,
    // but it DOES send the `Last-Event-ID` header automatically when the
    // server sends events with `id:` fields. Since our server sends `id`,
    // EventSource will automatically include `Last-Event-ID` on reconnect.
    this.#reconnectTimer = setTimeout(() => {
      this.connect();
    }, this.#backoffMs);

    this.#backoffMs = Math.min(this.#backoffMs * BACKOFF_MULTIPLIER, BACKOFF_MAX_MS);
  }

  #clearReconnectTimer(): void {
    if (this.#reconnectTimer !== null) {
      clearTimeout(this.#reconnectTimer);
      this.#reconnectTimer = null;
    }
  }
}

// ---------------------------------------------------------------------------
// Singleton export
// ---------------------------------------------------------------------------

export const sseClient = new SseClient();