import { KG_API, API } from '$lib/constants';

const PROXY_PREFIX = '/api/kg';

export interface ProcessResult {
  exif: Record<string, unknown> | null;
  faces: Record<string, unknown> | null;
  captions: string[];
  content_list: Record<string, unknown>[];
  inserted: boolean;
}

export class KgApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = KG_API) {
    this.baseUrl = baseUrl;
  }

  private proxyUrl(path: string): string {
    return `${this.baseUrl}${PROXY_PREFIX}${path}`;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = this.proxyUrl(path);
    const res = await fetch(url, options);
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`KG API ${res.status} ${res.statusText}: ${body}`);
    }
    return res.json();
  }

  async health(): Promise<{ status: string; service: string }> {
    return this.request(API.kg.health);
  }

  async processImage(
    file: File,
    options: { skipExif?: boolean; skipFaces?: boolean; insert?: boolean } = {}
  ): Promise<ProcessResult> {
    const formData = new FormData();
    formData.append('file', file);
    if (options.skipExif !== undefined) {
      formData.append('skip_exif', String(options.skipExif));
    }
    if (options.skipFaces !== undefined) {
      formData.append('skip_faces', String(options.skipFaces));
    }
    if (options.insert !== undefined) {
      formData.append('insert', String(options.insert));
    }

    return this.request(API.kg.imagesProcessJson, {
      method: 'POST',
      body: formData,
    });
  }

  processImageSse(
    file: File,
    options: { skipExif?: boolean; skipFaces?: boolean; insert?: boolean } = {}
  ): { stream: AsyncIterable<{ event: string; data: string }>; cancel: () => void } {
    const formData = new FormData();
    formData.append('file', file);
    if (options.skipExif !== undefined) {
      formData.append('skip_exif', String(options.skipExif));
    }
    if (options.skipFaces !== undefined) {
      formData.append('skip_faces', String(options.skipFaces));
    }
    if (options.insert !== undefined) {
      formData.append('insert', String(options.insert));
    }

    const controller = new AbortController();
    const url = this.proxyUrl(API.kg.imagesProcess);

    const stream = (async function* (): AsyncIterable<{ event: string; data: string }> {
      const res = await fetch(url, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`KG API ${res.status} ${res.statusText}: ${body}`);
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
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') return;
            yield { event: 'message', data };
          }
        }
      }
    })();

    return {
      stream,
      cancel: () => controller.abort(),
    };
  }
}

export const kgApiClient = new KgApiClient();