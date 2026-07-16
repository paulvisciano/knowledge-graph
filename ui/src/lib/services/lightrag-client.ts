import {
  LIGHTRAG_API,
  API,
  type QueryRequest,
  type LightragStatus,
  type KGGraph,
  type DocStatus,
  type PipelineStatus,
  type OllamaMessage,
  type OllamaChatChunk,
} from '$lib/constants';

const PROXY_PREFIX = '/api/lightrag';

export class LightragClient {
  private baseUrl: string;
  private authToken: string | null = null;

  constructor(baseUrl: string = LIGHTRAG_API) {
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

  private proxyUrl(path: string): string {
    return `${this.baseUrl}${PROXY_PREFIX}${path}`;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = this.proxyUrl(path);
    const res = await fetch(url, {
      ...options,
      headers: { ...this.headers(), ...options.headers },
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`LightRAG ${res.status} ${res.statusText}: ${body}`);
    }
    return res.json();
  }

  async health(): Promise<LightragStatus> {
    return this.request<LightragStatus>(API.lightrag.health);
  }

  async getStatus(): Promise<LightragStatus> {
    return this.request<LightragStatus>(API.lightrag.status);
  }

  async query(params: QueryRequest): Promise<{ response: string }> {
    return this.request<{ response: string }>(API.lightrag.query, {
      method: 'POST',
      body: JSON.stringify({ ...params, stream: false }),
    });
  }

  async *queryStream(params: QueryRequest): AsyncGenerator<string> {
    const url = this.proxyUrl(API.lightrag.queryStream);
    const res = await fetch(url, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ ...params, stream: true }),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`LightRAG ${res.status} ${res.statusText}: ${body}`);
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body');

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
            const text = parsed.content ?? parsed.text ?? parsed.delta ?? data;
            if (text) yield text;
          } catch {
            if (data) yield data;
          }
        } else {
          try {
            const parsed = JSON.parse(trimmed);
            const text = parsed.content ?? parsed.text ?? parsed.delta ?? '';
            if (text) yield text;
          } catch {
            if (trimmed) yield trimmed;
          }
        }
      }
    }

    if (buffer.trim()) {
      const trimmed = buffer.trim();
      if (trimmed.startsWith('data:')) {
        const data = trimmed.slice(5).trim();
        if (data !== '[DONE]') yield data;
      } else if (trimmed !== '[DONE]') {
        yield trimmed;
      }
    }
  }

  /** Stream a KG-augmented chat response via LightRAG's Ollama-compatible /api/chat endpoint.
   *  Yields OllamaChatChunk objects in real-time. The final chunk has done=true with timing stats.
   *  Supports query mode prefixes in the last user message: /local, /global, /hybrid, /naive, /mix, /bypass
   */
  async *chatStream(
    messages: OllamaMessage[],
    options?: { model?: string; system?: string },
  ): AsyncGenerator<OllamaChatChunk> {
    const url = this.proxyUrl(API.lightrag.chat);
    const res = await fetch(url, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        model: options?.model ?? 'lightrag',
        messages,
        stream: true,
        ...(options?.system ? { system: options.system } : {}),
      }),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`LightRAG chat ${res.status} ${res.statusText}: ${body}`);
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body for chat stream');

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
        if (!trimmed) continue;
        try {
          const chunk: OllamaChatChunk = JSON.parse(trimmed);
          yield chunk;
        } catch {
        }
      }
    }

    if (buffer.trim()) {
      try {
        const chunk: OllamaChatChunk = JSON.parse(buffer.trim());
        yield chunk;
      } catch {
        // skip
      }
    }
  }

  async chat(
    messages: OllamaMessage[],
    options?: { model?: string; system?: string },
  ): Promise<string> {
    const res = await this.request<{ message: { content: string }; done: boolean }>(API.lightrag.chat, {
      method: 'POST',
      body: JSON.stringify({
        model: options?.model ?? 'lightrag',
        messages,
        stream: false,
        ...(options?.system ? { system: options.system } : {}),
      }),
    });
    return res.message?.content ?? '';
  }

  async getGraph(label?: string, nodeId?: string, depth?: number): Promise<KGGraph> {
    const params = new URLSearchParams();
    if (label) params.set('label', label);
    if (nodeId) params.set('node_id', nodeId);
    if (depth !== undefined) params.set('depth', String(depth));
    const qs = params.toString();
    const path = `${API.lightrag.graph.graph}${qs ? '?' + qs : ''}`;
    return this.request<KGGraph>(path);
  }

  async getLabels(): Promise<string[]> {
    return this.request<string[]>(API.lightrag.graph.labels);
  }

  async getPopularLabels(limit?: number): Promise<string[]> {
    const qs = limit !== undefined ? `?limit=${limit}` : '';
    return this.request<string[]>(`${API.lightrag.graph.popularLabels}${qs}`);
  }

  async searchLabels(query: string, limit?: number, offset?: number): Promise<string[]> {
    const params = new URLSearchParams({ q: query });
    if (limit !== undefined) params.set('limit', String(limit));
    if (offset !== undefined) params.set('offset', String(offset));
    return this.request<string[]>(`${API.lightrag.graph.searchLabels}?${params}`);
  }

  async getEntityTypes(): Promise<string[]> {
    return this.request<string[]>(API.lightrag.graph.entityTypes);
  }

  async createEntity(
    name: string,
    type: string,
    properties?: Record<string, unknown>,
  ): Promise<unknown> {
    return this.request<unknown>(API.lightrag.graph.createEntity, {
      method: 'POST',
      body: JSON.stringify({ name, type, properties: properties ?? {} }),
    });
  }

  async createRelation(
    source: string,
    target: string,
    relation: string,
    properties?: Record<string, unknown>,
  ): Promise<unknown> {
    return this.request<unknown>(API.lightrag.graph.createRelation, {
      method: 'POST',
      body: JSON.stringify({ source, target, relation, properties: properties ?? {} }),
    });
  }

  async mergeEntities(source: string, target: string): Promise<unknown> {
    return this.request<unknown>(API.lightrag.graph.mergeEntities, {
      method: 'POST',
      body: JSON.stringify({ source, target }),
    });
  }

  async scanDocuments(): Promise<{ status: string; message: string }> {
    return this.request<{ status: string; message: string }>(API.lightrag.documents.scan, {
      method: 'POST',
    });
  }

  async getDocuments(
    page?: number,
    pageSize?: number,
  ): Promise<{ documents: DocStatus[]; total: number; page: number; page_size: number }> {
    const params = new URLSearchParams();
    if (page !== undefined) params.set('page', String(page));
    if (pageSize !== undefined) params.set('page_size', String(pageSize));
    const qs = params.toString();
    const raw = await this.request<Record<string, unknown>>(`${API.lightrag.documents.list}${qs ? '?' + qs : ''}`);

    // API returns { statuses: { processed: [...], pending: [...], ... } }
    // Flatten all status groups into a single documents array
    const allDocs: DocStatus[] = [];
    let total = 0;
    if (raw.statuses && typeof raw.statuses === 'object') {
      for (const docs of Object.values(raw.statuses as Record<string, DocStatus[]>)) {
        if (Array.isArray(docs)) {
          allDocs.push(...docs);
          total += docs.length;
        }
      }
    } else if (Array.isArray(raw.documents)) {
      allDocs.push(...(raw.documents as DocStatus[]));
      total = (raw.total as number) ?? allDocs.length;
    }

    return {
      documents: allDocs,
      total,
      page: (raw.page as number) ?? page ?? 1,
      page_size: (raw.page_size as number) ?? pageSize ?? allDocs.length,
    };
  }

  async getDocumentStatus(): Promise<DocStatus[]> {
    return this.request<DocStatus[]>(API.lightrag.documents.status);
  }

  async getPipelineStatus(): Promise<PipelineStatus> {
    return this.request<PipelineStatus>(API.lightrag.documents.pipelineStatus);
  }

  async uploadDocuments(files: File[]): Promise<{ status: string; message: string }> {
    const formData = new FormData();
    for (const file of files) formData.append('files', file);

    const headers: Record<string, string> = {};
    if (this.authToken) headers['Authorization'] = `Bearer ${this.authToken}`;

    const res = await fetch(this.proxyUrl(API.lightrag.documents.upload), {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`LightRAG upload ${res.status} ${res.statusText}: ${body}`);
    }

    return res.json();
  }

  async deleteDocument(id: string): Promise<{ status: string; message: string }> {
    return this.request<{ status: string; message: string }>(API.lightrag.documents.delete(id), {
      method: 'DELETE',
    });
  }

  async reprocessDocument(id: string): Promise<unknown> {
    return this.request<unknown>(API.lightrag.documents.reprocess(id), {
      method: 'POST',
    });
  }

  async cancelPipeline(): Promise<unknown> {
    return this.request<unknown>('/pipeline/cancel', {
      method: 'POST',
    });
  }

  personPhotoUrl(name: string): string {
    const KG_API_BASE = '/api/kg';
    return `${KG_API_BASE}${API.kg.faceCrop(name)}`;
  }

  photoImageUrl(filename: string): string {
    return `/api/kg${API.kg.photoImage(filename)}`;
  }

  documentContentUrl(docId: string): string {
    return this.proxyUrl(API.lightrag.documents.content(docId));
  }

  /** Resolve a graph node file_path to a document content URL.
   *  Uses a cached paginated document search to map file_path → doc_id → content URL.
   *  Returns null if the file cannot be found in the document index.
   */
  async resolveImageContentUrl(filePath: string): Promise<string | null> {
    await this.refreshDocCache();

    if (this.docIdCache.size === 0) {
      console.warn('[resolveImageContentUrl] Document cache is empty — cannot resolve filePath:', filePath);
      return null;
    }

    // 1. Exact match by file_path or basename
    const basename = filePath.split('/').pop() || filePath;
    const exactId = this.docIdCache.get(basename) || this.docIdCache.get(filePath);
    if (exactId) {
      return this.documentContentUrl(exactId);
    }

    // 2. Normalized matching (relaxed — does not require file_type)
    const normalizedTarget = this.normalizeFilePath(filePath);
    if (!normalizedTarget) {
      console.warn('[resolveImageContentUrl] Could not normalize filePath:', filePath);
      return null;
    }

    // Prefer image-typed docs, then fall back to any doc with a name match
    let fallbackMatch: string | null = null;
    for (const [key, id] of this.docIdCache) {
      const normalizedKey = this.normalizeFilePath(key);
      if (!normalizedKey || normalizedKey.length <= 5 || normalizedTarget.length <= 5) continue;
      if (normalizedTarget.includes(normalizedKey) || normalizedKey.includes(normalizedTarget)) {
        if (this.docFileTypeCache.get(id)?.toLowerCase() === 'image') {
          return this.documentContentUrl(id);
        }
        if (!fallbackMatch) {
          fallbackMatch = id;
        }
      }
    }

    if (fallbackMatch) {
      return this.documentContentUrl(fallbackMatch);
    }

    console.warn('[resolveImageContentUrl] No matching document found for filePath:', filePath, '(normalized:', normalizedTarget, ', cache size:', this.docIdCache.size, ')');
    return null;
  }

  private normalizeFilePath(fp: string): string {
    let s = fp.toLowerCase();
    s = s.split('/').pop() || s;
    s = s.replace(/^\d{4}_\d{2}_/, '');
    for (let i = 0; i < 3; i++) {
      s = s.replace(/\.\w+-\d+$/, '');
      s = s.replace(/\.[a-z0-9]+$/, '');
    }
    return s;
  }

  private docIdCache: Map<string, string> = new Map();
  private docFileTypeCache: Map<string, string> = new Map();
  private docCacheTimestamp: number = 0;
  private static DOC_CACHE_TTL = 60_000;

  private async refreshDocCache(): Promise<void> {
    const now = Date.now();
    if (now - this.docCacheTimestamp < LightragClient.DOC_CACHE_TTL && this.docIdCache.size > 0) {
      return;
    }
    this.docIdCache.clear();
    this.docFileTypeCache.clear();
    try {
      const res = await this.getDocuments(1, 10000);
      for (const doc of res.documents) {
        if (doc.file_path) {
          const basename = doc.file_path.split('/').pop() || doc.file_path;
          this.docIdCache.set(basename, doc.id);
          this.docIdCache.set(doc.file_path, doc.id);
        }
        if (doc.file_type) {
          this.docFileTypeCache.set(doc.id, doc.file_type);
        }
      }
    } catch (err) {
      console.warn('[refreshDocCache] Failed to refresh document cache:', err);
    }
    this.docCacheTimestamp = now;
  }
}

export const lightragClient = new LightragClient();