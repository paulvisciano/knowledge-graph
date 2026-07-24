export type ImageStage =
  | 'extracting_exif'
  | 'detecting_faces'
  | 'building_captions'
  | 'creating_entities'
  | 'queued_for_ai'
  | 'describing_image'
  | 'uploading_to_graph'
  | 'graph_processing'
  | 'linking_visual_entities'
  | 'complete'
  | 'error';

export interface ImageProcessingStatus {
  nodeId: string;
  fileName: string;
  dataUrl: string;
  jobId?: string;
  stage: ImageStage;
  get stageLabel(): string;
  get stepper(): { stage: ImageStage; label: string; state: 'pending' | 'current' | 'done' }[];
  error?: string;
  updatedAt: number;
  exifData?: Record<string, unknown>;
}

/** Ordered pipeline stages shown in the stepper. 'error' is excluded — rendered separately. */
const PIPELINE_ORDER: ImageStage[] = [
  'extracting_exif',
  'detecting_faces',
  'building_captions',
  'creating_entities',
  'queued_for_ai',
  'describing_image',
  'uploading_to_graph',
  'graph_processing',
  'linking_visual_entities',
  'complete',
];

const STAGE_LABELS: Record<ImageStage, string> = {
  extracting_exif: 'Extracting EXIF data...',
  detecting_faces: 'Running facial recognition...',
  building_captions: 'Building captions...',
  creating_entities: 'Creating graph entities...',
  queued_for_ai: 'Queued for AI analysis...',
  describing_image: 'Running AI analysis...',
  uploading_to_graph: 'Uploading to knowledge graph...',
  graph_processing: 'Processing in knowledge graph...',
  linking_visual_entities: 'Linking visual entities...',
  complete: 'Processing complete',
  error: 'Processing failed',
};

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
      stage: 'extracting_exif',
      get stageLabel() {
        return STAGE_LABELS[this.stage] ?? this.stage;
      },
      get stepper() {
        const currentIdx = PIPELINE_ORDER.indexOf(this.stage);
        return PIPELINE_ORDER.map((s): { stage: ImageStage; label: string; state: 'pending' | 'current' | 'done' } => {
          const sIdx = PIPELINE_ORDER.indexOf(s);
          const state: 'pending' | 'current' | 'done' =
            currentIdx < 0 ? 'pending' : currentIdx > sIdx ? 'done' : currentIdx === sIdx ? 'current' : 'pending';
          return { stage: s, label: STAGE_LABELS[s], state };
        });
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

  mapEventToStage(eventName: string): ImageStage | null {
    if (eventName === 'extracting_exif' || eventName === 'exif_complete' || eventName === 'exif_dimensions_ready') return 'extracting_exif';
    if (eventName === 'detecting_faces' || eventName === 'faces_complete') return 'detecting_faces';
    if (eventName === 'captions_built') return 'building_captions';
    if (eventName === 'injecting_exif_relations' || eventName === 'creating_exif_entities' || eventName === 'photo_node_created' || eventName === 'exif_node_created' || eventName === 'exif_relation_created' || eventName === 'exif_cross_relation_created' || eventName === 'exif_entities_complete') return 'creating_entities';
    if (eventName === 'queued_for_ai' || eventName === 'exif_phase_complete') return 'queued_for_ai';
    if (eventName === 'describing_image') return 'describing_image';
    if (eventName === 'upload_complete' || eventName === 'lightrag_upload_complete') return 'uploading_to_graph';
    if (eventName === 'lightrag_processing_waiting' || eventName === 'lightrag_processing_timeout') return 'graph_processing';
    if (eventName === 'lightrag_processing_complete' || eventName.startsWith('visual_')) return 'linking_visual_entities';
    if (eventName === 'pipeline_complete') return 'complete';
    if (eventName.endsWith('_failed') || eventName.endsWith('_error') || eventName.endsWith('_timeout')) return 'error';
    return null;
  }
}

export const imageProcessingStore = new ImageProcessingStore();