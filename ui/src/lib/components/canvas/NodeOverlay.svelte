<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { imageProcessingStore } from '$lib/stores/image-processing.svelte';
  import { API, type KGNode } from '$lib/constants';
  import { lightragClient } from '$lib/services/lightrag-client';
  import { kgApiClient } from '$lib/services/kg-api-client';
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
    const raw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
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
    const raw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
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
    const raw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
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
    const raw =
      (kgNode?.properties?.date_taken_friendly as string) ??
      (kgNode?.properties?.created_at as string) ??
      (node?.properties?.date_taken_friendly as string) ??
      (node?.properties?.created_at as string) ??
      null;
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

  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
  <div class="overlay-app" data-od-id="overlay-app" onclick={handleClose} role="presentation">
    <div class="overlay-body" data-od-id="overlay-body" onclick={(e) => e.stopPropagation()} role="presentation">

      <header class="topbar" data-od-id="topbar">
        <div class="topbar-left">
          {#if isProcessing || isComplete || isError}
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
          {/if}
          <span class="filename" data-od-id="filename" title={fileName}>
            {#if dateLabel}{dateLabel}{:else}{fileName}{/if}
          </span>
        </div>
        <button class="close-btn" data-od-id="close-btn" aria-label="Close details (Esc)" onclick={handleClose}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </header>

      <div class="content" data-od-id="content">

        <main class="stage" data-od-id="main-stage">
          <div class="viewer" data-od-id="image-viewer">
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

            {#if dateTimeText}
              <div class="viewer-badges" data-od-id="viewer-badges">
                {#if dateTimeText}
                  <div class="location-badge" data-od-id="time-badge">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
                    <span>{dateTimeText}</span>
                  </div>
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
        </main>

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
              <section class="section" data-od-id="sec-description">
                <div class="section-header">Description</div>
                {#if docLoading}
                  <div class="loading-indicator" data-od-id="desc-loading">
                    <span class="spinner"></span> Loading...
                  </div>
                {:else if docError}
                  <div class="error-text" data-od-id="desc-error">{docError}</div>
                {:else if descText}
                  <div class="description">{@html renderMarkdown(descText)}</div>
                {:else}
                  <div class="empty-text">No description available.</div>
                {/if}
              </section>

              {#if locationText}
                <section class="section" data-od-id="sec-location">
                  <div class="section-header">Location</div>
                  <div class="location-row">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></svg>
                    <div class="location-text">{locationText}</div>
                  </div>
                </section>
              {/if}

              {#if persons.length > 0}
                <section class="section" data-od-id="sec-people">
                  <div class="section-header">People - {persons.length}</div>
                  <div class="people-grid">
                    {#each persons as n (n.id)}
                      <div class="person" data-od-id="person-{n.id}" tabindex="0" role="button">
                        {#if isPersonNamed(n) && !personPhotoErrors.has(n.id)}
                          <img class="avatar" src={resolvePersonThumbUrl(n)} alt={getNodeName(n)} onerror={() => handlePersonImgError(n)}>
                        {:else if isPersonNamed(n)}
                          <div class="avatar-initials">{getInitials(getNodeName(n))}</div>
                        {:else}
                          <div class="avatar-unknown">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/></svg>
                          </div>
                        {/if}
                        <div class="person-name">{isPersonNamed(n) ? getNodeName(n) : 'Unknown'}</div>
                      </div>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if events.length > 0}
                <section class="section" data-od-id="sec-events">
                  <div class="section-header">Events</div>
                  <div class="event-list">
                    {#each events as n (n.id)}
                      <div class="event-item" data-od-id="event-{n.id}">
                        <span class="event-dot"></span>
                        <span>{getNodeName(n)}</span>
                      </div>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if exifExposureRow.length > 0}
                <section class="section" data-od-id="sec-exif">
                  <div class="section-header">Exposure</div>
                  <div class="pills">
                    {#each exifExposureRow as row}
                      <span class="pill" data-od-id="exif-{row.label}">{row.value}</span>
                    {/each}
                  </div>
                </section>
              {/if}
            {/if}

            {#if activeTab === 'insights'}
              <section class="section" data-od-id="sec-insights-entities">
                <div class="section-header">Entities - {others.length}</div>
                {#if others.length > 0}
                  <div class="pills">
                    {#each others as n, i (n.id)}
                      <span class="pill {i === 0 ? 'pill-accent' : ''}" data-od-id="entity-{n.id}">{getNodeName(n)}</span>
                    {/each}
                  </div>
                {:else}
                  <div class="empty-text">No entities detected.</div>
                {/if}
              </section>

              {#if locations.length > 0}
                <section class="section" data-od-id="sec-insights-locations">
                  <div class="section-header">Locations</div>
                  <div class="pills">
                    {#each locations as n (n.id)}
                      <span class="pill" data-od-id="loc-pill-{n.id}">{getNodeName(n)}</span>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if events.length > 0}
                <section class="section" data-od-id="sec-insights-events">
                  <div class="section-header">Events</div>
                  <div class="pills">
                    {#each events as n (n.id)}
                      <span class="pill" data-od-id="event-pill-{n.id}">{getNodeName(n)}</span>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if persons.length > 0}
                <section class="section" data-od-id="sec-insights-people">
                  <div class="section-header">People</div>
                  <div class="pills">
                    {#each persons as n (n.id)}
                      <span class="pill" data-od-id="person-pill-{n.id}">{isPersonNamed(n) ? getNodeName(n) : 'Unknown'}</span>
                    {/each}
                  </div>
                </section>
              {/if}
            {/if}

            {#if activeTab === 'connections' && others.length > 0}
              <section class="section" data-od-id="sec-connections">
                <div class="section-header">Connected Entities - {others.length}</div>
                <div class="connection-list">
                  {#each others as n (n.id)}
                    <div class="connection-item" data-od-id="conn-{n.id}">
                      <span class="connection-kind">{classifyKind(n)}</span>
                      <span class="connection-name">{getNodeName(n)}</span>
                    </div>
                  {/each}
                </div>
              </section>
            {/if}

          </div>

          <div class="sidebar-footer" data-od-id="sidebar-footer">
            <button class="btn-delete" data-od-id="btn-delete" onclick={handleDelete} disabled={deleting}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              {deleting ? 'Deleting...' : 'Delete Photo'}
            </button>
          </div>
        </aside>

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
  :root {
    --bg:          #0B0C10;
    --bg-elev:     #121317;
    --surface:     rgba(255, 255, 255, 0.05);
    --surface-2:   rgba(255, 255, 255, 0.08);
    --surface-dark: rgba(0, 0, 0, 0.40);
    --hairline:    rgba(255, 255, 255, 0.08);
    --hairline-2:  rgba(255, 255, 255, 0.12);
    --fg:          #FFFFFF;
    --fg-2:        #C8C9CE;
    --muted:       #A0A0A0;
    --faint:       #6B6C72;
    --accent:      #007AFF;
    --accent-soft: rgba(0, 122, 255, 0.15);
    --success:     #34C759;
    --warn:        #FF9F0A;
    --danger:      #FF3B30;
    --font-body:   -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Inter', 'Segoe UI', system-ui, sans-serif;
    --font-display:-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Inter', system-ui, sans-serif;
    --font-mono:   ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }

  .overlay-app {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 5vh 5vw;
    font-family: var(--font-body);
    color: var(--fg);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    animation: fade-in 0.25s ease;
  }
  @keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }

  .overlay-body {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    max-width: 1200px;
    max-height: 100%;
    background: var(--bg);
    border-radius: 16px;
    border: 1px solid var(--hairline-2);
    overflow: hidden;
    box-shadow:
      0 24px 80px rgba(0, 0, 0, 0.6),
      0 0 0 1px rgba(255, 255, 255, 0.04);
    animation: pop-in 0.28s cubic-bezier(0.16, 1, 0.3, 1);
  }
  @keyframes pop-in {
    from { opacity: 0; transform: scale(0.97) translateY(8px); }
    to   { opacity: 1; transform: scale(1) translateY(0); }
  }

  .topbar {
    height: 56px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    background: rgba(11, 12, 16, 0.80);
    backdrop-filter: blur(20px) saturate(1.4);
    -webkit-backdrop-filter: blur(20px) saturate(1.4);
    border-bottom: 1px solid var(--hairline);
    z-index: 100;
  }
  .topbar-left {
    display: flex;
    align-items: center;
    gap: 14px;
    min-width: 0;
  }
  .filename {
    font-family: var(--font-display);
    font-weight: 600;
    font-size: 14px;
    color: var(--fg);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 400px;
  }
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    background: var(--surface);
    border: 1px solid var(--hairline);
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .status-pill.is-processing { border-color: rgba(255, 159, 10, 0.3); }
  .status-pill.is-complete { border-color: rgba(52, 199, 89, 0.3); color: var(--success); }
  .status-pill.is-error { border-color: rgba(255, 59, 48, 0.3); color: var(--danger); }
  .status-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--success);
  }
  .status-dot.is-processing {
    background: var(--warn);
    animation: pulse 1.4s ease-in-out infinite;
  }
  .status-dot.is-error { background: var(--danger); }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

  .close-btn {
    width: 34px; height: 34px;
    border-radius: 50%;
    background: var(--surface-dark);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--hairline-2);
    color: rgba(255, 255, 255, 0.80);
    display: grid; place-items: center;
    cursor: pointer;
    transition: all 0.15s ease;
    flex-shrink: 0;
  }
  .close-btn:hover {
    background: rgba(255, 59, 48, 0.15);
    border-color: rgba(255, 59, 48, 0.4);
    color: var(--danger);
    transform: scale(1.05);
  }
  .close-btn:active { transform: scale(0.95); }
  .close-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .close-btn svg { width: 16px; height: 16px; }

  .content {
    flex: 1;
    display: flex;
    min-height: 0;
  }

  .stage {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    padding: 20px;
    gap: 14px;
  }

  .viewer {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    min-height: 0;
  }

  .photo {
    max-width: 100%;
    max-height: 100%;
    border-radius: 16px;
    box-shadow:
      0 0 0 1px rgba(255, 255, 255, 0.06),
      0 20px 60px rgba(0, 0, 0, 0.5),
      0 0 80px rgba(0, 122, 255, 0.05);
    object-fit: contain;
    display: block;
    cursor: zoom-in;
    transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
  }
  .photo:hover { transform: translateY(-2px); }

  .photo-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 300px;
    height: 300px;
    border-radius: 16px;
    background: var(--surface);
    border: 1px solid var(--hairline);
    color: var(--faint);
  }
  .photo-placeholder svg { width: 48px; height: 48px; }

  .viewer-badges {
    position: absolute;
    right: 12px;
    bottom: 12px;
    z-index: 10;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
    pointer-events: none;
  }
  .location-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    border-radius: 999px;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(12px) saturate(1.3);
    -webkit-backdrop-filter: blur(12px) saturate(1.3);
    border: 1px solid var(--hairline-2);
    font-size: 12px;
    font-weight: 500;
    color: var(--fg);
    max-width: 240px;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .location-badge svg { width: 14px; height: 14px; flex-shrink: 0; color: var(--muted); }
  .location-badge span { overflow: hidden; text-overflow: ellipsis; }

  .filmstrip {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    padding: 4px 0;
  }
  .filmstrip-nav {
    width: 32px; height: 32px;
    flex-shrink: 0;
    border-radius: 50%;
    background: var(--surface-dark);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--hairline);
    color: var(--fg-2);
    display: grid; place-items: center;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .filmstrip-nav:hover:not(:disabled) {
    background: var(--surface-2);
    border-color: var(--hairline-2);
    color: var(--fg);
  }
  .filmstrip-nav:active:not(:disabled) { transform: scale(0.92); }
  .filmstrip-nav:disabled { opacity: 0.3; cursor: default; }
  .filmstrip-nav:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .filmstrip-nav svg { width: 16px; height: 16px; }

  .filmstrip-track {
    flex: 1;
    display: flex;
    gap: 6px;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline-2) transparent;
    padding: 2px 0;
    min-width: 0;
  }
  .filmstrip-track::-webkit-scrollbar { height: 4px; }
  .filmstrip-track::-webkit-scrollbar-thumb { background: var(--hairline-2); border-radius: 2px; }

  .filmstrip-thumb {
    flex-shrink: 0;
    width: 52px; height: 52px;
    border-radius: 8px;
    overflow: hidden;
    border: 2px solid transparent;
    background: var(--surface);
    cursor: pointer;
    padding: 0;
    transition: border-color 0.15s ease, transform 0.15s ease;
  }
  .filmstrip-thumb img {
    width: 100%; height: 100%;
    object-fit: cover;
    display: block;
  }
  .filmstrip-thumb:hover { transform: translateY(-2px); border-color: var(--hairline-2); }
  .filmstrip-thumb.is-active {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }
  .filmstrip-thumb:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

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
    border-left: 1px solid var(--hairline-2);
    padding-left: 8px;
    margin-left: 4px;
  }
  .filmstrip-day-label {
    flex-shrink: 0;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    white-space: nowrap;
    padding: 2px 0;
  }

  .meta-bar {
    display: flex;
    align-items: stretch;
    gap: 0;
    background: var(--surface);
    backdrop-filter: blur(20px) saturate(1.3);
    -webkit-backdrop-filter: blur(20px) saturate(1.3);
    border: 1px solid var(--hairline);
    border-radius: 14px;
    padding: 14px 18px;
    flex-shrink: 0;
  }
  .meta-seg {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    border-right: 1px solid var(--hairline);
    min-width: 0;
  }
  .meta-seg:first-child { padding-left: 0; }
  .meta-seg:last-child { border-right: none; padding-right: 0; }
  .meta-seg-icon {
    width: 32px; height: 32px;
    border-radius: 8px;
    background: var(--surface-2);
    display: grid; place-items: center;
    color: var(--muted);
    flex-shrink: 0;
  }
  .meta-seg-icon svg { width: 16px; height: 16px; }
  .meta-seg-text { min-width: 0; flex: 1; }
  .meta-seg-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg);
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .meta-seg-sub {
    font-size: 11px;
    color: var(--muted);
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 1px;
  }

  .sidebar {
    width: 360px;
    flex-shrink: 0;
    background: rgba(15, 16, 20, 0.85);
    backdrop-filter: blur(24px) saturate(1.4);
    -webkit-backdrop-filter: blur(24px) saturate(1.4);
    border-left: 1px solid var(--hairline);
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .tabs {
    display: flex;
    border-bottom: 1px solid var(--hairline);
    flex-shrink: 0;
    padding: 0 4px;
  }
  .tab {
    padding: 14px 16px;
    font-size: 13px;
    font-weight: 500;
    color: var(--muted);
    background: transparent;
    border: none;
    border-bottom: 3px solid transparent;
    cursor: pointer;
    transition: color 0.15s ease, border-color 0.15s ease;
    font-family: var(--font-body);
    margin-bottom: -1px;
  }
  .tab:hover { color: var(--fg-2); }
  .tab.active {
    color: var(--fg);
    border-bottom-color: var(--accent);
  }
  .tab:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }

  .sidebar-content {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 20px;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline-2) transparent;
  }
  .sidebar-content::-webkit-scrollbar { width: 6px; }
  .sidebar-content::-webkit-scrollbar-thumb { background: var(--hairline-2); border-radius: 3px; }

  .section { margin-bottom: 24px; }
  .section:last-child { margin-bottom: 0; }
  .section-header {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    margin-bottom: 10px;
  }

  .description {
    font-size: 13px;
    line-height: 1.55;
    color: var(--fg-2);
  }
  .description :global(.overlay-p) { margin-bottom: 8px; }
  .description :global(.overlay-p:last-child) { margin-bottom: 0; }
  .description :global(.overlay-code) {
    background: var(--surface-2);
    border-radius: 8px;
    padding: 10px;
    overflow-x: hidden;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--fg-2);
    margin-bottom: 8px;
  }

  .loading-indicator {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--muted);
  }
  .spinner {
    width: 14px; height: 14px;
    border: 2px solid var(--hairline-2);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .error-text { font-size: 13px; color: var(--danger); }
  .empty-text { font-size: 13px; color: var(--faint); font-style: italic; }

  .location-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }
  .location-row svg {
    width: 16px; height: 16px;
    color: var(--muted);
    flex-shrink: 0;
    margin-top: 2px;
  }
  .location-text {
    font-size: 13px;
    color: var(--fg-2);
    line-height: 1.5;
  }

  .people-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }
  .person {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    width: 60px;
    cursor: pointer;
  }
  .person:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 8px; }
  .avatar {
    width: 48px; height: 48px;
    border-radius: 50%;
    border: 2px solid var(--hairline-2);
    object-fit: cover;
    background: var(--surface-2);
    transition: border-color 0.15s ease, transform 0.15s ease;
  }
  .person:hover .avatar { border-color: var(--accent); transform: scale(1.05); }
  .avatar-initials {
    width: 48px; height: 48px;
    border-radius: 50%;
    border: 2px solid var(--hairline-2);
    background: linear-gradient(135deg, #3A3B40, #2A2B30);
    display: grid; place-items: center;
    font-size: 14px; font-weight: 600;
    color: var(--fg-2);
    transition: border-color 0.15s ease, transform 0.15s ease;
  }
  .person:hover .avatar-initials { border-color: var(--accent); }
  .avatar-unknown {
    width: 48px; height: 48px;
    border-radius: 50%;
    border: 2px solid var(--hairline-2);
    background: var(--surface);
    display: grid; place-items: center;
    color: var(--faint);
    transition: border-color 0.15s ease;
  }
  .person:hover .avatar-unknown { border-color: var(--accent); }
  .avatar-unknown svg { width: 20px; height: 20px; }
  .person-name {
    font-size: 11px;
    color: var(--muted);
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    width: 100%;
  }

  .pills {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .pill {
    padding: 6px 12px;
    border-radius: 999px;
    background: var(--surface);
    border: 1px solid var(--hairline);
    font-size: 12px;
    font-weight: 500;
    color: var(--fg-2);
    transition: background 0.15s ease, border-color 0.15s ease;
  }
  .pill:hover { background: var(--surface-2); border-color: var(--hairline-2); }
  .pill-accent {
    background: var(--accent-soft);
    border-color: rgba(0, 122, 255, 0.3);
    color: #5AC8FA;
  }

  .event-list { display: flex; flex-direction: column; gap: 8px; }
  .event-item {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: var(--fg-2);
  }
  .event-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--warn);
    flex-shrink: 0;
  }

  .connection-list { display: flex; flex-direction: column; gap: 10px; }
  .connection-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    background: var(--surface);
    border-radius: 10px;
    border: 1px solid var(--hairline);
  }
  .connection-kind {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    font-family: var(--font-mono);
    flex-shrink: 0;
    min-width: 60px;
  }
  .connection-name {
    font-size: 13px;
    color: var(--fg-2);
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
    flex: 1;
  }

  .sidebar-footer {
    padding: 16px 20px;
    border-top: 1px solid var(--hairline);
    flex-shrink: 0;
  }
  .btn-delete {
    width: 100%;
    padding: 10px;
    border-radius: 10px;
    background: transparent;
    border: 1px solid rgba(255, 59, 48, 0.3);
    color: var(--danger);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s ease;
    font-family: var(--font-body);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .btn-delete:hover:not(:disabled) {
    background: rgba(255, 59, 48, 0.1);
    border-color: var(--danger);
  }
  .btn-delete:active:not(:disabled) { transform: scale(0.98); }
  .btn-delete:focus-visible { outline: 2px solid var(--danger); outline-offset: 2px; }
  .btn-delete:disabled { opacity: 0.5; cursor: default; }
  .btn-delete svg { width: 14px; height: 14px; }

  .fullscreen-overlay {
    position: fixed;
    inset: 0;
    z-index: 2000;
    background: rgba(0, 0, 0, 0.92);
    display: flex;
    align-items: center;
    justify-content: center;
    animation: fade-in 0.2s ease;
  }
  .fullscreen-overlay img {
    max-width: 92%;
    max-height: 92%;
    border-radius: 8px;
    box-shadow: 0 30px 100px rgba(0, 0, 0, 0.8);
  }
  .fullscreen-close {
    position: absolute;
    top: 20px; right: 20px;
    width: 40px; height: 40px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.1);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(255, 255, 255, 0.15);
    color: #fff;
    display: grid; place-items: center;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .fullscreen-close:hover {
    background: rgba(255, 59, 48, 0.2);
    border-color: rgba(255, 59, 48, 0.4);
    color: var(--danger);
  }
  .fullscreen-close svg { width: 18px; height: 18px; }

  @media (max-width: 900px) {
    .content { flex-direction: column; }
    .sidebar {
      width: 100%;
      border-left: none;
      border-top: 1px solid var(--hairline);
      max-height: 50vh;
    }
    .stage { padding: 14px; gap: 10px; }
    .photo { max-width: 100%; }
    .meta-bar { flex-wrap: wrap; gap: 12px; padding: 12px; }
    .meta-seg { flex: 1 1 45%; border-right: none; padding: 0; }
  }
  @media (max-width: 480px) {
    .topbar { height: 48px; padding: 0 14px; }
    .filename { font-size: 13px; }
    .status-pill { display: none; }
    .stage { padding: 10px; }
    .meta-seg { flex: 1 1 100%; }
    .sidebar-content { padding: 14px; }
    .tab { padding: 12px 12px; font-size: 12px; }
    .sidebar { max-height: 60vh; }
  }
</style>
