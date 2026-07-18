export const LIGHTRAG_API = '';
export const LLAMA_API = '';
export const KG_API = '';
export const MCP_API = '';
export const SYNC_URL = '';

export const API = {
  kg: {
    health: '/health',
    imagesProcess: '/images/process',
    imagesProcessJson: '/images/process-json',
    imagesReprocess: '/images/reprocess',
    imagesHealth: '/images/health',
    photoImage: (filename: string) => `/images/photo/${encodeURIComponent(filename)}`,
    photoImageFull: (filename: string) => `/images/photo/${encodeURIComponent(filename)}?w=full`,
    photoImageThumb: (filename: string, w = 256) => `/images/photo/${encodeURIComponent(filename)}?w=${w}`,
    photoExif: (fileSource: string) => `/images/exif/${encodeURIComponent(fileSource)}`,
    deletePhotoEntities: (fileSource: string) => `/images/photo-entities?file_source=${encodeURIComponent(fileSource)}`,
    faceCrop: (name: string) => `/images/faces/crops/${encodeURIComponent(name)}`,
    faceCropById: (faceId: string) => `/images/faces/crops/by-id/${encodeURIComponent(faceId)}`,
    labelFace: '/images/faces/label',
    createJob: '/images/jobs',
    listJobs: '/images/jobs',
    jobStatus: (jobId: string) => `/images/jobs/${jobId}`,
    jobEvents: (jobId: string, after?: number) => `/images/jobs/${jobId}/events${after ? `?after=${after}` : ''}`,
  },
  lightrag: {
    health: '/health',
    status: '/status',
    query: '/query',
    queryStream: '/query/stream',
    queryData: '/query/data',
    chat: '/api/chat',
    chatModels: '/api/show',
    graph: {
      labels: '/graph/label/list',
      popularLabels: '/graph/label/popular',
      searchLabels: '/graph/label/search',
      entityTypes: '/graph/entity/types',
      graph: '/graphs',
      entityExists: '/graph/entity/exists',
      editEntity: '/graph/entity/edit',
      editRelation: '/graph/relation/edit',
      createEntity: '/graph/entity/create',
      createRelation: '/graph/relation/create',
      mergeEntities: '/graph/entities/merge',
    },
    documents: {
      scan: '/documents/scan',
      scanProgress: '/documents/scan-progress',
      list: '/documents',
      paginated: '/documents/paginated',
      status: '/documents/status',
      upload: '/documents/upload',
      delete: (id: string) => `/documents/${id}`,
      reprocess: (id: string) => `/documents/${id}/reprocess`,
      content: (id: string) => `/documents/${id}/content`,
      fullContent: (id: string) => `/documents/${id}/full_content`,
      pipelineStatus: '/documents/pipeline_status',
    },
  },
  llama: {
    chatCompletions: '/v1/chat/completions',
    models: '/v1/models',
    health: '/health',
    slots: '/slots',
    transcriptions: '/v1/audio/transcriptions',
  },
  sync: {
    conversations: '/conversations',
    conversation: (id: string) => `/conversations/${id}`,
    conversationsSince: '/conversations/since',
  },
} as const;

export type QueryMode = 'naive' | 'local' | 'global' | 'hybrid' | 'mix' | 'bypass';

export interface KGNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface KGEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: Record<string, unknown>;
}

export interface KGGraph {
  nodes: KGNode[];
  edges: KGEdge[];
}

export interface QueryRequest {
  query: string;
  mode: QueryMode;
  only_need_context?: boolean;
  only_need_prompt?: boolean;
  response_type?: string;
  stream?: boolean;
  top_k?: number;
  conversation_history?: Message[];
  history_turns?: number;
  include_references?: boolean;
  include_chunk_content?: boolean;
}

export interface KgEntity {
  entity_name: string;
  entity_type: string;
  description: string;
  source_id: string;
  file_path: string;
  created_at: string;
  reference_id: string;
}

export interface KgRelationship {
  src_id: string;
  tgt_id: string;
  description: string;
  keywords: string;
  weight: number;
  source_id: string;
  file_path: string;
  created_at: string;
  reference_id: string;
}

export interface KgChunk {
  content: string;
  file_path: string;
  chunk_id: string;
  reference_id: string;
}

export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface DocStatus {
  id: string;
  content_summary: string;
  content_length: number;
  status: 'pending' | 'parsing' | 'analyzing' | 'handling' | 'processing' | 'preprocessed' | 'processed' | 'failed';
  created_at: string;
  updated_at: string;
  track_id?: string;
  chunks_count?: number;
  error_msg?: string;
  file_path: string;
  file_type?: 'image' | 'text';
}

export interface PipelineStatus {
  autoscanned: boolean;
  busy: boolean;
  job_name: string;
  job_start?: string;
  docs: number;
  batchs: number;
  cur_batch: number;
  request_pending: boolean;
  latest_message: string;
  history_messages?: string[];
}

export interface LightragStatus {
  status: 'healthy';
  configuration: {
    llm_model: string;
    embedding_model: string;
    kv_storage: string;
    graph_storage: string;
    vector_storage: string;
  };
  pipeline_busy: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  isStreaming?: boolean;
  mcpToolCalls?: MCPToolCall[];
  thinkingContent?: string;
  timings?: MessageTimings;
  model?: string;
  audioUrl?: string;
  audioData?: string;
  audioFormat?: 'wav' | 'mp3';
  imageUrls?: string[];
}

export interface MessageTimings {
  promptN?: number;
  promptMs?: number;
  predictedN?: number;
  predictedMs?: number;
  predictedPerSecond?: number;
}

export interface MCPToolCall {
  id?: string;
  toolName: string;
  arguments: Record<string, unknown>;
  result?: string;
  isError?: boolean;
  timestamp: number;
  parsedKG?: {
    entities: { entity: string; type?: string; description?: string }[];
    relationships: { entity1: string; entity2: string; description: string }[];
    imagePaths: string[];
  };
}

export interface OpenAIToolDefinition {
  type: 'function';
  function: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
  };
}

export interface MCPToolInfo {
  name: string;
  description: string;
  enabled: boolean;
  definition: OpenAIToolDefinition;
}

export interface OllamaChatRequest {
  model: string;
  messages: OllamaMessage[];
  stream?: boolean;
  system?: string;
  options?: Record<string, unknown>;
}

export interface OllamaMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  images?: string[];
}

export interface OllamaChatChunk {
  model: string;
  created_at: string;
  message: {
    role: string;
    content: string;
    images: string[] | null;
  };
  done: boolean;
  done_reason?: string;
  total_duration?: number;
  prompt_eval_count?: number;
  eval_count?: number;
}

export interface ActivityEvent {
  id: string;
  type: 'ingestion' | 'query' | 'mcp_call' | 'graph_update' | 'system';
  title: string;
  description: string;
  timestamp: number;
  status?: 'running' | 'completed' | 'error';
  meta?: Record<string, unknown>;
}