import { KG_API, API } from '$lib/constants';

const PROXY_PREFIX = '/api/kg';

export interface ProcessResult {
  exif: Record<string, unknown> | null;
  faces: Record<string, unknown> | null;
  captions: string[];
  content_list: Record<string, unknown>[];
  inserted: boolean;
}

export interface JobInfo {
  job_id: string;
  file_source: string;
  status: string;
  stage: string;
  error?: string;
  created_at: number;
  updated_at: number;
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

  async createJob(
    file: File,
    options: { skipExif?: boolean; skipFaces?: boolean; insert?: boolean } = {}
  ): Promise<JobInfo> {
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
    return this.request(API.kg.createJob, {
      method: 'POST',
      body: formData,
    });
  }

  async listJobs(status?: string): Promise<JobInfo[]> {
    const path = status ? `${API.kg.listJobs}?status=${encodeURIComponent(status)}` : API.kg.listJobs;
    return this.request(path);
  }

  async getJob(jobId: string): Promise<JobInfo> {
    return this.request(API.kg.jobStatus(jobId));
  }

  streamJobEvents(
    jobId: string,
    after: number = 0
  ): { stream: AsyncIterable<{ event: string; data: string; eventId?: number }>; cancel: () => void } {
    const controller = new AbortController();
    const url = this.proxyUrl(API.kg.jobEvents(jobId, after > 0 ? after : undefined));

    const stream = (async function* (): AsyncIterable<{ event: string; data: string; eventId?: number }> {
      const res = await fetch(url, {
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

            let eventId: number | undefined;
            const idMatch = line.match(/^id:\s*(\d+)/m);
            if (idMatch) eventId = parseInt(idMatch[1], 10);

            try {
              const parsed = JSON.parse(data);
              eventId = parsed.event_id ?? eventId;
            } catch { /* ignore */ }

            yield { event: 'message', data, eventId };
          }
        }
      }
    })();

    return {
      stream,
      cancel: () => controller.abort(),
    };
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

  reprocessImageSse(
    fileSource: string,
    options: { skipExif?: boolean; skipFaces?: boolean } = {}
  ): { stream: AsyncIterable<{ event: string; data: string }>; cancel: () => void } {
    const formData = new FormData();
    formData.append('file_source', fileSource);
    if (options.skipExif !== undefined) {
      formData.append('skip_exif', String(options.skipExif));
    }
    if (options.skipFaces !== undefined) {
      formData.append('skip_faces', String(options.skipFaces));
    }

    const controller = new AbortController();
    const url = this.proxyUrl(API.kg.imagesReprocess);

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

  async deletePhotoEntities(fileSource: string): Promise<{ entities_deleted: { name: string; status: string }[]; errors: unknown[] }> {
    const path = API.kg.deletePhotoEntities(fileSource);
    return this.request(path, { method: 'DELETE' });
  }
}

export const kgApiClient = new KgApiClient();