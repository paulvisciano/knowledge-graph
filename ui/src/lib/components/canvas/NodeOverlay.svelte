<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import { API, type KGNode, type ChatMessage } from '$lib/constants';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
  import { syncClient } from '$lib/services/sync-client.svelte';
  import type { CanvasNode } from './renderer/types';
  import { classifyKind } from './Layout';
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';

  const KG_API_PROXY_BASE = '/api/kg';

  marked.use({
    renderer: {
      code({ text, lang }: { text: string; lang?: string }) {
        const language = lang || 'plaintext';
        return `<pre class="overlay-code"><code class="language-${language}">${text}</code></pre>`;
      },
      paragraph({ text }: { text: string }) {
        return `<p class="overlay-p">${text}</p>`;
      },
    },
  });

  function renderMarkdown(text: string): string {
    const raw = marked.parse(text, { async: false }) as string;
    return DOMPurify.sanitize(raw);
  }

  function getNodeName(n: KGNode): string {
    return (n.properties?.name as string) ?? (n.properties?.title as string) ?? n.id;
  }

  function isPersonNamed(n: KGNode): boolean {
    const name = getNodeName(n);
    return name !== n.id && name.length > 0 && !/^[a-f0-9-]{8,}$/i.test(name);
  }

  function personFaceCropUrl(n: KGNode): string | null {
    const faceId = n.properties?.face_id as string | undefined;
    if (!faceId) return null;
    return `${KG_API_PROXY_BASE}${API.kg.faceCropById(faceId)}`;
  }

  function personFallbackUrl(n: KGNode): string {
    return `${KG_API_PROXY_BASE}${API.kg.faceCrop(n.id)}`;
  }

  function resolvePersonThumbUrl(n: KGNode): string {
    return personFaceCropUrl(n) ?? personFallbackUrl(n);
  }

  function getInitials(name: string): string {
    return name
      .split(/[\s_]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase();
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

  const EXIF_CAMERA_KEYS = ['camera', 'date_taken_friendly', 'lens', 'flash'];
  const EXIF_EXPOSURE_KEYS = ['f_number', 'iso', 'focal_length', 'exposure_time'];
  const EXIF_IMAGE_KEYS = ['image_width', 'image_height'];

  function formatExifRows(exif: Record<string, unknown>): { label: string; value: string }[] {
    const rows: { label: string; value: string }[] = [];
    for (const [key, displayLabel] of Object.entries(EXIF_DISPLAY_KEYS)) {
      const val = exif[key];
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

  function filterExifGroup(
    rows: { label: string; value: string }[],
    keys: string[],
  ): { label: string; value: string }[] {
    const labels = keys.map((k) => EXIF_DISPLAY_KEYS[k]).filter(Boolean);
    return rows.filter((r) => labels.includes(r.label));
  }

  let {
    node,
    kgNode,
    onClose,
    onNavigate,
  }: {
    node: CanvasNode | null;
    kgNode: KGNode | null;
    onClose: () => void;
    onNavigate?: (nodeId: string) => void;
  } = $props();

  const exifCache = new Map<string, { label: string; value: string }[] | null>();
  let fetchedExifNodeId = $state<string | null>(null);
  let fetchedExifRows = $state<{ label: string; value: string }[]>([]);
  let fullscreenUrl = $state<string | null>(null);
  let deleting = $state(false);
  let personPhotoErrors = $state(new Set<string>());
  let activeTab = $state<'details' | 'insights' | 'connections'>('details');

  let convMessages = $state<ChatMessage[]>([]);
  let convLoading = $state(false);
  let convDeleting = $state(false);

  function isConversation(n: CanvasNode | null): boolean {
    return n?.kind === 'conversation';
  }

  function conversationSlug(title: string): string {
    return (title || 'untitled')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48) || 'untitled';
  }

  function formatConversationDate(ms: number | undefined): string {
    if (!ms) return '';
    const d = new Date(ms > 1e12 ? ms : ms * 1000);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function conversationLinkedImages(messages: ChatMessage[]): string[] {
    const urls: string[] = [];
    for (const m of messages) {
      if (m.imageUrls) for (const u of m.imageUrls) urls.push(u);
    }
    return urls;
  }

  async function handleDeleteConversation(id: string) {
    if (convDeleting) return;
    convDeleting = true;
    try {
      await syncClient.deleteConversation(id);
      graphStore.loadConversations();
      handleClose();
    } catch (err) {
      console.error('[NodeOverlay] Delete conversation failed:', err);
    } finally {
      convDeleting = false;
    }
  }

  async function fetchExifForNode(nodeId: string, fileSource: string) {
    if (exifCache.has(nodeId)) {
      fetchedExifRows = exifCache.get(nodeId) ?? [];
      fetchedExifNodeId = nodeId;
      return;
    }
    try {
      const resp = await fetch(`/api/kg${API.kg.photoExif(fileSource)}`);
      if (!resp.ok) {
        exifCache.set(nodeId, null);
        return;
      }
      const exif = (await resp.json()) as Record<string, unknown>;
      const rows = formatExifRows(exif);
      exifCache.set(nodeId, rows);
      fetchedExifRows = rows;
      fetchedExifNodeId = nodeId;
    } catch {
      exifCache.set(nodeId, null);
    }
  }

  let neighborNodes = $derived.by<KGNode[]>(() => {
    const id = node?.id;
    if (!id) return [];
    const nbrEdges = graphStore.edges.filter(
      (e) => e.source === id || e.target === id,
    );
    const nbrIds = new Set(
      nbrEdges.flatMap((e) => [e.source, e.target]).filter((nid) => nid !== id),
    );
    return graphStore.nodes.filter((n) => nbrIds.has(n.id));
  });

  let persons = $derived(neighborNodes.filter((n) => classifyKind(n) === 'person'));
  let locations = $derived(neighborNodes.filter((n) => classifyKind(n) === 'location'));
  let events = $derived(neighborNodes.filter((n) => classifyKind(n) === 'event'));
  let others = $derived(
    neighborNodes.filter(
      (n) => !['person', 'location', 'event'].includes(classifyKind(n)),
    ),
  );

  /** Parse a date from any known photo-timestamp property (mirrors Layout.parseNodeDate). */
  function parsePhotoDate(n: KGNode): Date | null {
    const p = n.properties ?? {};
    const raw =
      p.date_taken_friendly ??
      p.datetime_original ??
      p.created_at ??
      p.timestamp ??
      p.date_taken ??
      p.datetime;
    if (raw === undefined || raw === null) return null;
    if (typeof raw === 'number') {
      const ms = raw > 1e12 ? raw : raw * 1000;
      const d = new Date(ms);
      return isNaN(d.getTime()) ? null : d;
    }
    if (typeof raw === 'string') {
      const m1 = raw.match(/^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?$/);
      if (m1) {
        const [, Y, Mo, D, H, Mi, S] = m1;
        return new Date(`${Y}-${Mo}-${D}T${H}:${Mi}:${S ?? '00'}`);
      }
      const m2 = raw.match(/^(\d{4})-(\d{2})-(\d{2})\s+at\s+(\d{2}):(\d{2})$/);
      if (m2) {
        const [, Y, Mo, D, H, Mi] = m2;
        return new Date(`${Y}-${Mo}-${D}T${H}:${Mi}:00`);
      }
      const d = new Date(raw);
      return isNaN(d.getTime()) ? null : d;
    }
    return null;
  }

  function dayKeyOf(d: Date): string {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
      d.getDate(),
    ).padStart(2, '0')}`;
  }

  function monthKeyOf(d: Date): string {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  }

  function dayLabelFor(d: Date): string {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const target = new Date(d);
    target.setHours(0, 0, 0, 0);
    const diffDays = Math.round((today.getTime() - target.getTime()) / 86_400_000);
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    return target.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  /** All photo nodes from the same month as the current node, sorted chronologically. */
  let sameDayPhotos = $derived.by<KGNode[]>(() => {
    const id = node?.id;
    if (!id || !kgNode) return [];
    const currentDate = parsePhotoDate(kgNode);
    if (!currentDate) return [];
    const monthKey = monthKeyOf(currentDate);
    return graphStore.nodes
      .filter((n) => {
        if (n.id === id) return false;
        if (classifyKind(n) !== 'photo') return false;
        const sourceId = n.properties?.source_id ?? n.properties?.file_path;
        if (!sourceId || sourceId === 'manual_creation') return false;
        const d = parsePhotoDate(n);
        return d !== null && monthKeyOf(d) === monthKey;
      })
      .sort((a, b) => {
        const da = parsePhotoDate(a);
        const db = parsePhotoDate(b);
        return (da?.getTime() ?? 0) - (db?.getTime() ?? 0);
      });
  });

  /** Combined list including the current photo, for index tracking. */
  let sameDayPhotosWithCurrent = $derived.by<KGNode[]>(() => {
    if (!kgNode) return [];
    const currentDate = parsePhotoDate(kgNode);
    if (!currentDate) return [];
    const monthKey = monthKeyOf(currentDate);
    return graphStore.nodes
      .filter((n) => {
        if (classifyKind(n) !== 'photo') return false;
        const sourceId = n.properties?.source_id ?? n.properties?.file_path;
        if (!sourceId || sourceId === 'manual_creation') return false;
        const d = parsePhotoDate(n);
        return d !== null && monthKeyOf(d) === monthKey;
      })
      .sort((a, b) => {
        const da = parsePhotoDate(a);
        const db = parsePhotoDate(b);
        return (da?.getTime() ?? 0) - (db?.getTime() ?? 0);
      });
  });

  /** Photos grouped by day, for the filmstrip with dividers. */
  let monthPhotosByDay = $derived.by<{ dayKey: string; label: string; photos: KGNode[] }[]>(() => {
    if (!sameDayPhotosWithCurrent.length) return [];
    const groups: { dayKey: string; label: string; photos: KGNode[] }[] = [];
    for (const n of sameDayPhotosWithCurrent) {
      const d = parsePhotoDate(n);
      if (!d) continue;
      const dk = dayKeyOf(d);
      let group = groups.find((g) => g.dayKey === dk);
      if (!group) {
        group = { dayKey: dk, label: dayLabelFor(d), photos: [] };
        groups.push(group);
      }
      group.photos.push(n);
    }
    return groups;
  });

  let currentMonthIndex = $derived(
    node ? sameDayPhotosWithCurrent.findIndex((n) => n.id === node.id) : -1,
  );

  function photoThumbUrl(n: KGNode): string {
    const existing = graphStore.photoImages[n.id];
    if (existing) return existing;
    const sourceId = n.properties?.source_id ?? n.properties?.file_path;
    return `${KG_API_PROXY_BASE}${API.kg.photoImageThumb(String(sourceId))}`;
  }

  function navigateToPhoto(n: KGNode) {
    onNavigate?.(n.id);
  }

  function navigateByOffset(offset: number) {
    if (!sameDayPhotosWithCurrent.length || currentMonthIndex < 0) return;
    const newIndex = currentMonthIndex + offset;
    if (newIndex < 0 || newIndex >= sameDayPhotosWithCurrent.length) return;
    const target = sameDayPhotosWithCurrent[newIndex];
    if (target) navigateToPhoto(target);
  }

  let fileName = $derived(
    (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string) ??
      node?.id ??
      'Photo',
  );

  let descriptionContent = $derived(
    (kgNode?.properties?.description as string) ??
      (kgNode?.properties?.summary as string) ??
      null,
  );

  const docContentCache = new Map<string, string>();
  let fetchedDocNodeId = $state<string | null>(null);
  let fetchedDocContent = $state<string | null>(null);
  let docLoading = $state(false);
  let docError = $state<string | null>(null);

  async function fetchDocContentForNode(nodeId: string, fileSource: string) {
    if (docContentCache.has(nodeId)) {
      fetchedDocContent = docContentCache.get(nodeId) ?? null;
      fetchedDocNodeId = nodeId;
      return;
    }
    docLoading = true;
    docError = null;
    try {
      const docId = await lightragClient.resolveDocumentId(fileSource);
      if (!docId) {
        docContentCache.set(nodeId, '');
        fetchedDocContent = null;
        fetchedDocNodeId = nodeId;
        return;
      }
      const data = await lightragClient.getDocumentFullContent(docId);
      const content = data?.content ?? '';
      docContentCache.set(nodeId, content);
      fetchedDocContent = content || null;
      fetchedDocNodeId = nodeId;
    } catch (err) {
      console.error('[NodeOverlay] Failed to fetch document content:', err);
      docError = 'Failed to load description.';
      docContentCache.set(nodeId, '');
      fetchedDocContent = null;
      fetchedDocNodeId = nodeId;
    } finally {
      docLoading = false;
    }
  }

  let locationText = $derived(
    locations.length > 0
      ? getNodeName(locations[0])
      : ((kgNode?.properties?.location as string) ?? (node?.properties?.location as string) ?? null),
  );
  let cityText = $derived.by(() => {
    if (!locationText) return null;
    return locationText.split(',')[0]?.trim() ?? null;
  });
  let stateText = $derived.by(() => {
    if (!locationText) return null;
    const parts = locationText.split(',');
    return parts.length > 1 ? parts.slice(1).join(',').trim() : null;
  });
  let dateText = $derived.by(() => {
    const rawRaw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
    // `created_at` can be a Unix epoch integer (notes), in which case the
    // `as string` cast above lies. Coerce to a string before regex/Date so
    // `.match` never throws on a number; treat null/empty as absent.
    const raw = rawRaw == null ? null : String(rawRaw);
    if (!raw) return null;

    const friendlyMatch = raw.match(/^(\d{4}-\d{2}-\d{2}) at (\d{2}:\d{2})/);
    if (friendlyMatch) {
      return `${friendlyMatch[1]} · ${friendlyMatch[2]}`;
    }

    const asNum = Number(raw);
    if (!isNaN(asNum) && asNum > 0) {
      const ms = asNum > 1e12 ? asNum : asNum * 1000;
      const parsed = new Date(ms);
      if (!isNaN(parsed.getTime())) {
        return parsed.toLocaleString(undefined, {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        });
      }
    }

    const parsed = new Date(raw);
    if (!isNaN(parsed.getTime())) {
      return parsed.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }

    return raw;
  });

  let dateTimeText = $derived.by(() => {
    const rawRaw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
    // `created_at` can be a Unix epoch integer (notes), in which case the
    // `as string` cast above lies. Coerce to a string before regex/Date so
    // `.match` never throws on a number; treat null/empty as absent.
    const raw = rawRaw == null ? null : String(rawRaw);
    if (!raw) return null;

    let d: Date | null = null;
    const friendlyMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2}) at (\d{2}:\d{2})/);
    if (friendlyMatch) {
      d = new Date(`${friendlyMatch[1]}-${friendlyMatch[2]}-${friendlyMatch[3]}T${friendlyMatch[4]}:00`);
    } else {
      const asNum = Number(raw);
      if (!isNaN(asNum) && asNum > 0) {
        const ms = asNum > 1e12 ? asNum : asNum * 1000;
        d = new Date(ms);
      }
      if (!d || isNaN(d.getTime())) d = new Date(raw);
    }
    if (!d || isNaN(d.getTime())) return null;

    const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    return time;
  });

  let dateLabel = $derived.by(() => {
    const rawRaw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
    // `created_at` can be a Unix epoch integer (notes), in which case the
    // `as string` cast above lies. Coerce to a string before regex/Date so
    // `.match` never throws on a number; treat null/empty as absent.
    const raw = rawRaw == null ? null : String(rawRaw);
    if (!raw) return null;

    let d: Date | null = null;
    const friendlyMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2}) at/);
    if (friendlyMatch) {
      d = new Date(`${friendlyMatch[1]}-${friendlyMatch[2]}-${friendlyMatch[3]}`);
    } else {
      const asNum = Number(raw);
      if (!isNaN(asNum) && asNum > 0) {
        const ms = asNum > 1e12 ? asNum : asNum * 1000;
        d = new Date(ms);
      }
      if (!d || isNaN(d.getTime())) d = new Date(raw);
    }
    if (!d || isNaN(d.getTime())) return null;

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffDays = Math.round((today.getTime() - d.getTime()) / 86_400_000);

    if (diffDays === 0) return 'Photos from Today';
    if (diffDays === 1) return 'Photos from Yesterday';
    if (diffDays > 0 && diffDays < 7) return `Photos from ${diffDays} days ago`;
    if (diffDays < 0 && diffDays > -7) return `Photos from in ${-diffDays} days`;

    if (diffDays > 0) {
      const months = Math.floor(diffDays / 30);
      if (months <= 1) return 'Photos from last month';
      if (months < 12) return `Photos from ${months} months ago`;
      const years = Math.floor(diffDays / 365);
      if (years === 1) return 'Photos from last year';
      return `Photos from ${years} years ago`;
    }

    return 'Photos from ' + d.toLocaleDateString(undefined, {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
    });
  });

  let dayName = $derived.by(() => {
    const rawRaw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
    // `created_at` can be a Unix epoch integer (notes), in which case the
    // `as string` cast above lies. Coerce to a string before regex/Date so
    // `.match` never throws on a number; treat null/empty as absent.
    const raw = rawRaw == null ? null : String(rawRaw);
    if (!raw) return null;

    let d: Date | null = null;
    const friendlyMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2}) at/);
    if (friendlyMatch) {
      d = new Date(`${friendlyMatch[1]}-${friendlyMatch[2]}-${friendlyMatch[3]}`);
    } else {
      const asNum = Number(raw);
      if (!isNaN(asNum) && asNum > 0) {
        const ms = asNum > 1e12 ? asNum : asNum * 1000;
        d = new Date(ms);
      }
      if (!d || isNaN(d.getTime())) d = new Date(raw);
    }
    if (!d || isNaN(d.getTime())) return null;
    return d.toLocaleDateString(undefined, { weekday: 'long' });
  });

  let exifCameraRow = $derived.by(() => {
    const rows = fetchedExifNodeId === node?.id ? fetchedExifRows : [];
    return filterExifGroup(rows, EXIF_CAMERA_KEYS);
  });
  let exifExposureRow = $derived.by(() => {
    const rows = fetchedExifNodeId === node?.id ? fetchedExifRows : [];
    return filterExifGroup(rows, EXIF_EXPOSURE_KEYS);
  });
  let exifImageRow = $derived.by(() => {
    const rows = fetchedExifNodeId === node?.id ? fetchedExifRows : [];
    return filterExifGroup(rows, EXIF_IMAGE_KEYS);
  });
  let exifCameraVal = $derived(exifCameraRow.find((r) => r.label === 'Camera')?.value ?? null);
  let exifLensVal = $derived(exifCameraRow.find((r) => r.label === 'Lens')?.value ?? null);
  let exifDimsVal = $derived.by(() => {
    const w = exifImageRow.find((r) => r.label === 'Width')?.value;
    const h = exifImageRow.find((r) => r.label === 'Height')?.value;
    if (w && h) return `${w}x${h}`;
    if (w) return w;
    if (h) return h;
    return null;
  });

  $effect(() => {
    const id = node?.id;
    if (!id) return;
    const live = imageProcessingStore.getExifSummary(id);
    if (live.length > 0) {
      fetchedExifNodeId = id;
      fetchedExifRows = live;
      return;
    }
    const fileSource =
      (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string);
    if (fileSource) {
      fetchExifForNode(id, fileSource);
    }
  });

  $effect(() => {
    const id = node?.id;
    if (!id) return;
    const fileSource =
      (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string);
    if (fileSource) {
      fetchDocContentForNode(id, fileSource);
    }
  });

  $effect(() => {
    const id = node?.id;
    if (!id || !isConversation(node)) {
      convMessages = [];
      convLoading = false;
      return;
    }
    let cancelled = false;
    convLoading = true;
    convMessages = [];
    syncClient.loadConversation(id).then((msgs) => {
      if (cancelled) return;
      convMessages = msgs;
      convLoading = false;
    });
    return () => { cancelled = true; };
  });

  function openFullscreen(url: string) {
    fullscreenUrl = url;
  }

  function closeFullscreen() {
    fullscreenUrl = null;
  }

  function handleClose() {
    fullscreenUrl = null;
    onClose();
  }

  async function handleDelete() {
    const fileSource =
      (kgNode?.properties?.source_id as string) ??
      (kgNode?.properties?.file_path as string) ??
      node?.id;
    if (!fileSource || deleting) return;
    deleting = true;
    try {
      const docId = await lightragClient.resolveDocumentId(fileSource);
      if (docId) {
        await lightragClient.deleteDocument(docId);
      }
      try {
        await kgApiClient.deletePhotoEntities(fileSource);
      } catch {
        // Best-effort cleanup
      }
      graphStore.refresh();
      fullscreenUrl = null;
      onClose();
    } catch (err) {
      console.error('[NodeOverlay] Delete failed:', err);
    } finally {
      deleting = false;
    }
  }

  function handlePersonImgError(n: KGNode) {
    personPhotoErrors = new Set([...personPhotoErrors, n.id]);
  }

  $effect(() => {
    if (!node) return;
    function onKeydown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        if (fullscreenUrl) {
          fullscreenUrl = null;
        } else {
          handleClose();
        }
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        e.stopPropagation();
        navigateByOffset(-1);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        e.stopPropagation();
        navigateByOffset(1);
      }
    }
    window.addEventListener('keydown', onKeydown);
    return () => window.removeEventListener('keydown', onKeydown);
  });
</script>

{#if node}
  {@const status = imageProcessingStore.statuses[node.id]}
  {@const imageUrl = node.imageUrl ?? graphStore.photoImages[node.id]}
  {@const fullUrl = node.fullUrl ?? (imageUrl ? imageUrl.replace(/([?&]w=)\d+\b/, '$1full') : undefined)}
  {@const isProcessing = status && status.stage !== 'complete' && status.stage !== 'error'}
  {@const isComplete = status?.stage === 'complete'}
  {@const isError = status?.stage === 'error'}
  {@const descText = fetchedDocContent ?? descriptionContent ?? null}
  {@const isNote = node.kind === 'note'}
  {@const isConv = isConversation(node)}
  {@const noteBody = fetchedDocContent ?? descriptionContent ?? node.textContent ?? ''}
  {@const convTitle = (kgNode?.properties?.name as string) ?? (node?.properties?.name as string) ?? node?.id ?? 'Conversation'}
  {@const convCreatedAt = (kgNode?.properties?.createdAt as number) ?? (node?.properties?.createdAt as number) ?? undefined}
  {@const convDateLabel = formatConversationDate(convCreatedAt)}
  {@const convSlug = conversationSlug(convTitle)}
  {@const convTurns = convMessages.filter((m) => m.role === 'user' || m.role === 'assistant').length}
  {@const convLinkedImages = conversationLinkedImages(convMessages)}

  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
  <div class="spatial-scene" data-od-id="overlay-app" onclick={handleClose} role="presentation">
    <div class="scene-inner" data-od-id="overlay-body" onclick={(e) => e.stopPropagation()} role="presentation">

      <header class="topbar" data-od-id="topbar">
        <div class="topbar-left">
          {#if isConv}
            <div class="status-pill is-chat" data-od-id="chat-kind-pill">
              <span class="status-dot"></span>
              <span>Conversation</span>
            </div>
            <span class="filename" data-od-id="chat-filename" title={`conversation-${convSlug}.json`}>
              conversation-{convSlug}.json
            </span>
          {:else if isProcessing || isComplete || isError}
            <div class="status-pill {isProcessing ? 'is-processing' : isComplete ? 'is-complete' : 'is-error'}" data-od-id="status-pill">
              {#if isProcessing}
                <span class="status-dot is-processing" aria-hidden="true"></span>
                <span>{status?.stageLabel ?? 'Processing...'}</span>
              {:else if isComplete}
                <span class="status-dot" aria-hidden="true"></span>
                <span>Processed</span>
              {:else if isError}
                <span class="status-dot is-error" aria-hidden="true"></span>
                <span>{status?.error ?? 'Failed'}</span>
              {/if}
            </div>
            <span class="filename" data-od-id="filename" title={fileName}>
              {#if dateLabel}{dateLabel}{:else}{fileName}{/if}
            </span>
          {:else}
            <span class="filename" data-od-id="filename" title={fileName}>
              {#if dateLabel}{dateLabel}{:else}{fileName}{/if}
            </span>
          {/if}
        </div>
        <button class="close-btn" data-od-id="close-btn" aria-label="Close details (Esc)" onclick={handleClose}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </header>

      <div class="image-stage" data-od-id="main-stage">
        {#if isConv}
          <div class="chat-view" data-od-id="chat-view">
            <div class="chat-view-header" data-od-id="chat-view-header">
              <div class="chat-view-kicker" data-od-id="chat-conv-kicker">
                Conversation{#if convTurns > 0} · {convTurns} {convTurns === 1 ? 'turn' : 'turns'}{/if}
              </div>
              {#if convDateLabel}
                <div class="chat-view-date" data-od-id="chat-conv-date">{convDateLabel}</div>
              {/if}
            </div>

            <div class="chat-view-thread" data-od-id="chat-conv-thread">
              {#if convLoading}
                <div class="note-loading" data-od-id="chat-loading">
                  <span class="spinner"></span> Loading conversation…
                </div>
              {:else if convMessages.length === 0}
                <div class="note-empty">No messages in this conversation.</div>
              {:else}
                {#each convMessages as m, i}
                  {#if m.role === 'user' || m.role === 'assistant'}
                    <div class="cv-msg {m.role === 'user' ? 'cv-user' : 'cv-assistant'}" style="animation-delay:{i * 0.06}s">
                      <div class="cv-msg-label">{m.role === 'user' ? 'You' : 'Assistant'}</div>
                      {#if m.role === 'assistant'}
                        {@html renderMarkdown(m.content)}
                      {:else}
                        <div class="cv-msg-text">{m.content}</div>
                      {/if}
                    </div>
                  {/if}
                {/each}
              {/if}
            </div>

            {#if convLinkedImages.length > 0}
              <div class="chat-view-linked" data-od-id="chat-view-linked">
                <span class="chat-view-linked-label">Linked photos</span>
                <div class="chat-view-thumbs" data-od-id="chat-conv-thumbs">
                  {#each convLinkedImages as src}
                    <img {src} alt="" />
                  {/each}
                </div>
              </div>
            {/if}

            <div class="action-area" data-od-id="chat-action-area">
              <button class="btn-delete" data-od-id="btn-delete-chat" disabled={convDeleting} onclick={() => handleDeleteConversation(node.id)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                Delete Conversation
              </button>
            </div>
          </div>
        {:else if isNote}
          <div class="note-reader" data-od-id="note-reader">
            {#if noteBody}
              <div class="note-content" data-od-id="note-content">
                {@html renderMarkdown(noteBody)}
              </div>
            {:else if docLoading}
              <div class="note-loading" data-od-id="note-loading">
                <span class="spinner"></span> Loading note…
              </div>
            {:else if docError}
              <div class="note-error" data-od-id="note-error">{docError}</div>
            {:else}
              <div class="note-empty">No content available.</div>
            {/if}
            {#if docLoading && noteBody}
              <div class="note-loading note-loading-inline" data-od-id="note-loading-inline">
                <span class="spinner"></span> Loading full note…
              </div>
            {/if}
          </div>
        {:else}
          <div class="image-frame" data-od-id="image-viewer">
            {#if fullUrl ?? imageUrl}
              <img class="photo" data-od-id="main-photo"
                   src={fullUrl ?? imageUrl!}
                   alt={fileName}
                   onclick={() => openFullscreen(fullUrl ?? imageUrl!)}>
            {:else}
              <div class="photo-placeholder" data-od-id="photo-placeholder">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.5-3.5a2 2 0 0 0-2.8 0L3 21"/>
                </svg>
              </div>
            {/if}

            {#if locationText || dateTimeText}
              <div class="image-tags" data-od-id="viewer-badges">
                {#if locationText}
                  <span class="tag tag-loc" data-od-id="time-badge">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></svg>
                    {cityText ?? locationText}
                  </span>
                {/if}
                {#if dateTimeText}
                  <span class="tag tag-date" data-od-id="date-badge">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
                    {dateTimeText}
                  </span>
                {/if}
              </div>
            {/if}
          </div>

          {#if sameDayPhotosWithCurrent.length > 1}
            <div class="filmstrip" data-od-id="filmstrip">
              <button class="filmstrip-nav filmstrip-nav-prev" data-od-id="filmstrip-prev"
                aria-label="Previous photo"
                disabled={currentMonthIndex <= 0}
                onclick={() => navigateByOffset(-1)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
              </button>
              <div class="filmstrip-track" data-od-id="filmstrip-track">
                {#each monthPhotosByDay as group (group.dayKey)}
                  <div class="filmstrip-day-group" data-od-id="filmstrip-day-{group.dayKey}">
                    <div class="filmstrip-day-label">{group.label}</div>
                    <div class="filmstrip-day-photos">
                      {#each group.photos as n (n.id)}
                        <button class="filmstrip-thumb {n.id === node?.id ? 'is-active' : ''}"
                          data-od-id="filmstrip-thumb-{n.id}"
                          aria-label="Photo {getNodeName(n)}"
                          onclick={() => navigateToPhoto(n)}>
                          <img src={photoThumbUrl(n)} alt={getNodeName(n)} loading="lazy" />
                        </button>
                      {/each}
                    </div>
                  </div>
                {/each}
              </div>
              <button class="filmstrip-nav filmstrip-nav-next" data-od-id="filmstrip-next"
                aria-label="Next photo"
                disabled={currentMonthIndex < 0 || currentMonthIndex >= sameDayPhotosWithCurrent.length - 1}
                onclick={() => navigateByOffset(1)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>
              </button>
            </div>
          {/if}
        {/if}
      </div>

      <aside class="sidebar" data-od-id="details-sidebar">
        <nav class="tabs" data-od-id="sidebar-tabs">
          <button class="tab {activeTab === 'details' ? 'active' : ''}" data-od-id="tab-details" onclick={() => (activeTab = 'details')}>Details</button>
          <button class="tab {activeTab === 'insights' ? 'active' : ''}" data-od-id="tab-insights" onclick={() => (activeTab = 'insights')}>AI Insights</button>
          {#if others.length > 0}
            <button class="tab {activeTab === 'connections' ? 'active' : ''}" data-od-id="tab-connections" onclick={() => (activeTab = 'connections')}>Related</button>
          {/if}
        </nav>

        <div class="sidebar-content" data-od-id="sidebar-content">

          {#if activeTab === 'details'}
            <div class="description-panel" data-od-id="sec-description">
              <div class="description-label">Description</div>
              {#if docLoading}
                <div class="loading-indicator" data-od-id="desc-loading">
                  <span class="spinner"></span> Loading...
                </div>
              {:else if docError}
                <div class="error-text" data-od-id="desc-error">{docError}</div>
              {:else if descText}
                <div class="description-text">{@html renderMarkdown(descText)}</div>
              {:else}
                <div class="empty-text">No description available.</div>
              {/if}
            </div>

            <div class="data-row" data-od-id="data-row-details">
              {#if locationText}
                <section class="data-panel" data-od-id="sec-location">
                  <div class="panel-label">Location</div>
                  <div class="chip-row">
                    <span class="chip chip-loc" data-od-id="loc-chip">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></svg>
                      {locationText}
                    </span>
                  </div>
                </section>
              {/if}

              {#if persons.length > 0}
                <section class="data-panel" data-od-id="sec-people">
                  <div class="panel-label">People<span class="count">{persons.length}</span></div>
                  <div class="people-row">
                    {#each persons as n (n.id)}
                      <div class="person" data-od-id="person-{n.id}" tabindex="0" role="button">
                        {#if isPersonNamed(n) && !personPhotoErrors.has(n.id)}
                          <img class="person-thumb" src={resolvePersonThumbUrl(n)} alt={getNodeName(n)} onerror={() => handlePersonImgError(n)}>
                        {:else if isPersonNamed(n)}
                          <div class="person-initials">{getInitials(getNodeName(n))}</div>
                        {:else}
                          <div class="person-initials person-unknown">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/></svg>
                          </div>
                        {/if}
                        <span class="person-name">{isPersonNamed(n) ? getNodeName(n) : 'Unknown'}</span>
                      </div>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if events.length > 0}
                <section class="data-panel" data-od-id="sec-events">
                  <div class="panel-label">Events<span class="count">{events.length}</span></div>
                  <div class="chip-row">
                    {#each events as n (n.id)}
                      <span class="chip chip-event" data-od-id="event-{n.id}">
                        <span class="event-dot"></span>
                        {getNodeName(n)}
                      </span>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if !isNote && (exifCameraRow.length > 0 || exifExposureRow.length > 0 || exifImageRow.length > 0)}
                <section class="data-panel exif-panel" data-od-id="sec-exif">
                  <div class="panel-label">Exif</div>

                  {#if exifCameraRow.length > 0}
                    <div class="exif-group">
                      <div class="exif-group-label">Camera</div>
                      <div class="exif-grid">
                        {#each exifCameraRow as row}
                          <div class="exif-row">
                            <span class="exif-label">{row.label}</span>
                            <span class="exif-value">{row.value}</span>
                          </div>
                        {/each}
                      </div>
                    </div>
                  {/if}

                  {#if exifExposureRow.length > 0}
                    <div class="exif-group">
                      <div class="exif-group-label">Exposure</div>
                      <div class="exif-grid">
                        {#each exifExposureRow as row}
                          <div class="exif-row">
                            <span class="exif-label">{row.label}</span>
                            <span class="exif-value" data-od-id="exif-{row.label}">{row.value}</span>
                          </div>
                        {/each}
                      </div>
                    </div>
                  {/if}

                  {#if exifImageRow.length > 0}
                    <div class="exif-group">
                      <div class="exif-group-label">Image</div>
                      <div class="exif-grid">
                        {#each exifImageRow as row}
                          <div class="exif-row">
                            <span class="exif-label">{row.label}</span>
                            <span class="exif-value">{row.value}</span>
                          </div>
                        {/each}
                      </div>
                    </div>
                  {/if}
                </section>
              {/if}
            </div>
          {/if}

          {#if activeTab === 'insights'}
            <div class="data-row" data-od-id="data-row-insights">
              <section class="data-panel" data-od-id="sec-insights-entities">
                <div class="panel-label">Entities<span class="count">{others.length}</span></div>
                {#if others.length > 0}
                  <div class="chip-row">
                    {#each others as n, i (n.id)}
                      <span class="chip chip-entity {i === 0 ? 'chip-accent' : ''}" data-od-id="entity-{n.id}">{getNodeName(n)}</span>
                    {/each}
                  </div>
                {:else}
                  <div class="empty-text">No entities detected.</div>
                {/if}
              </section>

              {#if locations.length > 0}
                <section class="data-panel" data-od-id="sec-insights-locations">
                  <div class="panel-label">Locations<span class="count">{locations.length}</span></div>
                  <div class="chip-row">
                    {#each locations as n (n.id)}
                      <span class="chip chip-loc" data-od-id="loc-pill-{n.id}">{getNodeName(n)}</span>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if events.length > 0}
                <section class="data-panel" data-od-id="sec-insights-events">
                  <div class="panel-label">Events<span class="count">{events.length}</span></div>
                  <div class="chip-row">
                    {#each events as n (n.id)}
                      <span class="chip chip-event" data-od-id="event-pill-{n.id}">{getNodeName(n)}</span>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if persons.length > 0}
                <section class="data-panel" data-od-id="sec-insights-people">
                  <div class="panel-label">People<span class="count">{persons.length}</span></div>
                  <div class="people-row">
                    {#each persons as n (n.id)}
                      <div class="person" data-od-id="person-pill-{n.id}" tabindex="0" role="button">
                        {#if isPersonNamed(n) && !personPhotoErrors.has(n.id)}
                          <img class="person-thumb" src={resolvePersonThumbUrl(n)} alt={getNodeName(n)} onerror={() => handlePersonImgError(n)}>
                        {:else if isPersonNamed(n)}
                          <div class="person-initials">{getInitials(getNodeName(n))}</div>
                        {:else}
                          <div class="person-initials person-unknown">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/></svg>
                          </div>
                        {/if}
                        <span class="person-name">{isPersonNamed(n) ? getNodeName(n) : 'Unknown'}</span>
                      </div>
                    {/each}
                  </div>
                </section>
              {/if}
            </div>
          {/if}

          {#if activeTab === 'connections' && others.length > 0}
            <div class="data-row" data-od-id="data-row-connections">
              <section class="data-panel exif-panel" data-od-id="sec-connections">
                <div class="panel-label">Connected Entities<span class="count">{others.length}</span></div>
                <div class="exif-grid">
                  {#each others as n (n.id)}
                    <div class="exif-row connection-item" data-od-id="conn-{n.id}">
                      <span class="exif-label connection-kind">{classifyKind(n)}</span>
                      <span class="exif-value connection-name">{getNodeName(n)}</span>
                    </div>
                  {/each}
                </div>
              </section>
            </div>
          {/if}

        </div>
      </aside>

      <div class="action-area" data-od-id="sidebar-footer">
        <button class="btn-delete" data-od-id="btn-delete" onclick={handleDelete} disabled={deleting}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          {deleting ? 'Deleting...' : isNote ? 'Delete Note' : 'Delete Photo'}
        </button>
      </div>
    </div>
  </div>

  {/if}

  {#if fullscreenUrl}
    <div class="fullscreen-overlay" data-od-id="fullscreen-overlay" onclick={closeFullscreen} role="presentation">
      <img src={fullscreenUrl} alt={fileName} onclick={(e) => e.stopPropagation()} role="presentation">
      <button class="fullscreen-close" data-od-id="fullscreen-close" aria-label="Close fullscreen" onclick={closeFullscreen}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
  {/if}

<style>
  /* ════════════════════════════════════════════════════════════════════
     Tokens — oklch system matching the CanvasView prototype
     ════════════════════════════════════════════════════════════════════ */
  :root {
    --bg:          oklch(6% 0.02 260);
    --fg:          oklch(90% 0.005 250);
    --muted:       oklch(65% 0.02 255);
    --faint:       oklch(55% 0.02 255);
    --accent:      oklch(82% 0.14 210);
    --accent-dim:  oklch(82% 0.14 210 / 18%);
    --accent-purple: oklch(72% 0.16 295);
    --accent-purple-dim: oklch(72% 0.16 295 / 18%);
    --success:     oklch(72% 0.15 150);
    --danger:      oklch(62% 0.20 18);
    --glass:       oklch(16% 0.015 255 / 45%);
    --glass-light: oklch(20% 0.015 255 / 30%);
    --hairline:    oklch(50% 0.03 255 / 8%);

    --font-mono:   ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
    --font-sans:   -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    --font-display: 'Iowan Old Style', 'Charter', Georgia, 'SF Pro Display', serif;
    --font-body:    -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
  }

  /* Focus-visible — keyboard accessibility */
  .close-btn:focus-visible,
  .btn-delete:focus-visible,
  .filmstrip-nav:focus-visible,
  .filmstrip-thumb:focus-visible,
  .tab:focus-visible,
  .person:focus-visible,
  .chip:focus-visible,
  .fullscreen-close:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: inherit;
  }
  .filmstrip-thumb:focus-visible { outline-offset: 1px; }
  .tab:focus-visible { outline-offset: -2px; }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  /* ════════════════════════════════════════════════════════════════════
     Spatial scene — the overlay backdrop with radial gradients + glass
     ════════════════════════════════════════════════════════════════════ */
  .spatial-scene {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    padding: 3vh 2vw 2vh;
    perspective: 1200px;
    perspective-origin: 50% 40%;
    overflow-y: auto;
    overflow-x: hidden;
    background:
      radial-gradient(ellipse 90% 70% at 50% 35%, oklch(14% 0.06 270 / 40%), transparent),
      radial-gradient(ellipse 60% 50% at 30% 80%, oklch(10% 0.04 210 / 30%), transparent),
      radial-gradient(ellipse 50% 40% at 80% 20%, oklch(8% 0.03 180 / 20%), transparent),
      oklch(6% 0.02 260 / 92%);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    color: var(--fg);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    animation: fade-in 0.25s ease;
  }
  @keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }

  .scene-inner {
    position: relative;
    width: 100%;
    max-width: 920px;
    transform-style: preserve-3d;
    will-change: transform;
  }

  /* ════════════════════════════════════════════════════════════════════
     Topbar — status pill, filename, close button
     ════════════════════════════════════════════════════════════════════ */
  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    margin-bottom: 2vh;
    transform: translateZ(20px);
  }
  .topbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    flex: 1;
  }

  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    background: var(--glass);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border-radius: 100px;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    flex-shrink: 0;
  }
  .status-pill.is-complete { color: var(--success); }
  .status-pill.is-processing { color: oklch(78% 0.15 60); }
  .status-pill.is-error { color: var(--danger); }
  .status-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: currentColor;
  }
  .status-dot.is-processing {
    animation: pulse 1.4s ease-in-out infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

  .filename {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--muted);
    letter-spacing: 0.06em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .close-btn {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: oklch(20% 0.02 255 / 70%);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border-radius: 50%;
    cursor: pointer;
    color: var(--fg);
    border: 2px solid oklch(60% 0.03 255 / 55%);
    box-shadow: 0 0 0 1px oklch(0% 0 0 / 20%), 0 4px 12px oklch(0% 0 0 / 25%);
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .close-btn:hover {
    color: var(--danger);
    background: oklch(62% 0.20 18 / 18%);
    border-color: oklch(62% 0.20 18 / 70%);
    box-shadow: 0 0 0 1px oklch(0% 0 0 / 20%), 0 4px 16px oklch(62% 0.20 18 / 25%);
    transform: scale(1.08);
  }
  .close-btn:active { transform: scale(0.95); }
  .close-btn svg { width: 18px; height: 18px; transition: transform 0.2s; }
  .close-btn:hover svg { transform: rotate(90deg); }

  /* ════════════════════════════════════════════════════════════════════
     Image stage — the hero photo with tags
     ════════════════════════════════════════════════════════════════════ */
  .image-stage {
    position: relative;
    width: 100%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    margin-bottom: 1.5vh;
    transform: translateZ(0px);
    gap: 14px;
  }

  .image-frame {
    position: relative;
    max-width: 840px;
    width: 100%;
    margin: 0 auto;
    border-radius: 16px;
    overflow: hidden;
    box-shadow:
      0 40px 100px oklch(0% 0 0 / 60%),
      0 0 60px oklch(82% 0.14 210 / 8%),
      0 0 0 1px oklch(50% 0.03 255 / 10%);
    cursor: pointer;
    transition: transform 0.6s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.4s;
  }
  .image-frame:hover {
    transform: translateY(-4px);
    box-shadow:
      0 50px 120px oklch(0% 0 0 / 70%),
      0 0 80px oklch(82% 0.14 210 / 12%),
      0 0 0 1px oklch(82% 0.14 210 / 20%);
  }
  .image-frame .photo {
    display: block;
    width: 100%;
    height: auto;
    max-height: 65vh;
    object-fit: cover;
    cursor: zoom-in;
  }

  .photo-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    min-height: 300px;
    background: var(--glass);
    color: var(--faint);
  }
  .photo-placeholder svg { width: 64px; height: 64px; }

  .image-tags {
    position: absolute;
    bottom: 14px;
    left: 14px;
    display: flex;
    gap: 6px;
    z-index: 2;
    pointer-events: none;
  }
  .tag {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 5px 10px;
    background: oklch(6% 0.01 255 / 60%);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-radius: 100px;
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    max-width: 240px;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .tag svg { width: 12px; height: 12px; flex-shrink: 0; }
  .tag-loc { color: oklch(78% 0.13 155); }
  .tag-date { color: var(--accent); }

  /* ════════════════════════════════════════════════════════════════════
     Filmstrip — same-day photos navigation
     ════════════════════════════════════════════════════════════════════ */
  .filmstrip {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    padding: 8px 0;
  }
  .filmstrip-nav {
    width: 54px; height: 54px;
    margin-top: 10px;
    flex-shrink: 0;
    border-radius: 50%;
    background: var(--glass);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border: 1px solid var(--hairline);
    color: var(--muted);
    display: grid; place-items: center;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .filmstrip-nav:hover:not(:disabled) {
    background: var(--glass-light);
    color: var(--fg);
  }
  .filmstrip-nav:active:not(:disabled) { transform: scale(0.92); }
  .filmstrip-nav:disabled { opacity: 0.3; cursor: default; }
  .filmstrip-nav svg { width: 26px; height: 26px; }

  .filmstrip-track {
    flex: 1;
    display: flex;
    gap: 6px;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: thin;
    scrollbar-color: var(--accent-dim) transparent;
    padding: 2px 0;
    min-width: 0;
  }
  .filmstrip-track::-webkit-scrollbar { height: 4px; }
  .filmstrip-track::-webkit-scrollbar-thumb { background: var(--accent-dim); border-radius: 2px; }

  .filmstrip-thumb {
    flex-shrink: 0;
    width: 82px; height: 82px;
    border-radius: 8px;
    overflow: hidden;
    border: 2px solid transparent;
    background: var(--glass);
    cursor: pointer;
    padding: 0;
    transition: border-color 0.15s ease, transform 0.15s ease;
  }
  .filmstrip-thumb img {
    width: 100%; height: 100%;
    object-fit: cover;
    display: block;
  }
  .filmstrip-thumb:hover { transform: translateY(-2px); border-color: var(--hairline); }
  .filmstrip-thumb.is-active {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }

  .filmstrip-day-group {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }
  .filmstrip-day-photos {
    display: flex;
    gap: 6px;
  }
  .filmstrip-day-group + .filmstrip-day-group {
    border-left: 1px solid var(--hairline);
    padding-left: 8px;
    margin-left: 4px;
  }
  .filmstrip-day-label {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 14px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    white-space: nowrap;
    padding: 2px 0;
  }

  /* ════════════════════════════════════════════════════════════════════
     Description panel — glassmorphism card
     ════════════════════════════════════════════════════════════════════ */
  .description-panel {
    position: relative;
    max-width: 920px;
    width: 100%;
    margin: 0 auto 1.5vh;
    padding: 18px 24px;
    background: var(--glass);
    backdrop-filter: blur(24px) saturate(1.4);
    -webkit-backdrop-filter: blur(24px) saturate(1.4);
    border-radius: 14px;
    box-shadow:
      0 20px 60px oklch(0% 0 0 / 30%),
      0 0 0 1px oklch(50% 0.03 255 / 6%);
    transform: translateZ(10px);
  }
  .description-label {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 8px;
    opacity: 0.95;
  }
  .description-text {
    font-size: 15px;
    line-height: 1.75;
    color: oklch(86% 0.004 250);
    max-height: 240px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--accent-dim) transparent;
  }
  .description-text::-webkit-scrollbar { width: 2px; }
  .description-text::-webkit-scrollbar-thumb { background: var(--accent-dim); }
  .description-text :global(.overlay-p) { margin-bottom: 8px; }
  .description-text :global(.overlay-p:last-child) { margin-bottom: 0; }
  .description-text :global(.overlay-code) {
    background: oklch(20% 0.02 255 / 40%);
    border-radius: 8px;
    padding: 10px;
    overflow-x: hidden;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--fg);
    margin-bottom: 8px;
  }

  /* ════════════════════════════════════════════════════════════════════
     Data row + panels — people, events, locations, entities, exif
     ════════════════════════════════════════════════════════════════════ */
  .data-row {
    display: flex;
    gap: 12px;
    max-width: 920px;
    width: 100%;
    margin: 0 auto;
    flex-wrap: wrap;
    transform: translateZ(5px);
  }
  .data-panel {
    flex: 1;
    min-width: 200px;
    padding: 14px 18px;
    background: var(--glass-light);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border-radius: 12px;
    box-shadow: 0 0 0 1px oklch(50% 0.03 255 / 5%);
  }
  .exif-panel {
    flex-basis: 100%;
    min-width: 100%;
    padding: 16px 20px;
  }

  .panel-label {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--accent-dim);
  }
  .panel-label .count {
    color: var(--fg);
    font-weight: 700;
    margin-left: 6px;
  }

  /* People */
  .people-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .person {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 4px 8px 4px 4px;
    border-radius: 100px;
    transition: background 0.2s;
    cursor: pointer;
  }
  .person:hover { background: oklch(20% 0.02 255 / 30%); }
  .person-thumb {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    object-fit: cover;
    flex-shrink: 0;
    filter: saturate(0.8);
  }
  .person-initials {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 700;
    font-family: var(--font-mono);
    background: oklch(30% 0.02 255 / 60%);
    color: var(--fg);
  }
  .person-unknown {
    background: oklch(20% 0.02 255 / 40%);
    color: var(--faint);
  }
  .person-unknown svg { width: 14px; height: 14px; }
  .person-name {
    font-size: 13px;
    color: var(--fg);
    white-space: nowrap;
  }

  /* Chips */
  .chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    border-radius: 100px;
    font-family: var(--font-mono);
    font-size: 12px;
    cursor: pointer;
    transition: filter 0.15s;
    letter-spacing: 0.02em;
  }
  .chip:hover { filter: brightness(1.25); }
  .chip-loc {
    color: oklch(78% 0.13 155);
    background: oklch(72% 0.13 155 / 8%);
  }
  .chip-event {
    color: oklch(80% 0.13 70);
    background: oklch(74% 0.13 70 / 8%);
  }
  .chip-entity {
    color: var(--muted);
    background: oklch(20% 0.02 255 / 25%);
  }
  .chip-entity:hover { color: var(--fg); }
  .chip-accent {
    color: var(--accent);
    background: var(--accent-dim);
  }
  .chip svg { width: 12px; height: 12px; flex-shrink: 0; }

  .event-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: currentColor;
    flex-shrink: 0;
  }

  /* ════════════════════════════════════════════════════════════════════
     EXIF grid
     ════════════════════════════════════════════════════════════════════ */
  .exif-group {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .exif-group + .exif-group {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--hairline);
  }
  .exif-group-label {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 6px;
  }
  .exif-grid { display: grid; gap: 0; }
  .exif-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 5px 8px;
    gap: 12px;
    border-radius: 6px;
    transition: background 0.15s;
  }
  .exif-row:hover { background: oklch(50% 0.03 255 / 6%); }
  .exif-label {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .exif-value {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--fg);
    text-align: right;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }

  .connection-kind {
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .connection-name {
    font-size: 12px;
  }

  /* ════════════════════════════════════════════════════════════════════
     Sidebar — tabs (details/insights/connections) restyled to fit aesthetic
     ════════════════════════════════════════════════════════════════════ */
  .sidebar {
    width: 100%;
    max-width: 920px;
    margin: 0 auto 1.5vh;
    background: var(--glass);
    backdrop-filter: blur(24px) saturate(1.4);
    -webkit-backdrop-filter: blur(24px) saturate(1.4);
    border-radius: 14px;
    box-shadow:
      0 20px 60px oklch(0% 0 0 / 30%),
      0 0 0 1px oklch(50% 0.03 255 / 6%);
    transform: translateZ(8px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .tabs {
    display: flex;
    border-bottom: 1px solid var(--hairline);
    flex-shrink: 0;
    padding: 0 4px;
  }
  .tab {
    padding: 14px 16px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    transition: color 0.15s ease, border-color 0.15s ease;
    margin-bottom: -1px;
  }
  .tab:hover { color: var(--fg); }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .sidebar-content {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 20px;
    scrollbar-width: thin;
    scrollbar-color: var(--accent-dim) transparent;
  }
  .sidebar-content::-webkit-scrollbar { width: 4px; }
  .sidebar-content::-webkit-scrollbar-thumb { background: var(--accent-dim); border-radius: 2px; }

  .loading-indicator {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--muted);
  }
  .spinner {
    width: 14px; height: 14px;
    border: 2px solid var(--hairline);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .error-text { font-size: 13px; color: var(--danger); }
  .empty-text { font-size: 13px; color: var(--faint); font-style: italic; }

  /* ════════════════════════════════════════════════════════════════════
     Action area — delete button
     ════════════════════════════════════════════════════════════════════ */
  .action-area {
    display: flex;
    justify-content: center;
    margin-top: 1.5vh;
    transform: translateZ(15px);
  }
  .btn-delete {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 10px 22px;
    background: oklch(62% 0.20 18 / 10%);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border-radius: 100px;
    border: 1px solid oklch(62% 0.20 18 / 20%);
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    cursor: pointer;
    color: oklch(62% 0.20 18 / 80%);
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .btn-delete:hover:not(:disabled) {
    color: oklch(62% 0.20 18);
    background: oklch(62% 0.20 18 / 18%);
    border-color: oklch(62% 0.20 18 / 50%);
    transform: translateY(-1px);
    box-shadow: 0 8px 24px oklch(62% 0.20 18 / 15%);
  }
  .btn-delete:active:not(:disabled) { transform: translateY(0) scale(0.98); }
  .btn-delete:disabled { opacity: 0.5; cursor: default; }
  .btn-delete svg { width: 13px; height: 13px; opacity: 0.8; }

  /* ════════════════════════════════════════════════════════════════════
     Fullscreen overlay — restyled with new tokens
     ════════════════════════════════════════════════════════════════════ */
  .fullscreen-overlay {
    position: fixed;
    inset: 0;
    z-index: 2000;
    background:
      radial-gradient(ellipse 80% 60% at 50% 50%, oklch(10% 0.02 260 / 80%), oklch(3% 0.01 260 / 96%));
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    display: flex;
    align-items: center;
    justify-content: center;
    animation: fade-in 0.2s ease;
  }
  .fullscreen-overlay img {
    max-width: 92%;
    max-height: 92%;
    border-radius: 8px;
    box-shadow: 0 30px 100px oklch(0% 0 0 / 80%);
  }
  .fullscreen-close {
    position: absolute;
    top: 20px; right: 20px;
    width: 40px; height: 40px;
    border-radius: 50%;
    background: oklch(20% 0.02 255 / 70%);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border: 2px solid oklch(60% 0.03 255 / 55%);
    color: var(--fg);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .fullscreen-close:hover {
    color: var(--danger);
    background: oklch(62% 0.20 18 / 18%);
    border-color: oklch(62% 0.20 18 / 70%);
    transform: scale(1.08);
  }
  .fullscreen-close svg { width: 18px; height: 18px; transition: transform 0.2s; }
  .fullscreen-close:hover svg { transform: rotate(90deg); }

  /* ════════════════════════════════════════════════════════════════════
     Note reader — serif reading view, preserved
     ════════════════════════════════════════════════════════════════════ */
  .note-reader {
    width: 100%;
    max-width: 720px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    min-height: 0;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--accent-dim) transparent;
    padding: 8px 0;
  }
  .note-reader::-webkit-scrollbar { width: 4px; }
  .note-reader::-webkit-scrollbar-thumb { background: var(--accent-dim); border-radius: 2px; }

  .note-content {
    width: 100%;
    max-width: 680px;
    background: oklch(92% 0.04 85);
    color: oklch(28% 0.02 60);
    border-radius: 16px;
    padding: 1.5rem 1.75rem;
    box-shadow:
      0 0 0 1px oklch(0% 0 0 / 6%),
      0 20px 60px oklch(0% 0 0 / 45%);
    font-family: var(--font-body);
    font-size: 15px;
    line-height: 1.6;
    overflow-wrap: break-word;
  }
  .note-content :global(h1),
  .note-content :global(h2),
  .note-content :global(h3),
  .note-content :global(h4),
  .note-content :global(h5),
  .note-content :global(h6) {
    font-family: var(--font-display);
    color: oklch(22% 0.02 60);
    line-height: 1.3;
    margin-top: 1.25em;
    margin-bottom: 0.5em;
    font-weight: 600;
  }
  .note-content :global(h1) { font-size: 1.5em; }
  .note-content :global(h2) { font-size: 1.3em; }
  .note-content :global(h3) { font-size: 1.15em; }
  .note-content :global(h4),
  .note-content :global(h5),
  .note-content :global(h6) { font-size: 1em; }
  .note-content :global(h1:first-child),
  .note-content :global(h2:first-child),
  .note-content :global(h3:first-child) { margin-top: 0; }
  .note-content :global(.overlay-p),
  .note-content :global(p) {
    margin: 0 0 0.85em;
  }
  .note-content :global(.overlay-p:last-child),
  .note-content :global(p:last-child) { margin-bottom: 0; }
  .note-content :global(ul),
  .note-content :global(ol) {
    margin: 0 0 0.85em;
    padding-left: 1.4em;
  }
  .note-content :global(li) { margin-bottom: 0.25em; }
  .note-content :global(li:last-child) { margin-bottom: 0; }
  .note-content :global(blockquote) {
    margin: 0 0 0.85em;
    padding: 0.25em 0.9em;
    border-left: 3px solid oklch(43% 0.02 60 / 25%);
    color: oklch(40% 0.02 60);
    font-style: italic;
  }
  .note-content :global(a) {
    color: oklch(55% 0.10 50);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .note-content :global(a:hover) { color: oklch(45% 0.10 50); }
  .note-content :global(strong) { font-weight: 600; color: oklch(22% 0.02 60); }
  .note-content :global(em) { font-style: italic; }
  .note-content :global(.overlay-code),
  .note-content :global(pre) {
    background: oklch(43% 0.02 60 / 8%);
    border-radius: 8px;
    padding: 0.75em 0.9em;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--font-mono);
    font-size: 0.85em;
    color: oklch(28% 0.02 60);
    margin: 0 0 0.85em;
  }
  .note-content :global(.overlay-code:last-child),
  .note-content :global(pre:last-child) { margin-bottom: 0; }
  .note-content :global(code) {
    font-family: var(--font-mono);
    font-size: 0.88em;
    background: oklch(43% 0.02 60 / 8%);
    padding: 0.12em 0.35em;
    border-radius: 4px;
  }
  .note-content :global(.overlay-code code),
  .note-content :global(pre code) {
    background: transparent;
    padding: 0;
  }
  .note-content :global(hr) {
    border: none;
    border-top: 1px solid oklch(43% 0.02 60 / 18%);
    margin: 1.2em 0;
  }
  .note-content :global(img) {
    max-width: 100%;
    border-radius: 8px;
    margin: 0 0 0.85em;
  }
  .note-content :global(table) {
    width: 100%;
    border-collapse: collapse;
    margin: 0 0 0.85em;
    font-size: 0.95em;
  }
  .note-content :global(th),
  .note-content :global(td) {
    border: 1px solid oklch(43% 0.02 60 / 18%);
    padding: 0.4em 0.6em;
    text-align: left;
  }
  .note-content :global(th) { background: oklch(43% 0.02 60 / 8%); font-weight: 600; }

  .note-loading,
  .note-error,
  .note-empty {
    width: 100%;
    max-width: 680px;
    text-align: center;
    padding: 2.5rem 1rem;
    font-size: 14px;
    color: var(--muted);
  }
  .note-loading { display: inline-flex; align-items: center; gap: 8px; }
  .note-loading-inline {
    width: auto;
    padding: 10px 14px;
    margin-top: 12px;
    background: var(--glass);
    border: 1px solid var(--hairline);
    border-radius: 999px;
    font-size: 12px;
  }
  .note-error { color: var(--danger); }
  .note-empty { font-style: italic; color: var(--faint); }

  /* ════════════════════════════════════════════════════════════════════
     Float-in stagger animations
     ════════════════════════════════════════════════════════════════════ */
  .scene-inner > * {
    animation: float-in 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
  }
  .topbar          { animation-delay: 0s; }
  .image-stage     { animation-delay: 0.08s; }
  .description-panel { animation-delay: 0.16s; }
  .sidebar         { animation-delay: 0.20s; }
  .data-row        { animation-delay: 0.24s; }
  .action-area     { animation-delay: 0.32s; }
  @keyframes float-in {
    from { opacity: 0; transform: translateY(20px) translateZ(-30px); }
    to   { opacity: 1; }
  }

  /* ════════════════════════════════════════════════════════════════════
     Reduced motion
     ════════════════════════════════════════════════════════════════════ */
  @media (prefers-reduced-motion: reduce) {
    .scene-inner > *,
    .spatial-scene,
    .image-frame,
    .close-btn,
    .btn-delete {
      animation: none !important;
      transition-duration: 0.01ms !important;
    }
  }

  /* ════════════════════════════════════════════════════════════════════
     Mobile responsive
     ════════════════════════════════════════════════════════════════════ */
  @media (max-width: 768px) {
    .spatial-scene { padding: 2vh 4vw; perspective: none; }
    .scene-inner { transform: none !important; }
    .image-frame { border-radius: 12px; }
    .image-frame .photo { min-height: 45vh; max-height: 45vh; }
    .description-panel { padding: 14px 18px; border-radius: 12px; }
    .description-text { font-size: 13px; line-height: 1.65; }
    .data-row { flex-direction: column; gap: 8px; }
    .data-panel { min-width: 100%; border-radius: 10px; }
    .topbar { margin-bottom: 1.5vh; }
    .image-stage, .description-panel, .data-row, .action-area, .sidebar {
      transform: none !important;
    }
    .chat-view-thread { max-height: 60vh; }
  }

  /* ════════════════════════════════════════════════════════════════════
     Chat conversation view — message bubbles in a thread
     ════════════════════════════════════════════════════════════════════ */
  .chat-view { max-width: 680px; width: 100%; transform: translateZ(10px); }
  .chat-view-header {
    padding: 1.5vh 0 2vh;
    border-bottom: 1px solid var(--hairline);
    margin-bottom: 1vh;
    transform: translateZ(10px);
  }
  .chat-view-kicker {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 6px;
  }
  .chat-view-date {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--faint);
  }
  .chat-view-thread {
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: 1.5vh 0 2vh;
    max-height: 52vh;
    overflow-y: auto;
    scrollbar-color: var(--accent-dim) transparent;
  }
  .chat-view-thread::-webkit-scrollbar { width: 3px; }
  .chat-view-thread::-webkit-scrollbar-thumb { background: var(--accent-dim); border-radius: 2px; }
  .cv-msg {
    max-width: 82%;
    padding: 14px 18px;
    border-radius: 18px;
    font-size: 15px;
    line-height: 1.55;
    animation: cvFloatIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) backwards;
  }
  .cv-msg.cv-user {
    align-self: flex-end;
    background: oklch(82% 0.14 210 / 12%);
    border: 1px solid oklch(82% 0.14 210 / 18%);
    color: var(--fg);
    border-bottom-right-radius: 4px;
    font-weight: 600;
  }
  .cv-msg.cv-assistant {
    align-self: flex-start;
    background: var(--glass);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border: 1px solid var(--hairline);
    color: oklch(82% 0.008 250);
    border-bottom-left-radius: 4px;
  }
  .cv-msg-text { white-space: pre-wrap; word-break: break-word; }
  .cv-msg-label {
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-bottom: 6px;
    opacity: 0.6;
  }
  .cv-msg.cv-user .cv-msg-label { color: var(--accent); }
  .cv-msg.cv-assistant .cv-msg-label { color: var(--accent-purple); }
  @keyframes cvFloatIn {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .chat-view-linked {
    padding: 1vh 0 1.5vh;
    border-top: 1px solid var(--hairline);
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .chat-view-linked-label {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--faint);
    flex-shrink: 0;
  }
  .chat-view-thumbs {
    display: flex;
    gap: 6px;
  }
  .chat-view-thumbs img {
    width: 40px;
    height: 40px;
    border-radius: 6px;
    object-fit: cover;
    border: 1px solid var(--hairline);
  }
  .status-pill.is-chat { color: var(--accent); }
</style>