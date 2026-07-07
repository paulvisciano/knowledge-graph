import { LLAMA_API, API, type Message } from '$lib/constants';

export interface ChatCompletionOptions {
  messages: Message[];
  model?: string;
  stream?: boolean;
  temperature?: number;
  max_tokens?: number;
}

export interface ChatCompletionResponse {
  id: string;
  object: string;
  created: number;
  model: string;
  choices: {
    index: number;
    message: Message;
    finish_reason: string;
  }[];
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

export interface ModelInfo {
  id: string;
  object: string;
  created: number;
  owned_by: string;
}

export interface SlotInfo {
  id: number;
  model_path: string;
  prompt: string;
  tokens_evaluated: number;
  tokens_predicted: number;
  state: number;
  is_processing: boolean;
}

export class LlamaClient {
  private baseUrl: string;
  private authToken: string | null = null;

  constructor(baseUrl: string = LLAMA_API) {
    this.baseUrl = baseUrl;
  }

  setAuthToken(token: string | null) {
    this.authToken = token;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.authToken) h['Authorization'] = `Bearer ${this.authToken}`;
    return h;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const res = await fetch(url, {
      ...options,
      headers: { ...this.headers(), ...options.headers },
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`Llama ${res.status} ${res.statusText}: ${body}`);
    }
    return res.json();
  }

  async chatCompletions(
    params: ChatCompletionOptions,
    onChunk?: (text: string) => void,
  ): Promise<ChatCompletionResponse | void> {
    const url = `${this.baseUrl}${API.llama.chatCompletions}`;
    const stream = params.stream ?? (onChunk !== undefined);

    const res = await fetch(url, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ ...params, stream }),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`Llama ${res.status} ${res.statusText}: ${body}`);
    }

    if (!stream) {
      return res.json();
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body for streaming');

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(':')) continue;

        if (trimmed.startsWith('data:')) {
          const data = trimmed.slice(5).trim();
          if (data === '[DONE]') return;

          try {
            const parsed = JSON.parse(data);
            const delta = parsed.choices?.[0]?.delta;
            if (delta?.content) onChunk?.(delta.content);
          } catch {
            continue;
          }
        }
      }
    }

    if (buffer.trim()) {
      const trimmed = buffer.trim();
      if (trimmed.startsWith('data:')) {
        const data = trimmed.slice(5).trim();
        if (data !== '[DONE]') {
          try {
            const parsed = JSON.parse(data);
            const delta = parsed.choices?.[0]?.delta;
            if (delta?.content) onChunk?.(delta.content);
          } catch {
            void data;
          }
        }
      }
    }
  }

  async getModels(): Promise<{ data: ModelInfo[] }> {
    return this.request<{ data: ModelInfo[] }>(API.llama.models);
  }

  async getHealth(): Promise<{ status: string }> {
    return this.request<{ status: string }>(API.llama.health);
  }

  async getSlots(): Promise<SlotInfo[]> {
    return this.request<SlotInfo[]>(API.llama.slots);
  }
}

export const llamaClient = new LlamaClient();