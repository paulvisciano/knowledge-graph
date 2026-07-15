export interface ImageProcessingStatus {
  nodeId: string;
  fileName: string;
  dataUrl: string;
  jobId?: string;
  stage: 'extracting_metadata' | 'creating_entities' | 'processing_ai' | 'linking_entities' | 'complete' | 'error';
  get stageLabel(): string;
  error?: string;
  updatedAt: number;
  exifData?: Record<string, unknown>;
}

const EXIF_DISPLAY_KEYS: Record<string, string> = {
  camera: 'Camera',
  date_taken_friendly: 'Date',
  location: 'Location',
  lens: 'Lens',
  f_number: 'f/',
  iso: 'ISO',
  focal_length: 'Focal Length',
  exposure_time: 'Exposure',
  image_width: 'Width',
  image_height: 'Height',
  flash: 'Flash',
  white_balance: 'White Balance',
  orientation: 'Orientation',
};

class ImageProcessingStore {
  statuses = $state<Record<string, ImageProcessingStatus>>({});

  startProcessing(nodeId: string, fileName: string, dataUrl: string, jobId?: string) {
    this.statuses[nodeId] = {
      nodeId,
      fileName,
      dataUrl,
      jobId,
      stage: 'extracting_metadata',
      get stageLabel() {
        return STAGE_LABELS[this.stage] ?? this.stage;
      },
      updatedAt: Date.now(),
    };
    this.statuses = { ...this.statuses };
  }

  getByJobId(jobId: string): ImageProcessingStatus | undefined {
    return Object.values(this.statuses).find((s) => s.jobId === jobId);
  }

  updateStage(nodeId: string, stage: ImageProcessingStatus['stage'], error?: string) {
    const existing = this.statuses[nodeId];
    if (!existing) return;
    existing.stage = stage;
    if (error) existing.error = error;
    existing.updatedAt = Date.now();
    this.statuses = { ...this.statuses };
  }

  setExifData(nodeId: string, exifData: Record<string, unknown>) {
    const existing = this.statuses[nodeId];
    if (!existing) return;
    existing.exifData = exifData;
    this.statuses = { ...this.statuses };
  }

  getExifSummary(nodeId: string): { label: string; value: string }[] {
    const existing = this.statuses[nodeId];
    if (!existing?.exifData) return [];
    const rows: { label: string; value: string }[] = [];
    for (const [key, displayLabel] of Object.entries(EXIF_DISPLAY_KEYS)) {
      const val = existing.exifData[key];
      if (val != null && val !== '') {
        const strVal = String(val);
        if (key === 'f_number') {
          rows.push({ label: displayLabel, value: `f/${strVal}` });
        } else {
          rows.push({ label: displayLabel, value: strVal });
        }
      }
    }
    return rows;
  }

  remove(nodeId: string) {
    delete this.statuses[nodeId];
    this.statuses = { ...this.statuses };
  }

  mapEventToStage(eventName: string): ImageProcessingStatus['stage'] | null {
    if (eventName === 'extracting_exif' || eventName === 'exif_complete' || eventName === 'detecting_faces' || eventName === 'faces_complete' || eventName === 'captions_built' || eventName === 'exif_dimensions_ready') return 'extracting_metadata';
    if (eventName === 'injecting_exif_relations' || eventName === 'creating_exif_entities' || eventName === 'photo_node_created' || eventName === 'exif_node_created' || eventName === 'exif_relation_created' || eventName === 'exif_cross_relation_created' || eventName === 'exif_entities_complete') return 'creating_entities';
    if (eventName === 'describing_image') return 'processing_ai';
    if (eventName === 'upload_complete' || eventName === 'lightrag_upload_complete' || eventName === 'lightrag_processing_waiting') return 'processing_ai';
    if (eventName === 'lightrag_processing_complete') return 'linking_entities';
    if (eventName.startsWith('visual_')) return 'linking_entities';
    if (eventName === 'pipeline_complete') return 'complete';
    if (eventName.endsWith('_failed') || eventName.endsWith('_error') || eventName.endsWith('_timeout')) return 'error';
    return null;
  }
}

const STAGE_LABELS: Record<string, string> = {
  extracting_metadata: 'Analyzing image...',
  creating_entities: 'Creating graph entities...',
  processing_ai: 'Running AI analysis...',
  linking_entities: 'Linking knowledge entities...',
  complete: 'Processing complete',
  error: 'Processing failed',
};

export const imageProcessingStore = new ImageProcessingStore();