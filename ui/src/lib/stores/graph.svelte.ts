import type { KGNode, KGEdge } from '$lib/constants';
import { API } from '$lib/constants';
import { lightragClient } from '$lib/services/lightrag-client';
import { isNoteNode } from '$lib/components/canvas/Layout';

const KG_API_BASE = '/api/kg';

class GraphStore {
  nodes = $state<KGNode[]>([]);
  edges = $state<KGEdge[]>([]);
  selectedNode: KGNode | null = $state(null);
  hoveredNode: KGNode | null = $state(null);
  searchQuery = $state('');
  isLoading = $state(false);
  /** nodeId → thumbnail URL for Photo node images (loaded on demand, not base64) */
  photoImages = $state<Record<string, string>>({});
  /** nodeId → dataUrl for Person face-crop images */
  personImages = $state<Record<string, string>>({});
  /** nodeId → full text content for Note nodes, fetched from the processed
   *  LightRAG document (same source as the Ingest tab). Populated async by
   *  `fetchNoteContents`; `buildCanvasLayout` reads this to bake the note
   *  text into the plane's CanvasTexture. */
  noteContents = $state<Record<string, string>>({});

  filteredNodes = $derived.by(() => {
    if (!this.searchQuery.trim()) return this.nodes;
    const q = this.searchQuery.toLowerCase();
    return this.nodes.filter((n) => {
      const label = n.labels?.join(' ').toLowerCase() ?? '';
      const props = JSON.stringify(n.properties).toLowerCase();
      return label.includes(q) || props.includes(q) || n.id.toLowerCase().includes(q);
    });
  });

  filteredEdges = $derived.by(() => {
    if (!this.searchQuery.trim()) return this.edges;
    const nodeIds = new Set(this.filteredNodes.map((n) => n.id));
    return this.edges.filter((e) => nodeIds.has(e.source) || nodeIds.has(e.target));
  });

  async loadGraph(label?: string, nodeId?: string, depth?: number) {
    this.isLoading = true;
    try {
      // LightRAG /graphs requires `label` (422 if omitted); default to 'default'
      const graph = await lightragClient.getGraph(label ?? 'default', nodeId, depth);
      const lightragNodeIds = new Set(graph.nodes.map((n: KGNode) => n.id));
      const lightragEdgeIds = new Set(graph.edges.map((e: KGEdge) => e.id));
      // Preserve locally-added nodes that aren't in the backend response yet
      // (e.g. user-created entities not yet synced), but drop stale optimistic
      // Photo nodes whose source_id is "manual_creation" — those were added
      // during image processing that never completed/persisted, so keeping
      // them would show images with no backing processed document. This
      // mirrors the existing manual_creation filters in fetchPhotoImages
      // (line 87) and GraphView.svelte. A node that is actually a Note (even
      // a spurious `(Photo)` hub whose source_id is a note file_source) is
      // treated as stale from the photo-preservation perspective so it isn't
      // kept as a photo — the canvas renders it as a `note` plane instead.
      const isStalePhotoNode = (n: KGNode): boolean => {
        if (!n.labels?.some((l) => /^(Photo|Image)$/i.test(l)) && n.properties?.entity_type !== 'Photo' && n.properties?.entity_type !== 'Image') return false;
        if (isNoteNode(n)) return true;
        const sourceId = n.properties?.source_id ?? n.properties?.file_path;
        return !sourceId || sourceId === 'manual_creation';
      };
      const preservedNodes = this.nodes.filter(
        (n) => !lightragNodeIds.has(n.id) && !isStalePhotoNode(n),
      );
      const preservedEdges = this.edges.filter((e) => !lightragEdgeIds.has(e.id));
      this.nodes = [...graph.nodes, ...preservedNodes];
      this.edges = [...graph.edges, ...preservedEdges];
      this.fetchPhotoImages(graph.nodes);
      this.fetchNoteContents(graph.nodes);
    } catch (err) {
      console.error('Graph load failed:', err);
    } finally {
      this.isLoading = false;
    }
  }

  /** Resolve thumbnail URLs for Photo nodes (no fetch — loaded on demand by TextureLoader/<img>).
   *  Notes are explicitly excluded even if they carry a `(Photo)` label/entity_type,
   *  so a spurious `(Photo)` hub whose `source_id` is a note file_source never gets
   *  routed to `/api/kg/images/photo/...` (which 404s). Notes render as text planes. */
  private async fetchPhotoImages(nodes: KGNode[]) {
    const photoNodes = nodes.filter((n) =>
      !isNoteNode(n) && (
        n.labels?.some((l) => /^(Photo|Image)$/i.test(l))
        || n.properties?.entity_type === 'Photo'
        || n.properties?.entity_type === 'Image'
        || n.id?.includes('(Photo)')
        || n.id?.includes('(Image)')
      )
    );
    if (photoNodes.length === 0) return;

    const updates: Record<string, string> = {};
    for (const node of photoNodes) {
      if (this.photoImages[node.id]) continue;
      if (isNoteNode(node)) continue;
      const sourceId = node.properties?.source_id ?? node.properties?.file_path;
      if (!sourceId || sourceId === 'manual_creation') continue;
      updates[node.id] = `${KG_API_BASE}${API.kg.photoImageThumb(String(sourceId))}`;
    }
    if (Object.keys(updates).length > 0) {
      this.photoImages = { ...this.photoImages, ...updates };
    }
  }

  /** Fetch the full text content for Note nodes from the processed LightRAG
   *  document (same source as the Ingest tab's `getDocumentFullContent`). The
   *  note hub's `source_id` is the document's `file_path`; `resolveDocumentId`
   *  maps it to a doc id, then `getDocumentFullContent` returns the text.
   *  Populates `noteContents` so `buildCanvasLayout` can bake the real note
   *  text into the plane texture (instead of the short `"Note: {file_source}"`
   *  description label). Mirrors `fetchPhotoImages`'s async-then-populate
   *  pattern; the CanvasView `$effect` rebuilds when `noteContents` updates. */
  private async fetchNoteContents(nodes: KGNode[]) {
    const noteNodes = nodes.filter((n) => isNoteNode(n));
    if (noteNodes.length === 0) return;
    const updates: Record<string, string> = {};
    for (const node of noteNodes) {
      if (this.noteContents[node.id]) continue;
      const fileSource = node.properties?.source_id;
      if (!fileSource || typeof fileSource !== 'string') continue;
      try {
        const docId = await lightragClient.resolveDocumentId(fileSource);
        if (!docId) continue;
        const data = await lightragClient.getDocumentFullContent(docId);
        const content = data?.content ?? '';
        if (content) updates[node.id] = content;
      } catch (err) {
        console.error('[fetchNoteContents] Failed for', node.id, err);
      }
    }
    if (Object.keys(updates).length > 0) {
      this.noteContents = { ...this.noteContents, ...updates };
    }
  }

  selectNode(node: KGNode | null) {
    this.selectedNode = node;
  }

  setHoveredNode(node: KGNode | null) {
    this.hoveredNode = node;
  }

  searchEntities(query: string) {
    this.searchQuery = query;
  }

  clearSearch() {
    this.searchQuery = '';
  }

  async refresh() {
    await this.loadGraph(undefined, undefined, undefined);
  }

  pipelineDone = $state(false);

  addNode(node: KGNode) {
    this.nodes = [...this.nodes.filter((n) => n.id !== node.id), node];
  }

  addEdge(edge: KGEdge) {
    this.edges = [...this.edges.filter((e) => e.id !== edge.id), edge];
  }

  upsertNode(id: string, labels: string[], properties: Record<string, unknown>) {
    const node: KGNode = { id, labels, properties };
    this.nodes = [...this.nodes.filter((n) => n.id !== id), node];
  }

  /** Merge properties into an existing node, preserving any backend-provided
   *  fields not present in the incoming `properties`. Falls back to
   *  `upsertNode` when the node doesn't exist yet. */
  mergeNodeProperties(id: string, labels: string[], properties: Record<string, unknown>) {
    const existing = this.nodes.find((n) => n.id === id);
    if (!existing) {
      this.upsertNode(id, labels, properties);
      return;
    }
    existing.properties = { ...existing.properties, ...properties };
    this.nodes = [...this.nodes];
  }

  upsertEdge(source: string, target: string, type: string, properties: Record<string, unknown> = {}) {
    const id = `${source}-${type}-${target}`;
    const edge: KGEdge = { id, source, target, type, properties };
    this.edges = [...this.edges.filter((e) => e.id !== id), edge];
  }

  /** Optimistically remove a photo node (and its edges + cached images) for
   *  the given file source. Called by delete handlers so the graph updates
   *  immediately, since `refresh()` preserves nodes missing from the backend
   *  response (intended for locally-created, not-yet-synced entities). */
  removePhoto(fileSource: string): void {
    const nodeId = this.nodes.find(
      (n) =>
        (n.properties?.source_id as string) === fileSource ||
        (n.properties?.file_path as string) === fileSource ||
        n.id === fileSource,
    )?.id;
    if (!nodeId) return;
    this.nodes = this.nodes.filter((n) => n.id !== nodeId);
    this.edges = this.edges.filter((e) => e.source !== nodeId && e.target !== nodeId);
    delete this.photoImages[nodeId];
    this.photoImages = { ...this.photoImages };
    delete this.personImages[nodeId];
    this.personImages = { ...this.personImages };
  }

  setPhotoImage(nodeId: string, url: string) {
    this.photoImages = { ...this.photoImages, [nodeId]: url };
  }

  setPersonImage(nodeId: string, dataUrl: string) {
    this.personImages = { ...this.personImages, [nodeId]: dataUrl };
  }

  private async fetchPersonImages(nodes: KGNode[]) {
    const personNodes = nodes.filter((n) => {
      const et = n.properties?.entity_type;
      if (typeof et === 'string') return et.toLowerCase() === 'person';
      return n.labels?.some((l) => l.toLowerCase() === 'person') ?? false;
    });
    if (personNodes.length === 0) return;

    const updates: Record<string, string> = {};
    await Promise.all(
      personNodes.map(async (node) => {
        if (this.personImages[node.id]) return;
        try {
          const faceId = node.properties?.face_id as string | undefined;
          const url = faceId
            ? `${'/api/kg'}${API.kg.faceCropById(faceId)}`
            : lightragClient.personPhotoUrl(node.id);
          const resp = await fetch(url);
          if (!resp.ok) return;
          const blob = await resp.blob();
          const dataUrl = await new Promise<string>((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result as string);
            reader.readAsDataURL(blob);
          });
          updates[node.id] = dataUrl;
        } catch {}
      })
    );
    if (Object.keys(updates).length > 0) {
      this.personImages = { ...this.personImages, ...updates };
    }
  }

  reset() {
    this.nodes = [];
    this.edges = [];
    this.selectedNode = null;
    this.hoveredNode = null;
    this.searchQuery = '';
  }
}

export const graphStore = new GraphStore();