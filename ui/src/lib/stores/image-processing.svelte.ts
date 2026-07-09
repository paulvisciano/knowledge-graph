/** Per-image processing status for the knowledge graph pipeline */

export interface ImageProcessingStatus {
  /** Node ID for the Photo node, e.g. "filename.jpg (Photo)" */
  nodeId: string;
  /** Original filename */
  fileName: string;
  /** Data URL of the image for display */
  dataUrl: string;
  /** Pipeline stage */
  stage: 'attaching' | 'extracting_metadata' | 'creating_entities' | 'uploading' | 'processing_ai' | 'linking_entities' | 'complete' | 'error';
  /** Human-readable stage label */
  get stageLabel(): string;
  /** Error message if stage === 'error' */
  error?: string;
  /** Timestamp of last update */
  updatedAt: number;
}

class ImageProcessingStore {
  statuses = $state<Record<string, ImageProcessingStatus>>({});

  startProcessing(nodeId: string, fileName: string, dataUrl: string) {
    this.statuses[nodeId] = {
      nodeId,
      fileName,
      dataUrl,
      stage: 'attaching',
      get stageLabel() {
        return STAGE_LABELS[this.stage] ?? this.stage;
      },
      updatedAt: Date.now(),
    };
    this.statuses = { ...this.statuses };
  }

  updateStage(nodeId: string, stage: ImageProcessingStatus['stage'], error?: string) {
    const existing = this.statuses[nodeId];
    if (!existing) return;
    existing.stage = stage;
    if (error) existing.error = error;
    existing.updatedAt = Date.now();
    this.statuses = { ...this.statuses };
  }

  remove(nodeId: string) {
    delete this.statuses[nodeId];
    this.statuses = { ...this.statuses };
  }

  /** Map SSE event names to pipeline stages */
  mapEventToStage(eventName: string): ImageProcessingStatus['stage'] | null {
    if (eventName === 'photo_node_created') return 'creating_entities';
    if (eventName === 'exif_node_created') return 'creating_entities';
    if (eventName === 'exif_dimensions_ready') return 'extracting_metadata';
    if (eventName === 'exif_entities_complete') return 'creating_entities';
    if (eventName === 'upload_complete') return 'uploading';
    if (eventName === 'lightrag_upload_complete') return 'processing_ai';
    if (eventName === 'lightrag_processing_waiting') return 'processing_ai';
    if (eventName === 'lightrag_processing_complete') return 'linking_entities';
    if (eventName.startsWith('visual_')) return 'linking_entities';
    if (eventName === 'pipeline_complete') return 'complete';
    if (eventName.endsWith('_failed') || eventName.endsWith('_error') || eventName.endsWith('_timeout')) return 'error';
    return null;
  }
}

const STAGE_LABELS: Record<string, string> = {
  attaching: 'Attaching image...',
  extracting_metadata: 'Extracting metadata...',
  creating_entities: 'Creating graph entities...',
  uploading: 'Uploading to AI...',
  processing_ai: 'Running AI analysis...',
  linking_entities: 'Linking knowledge entities...',
  complete: 'Processing complete',
  error: 'Processing failed',
};

export const imageProcessingStore = new ImageProcessingStore();