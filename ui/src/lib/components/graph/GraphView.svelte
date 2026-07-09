<script lang="ts">
  import { type KGNode, type KGEdge, type KGGraph } from '$lib/constants';
  import { LightragClient } from '$lib/services/lightrag-client';
  import { graphStore } from '$lib/stores/graph.svelte';
  import GraphCanvas from './GraphCanvas.svelte';
  import GraphControls from './GraphControls.svelte';
  import NodeDetail from './NodeDetail.svelte';
  import GraphSearch from './GraphSearch.svelte';
  import PhotoOverlay from './PhotoOverlay.svelte';

  const client = new LightragClient();

  let graphContainerEl: HTMLDivElement | undefined = $state();
  let photoOverlay: PhotoOverlay | undefined = $state();

  let allNodes = $state<KGNode[]>([]);
  let allEdges = $state<KGEdge[]>([]);
  let activePhotoCluster: Set<string> | null = null;
  let visibleNodeIds = $state<Set<string>>(new Set());
  let expandedNodeIds = $state<Set<string>>(new Set());
  let hoveredNodeId = $state<string | null>(null);
  let selectedNode = $state<KGNode | null>(null);
  let hoveredNode = $state<KGNode | null>(null);
  let isLoading = $state(true);
  let error = $state<string | null>(null);
  let layout = $state<'force' | 'circular' | 'radial'>('force');
  let nodeSizeMode = $state<'degree' | 'uniform'>('degree');
  let showLabels = $state(true);
  let graphCanvas: GraphCanvas | undefined = $state();
  let fullscreenUrl = $state<string | null>(null);

  let highlightedIds = $state<Set<string>>(new Set());

  let degreeMap = $derived.by(() => {
    const map = new Map<string, number>();
    for (const e of allEdges) {
      map.set(e.source, (map.get(e.source) ?? 0) + 1);
      map.set(e.target, (map.get(e.target) ?? 0) + 1);
    }
    return map;
  });

  /** Pre-computed adjacency map for fast neighbor lookup */
  let adjacencyMap = $derived.by(() => {
    const map = new Map<string, string[]>();
    for (const e of allEdges) {
      if (!map.has(e.source)) map.set(e.source, []);
      if (!map.has(e.target)) map.set(e.target, []);
      map.get(e.source)!.push(e.target);
      map.get(e.target)!.push(e.source);
    }
    return map;
  });

  /** The effective visible set: base visible + hovered node's cluster */
  let effectiveVisibleIds = $derived.by(() => {
    const ids = new Set(visibleNodeIds);

    // Hovered node + its L1 neighbors (temporary)
    if (hoveredNodeId) {
      ids.add(hoveredNodeId);
      const neighbors = adjacencyMap.get(hoveredNodeId);
      if (neighbors) {
        for (const nId of neighbors) {
          ids.add(nId);
        }
      }
    }

    return ids;
  });

  function initVisibleNodes() {
    // If we're focused on a photo cluster, keep showing that cluster
    if (activePhotoCluster && activePhotoCluster.size > 0) {
      visibleNodeIds = new Set(activePhotoCluster);
      return;
    }
    // Show the top 50 entities with the most relations
    const sorted = [...allNodes].sort((a, b) =>
      (degreeMap.get(b.id) ?? 0) - (degreeMap.get(a.id) ?? 0)
    );
    if (sorted.length === 0) {
      visibleNodeIds = new Set();
      return;
    }
    const top50 = sorted.slice(0, 50);
    visibleNodeIds = new Set(top50.map((n) => n.id));
  }

  let neighbors = $derived.by(() => {
    if (!selectedNode) return { nodes: [] as KGNode[], edges: [] as KGEdge[] };
    const nodeIds = new Set<string>();
    const connectedEdges: KGEdge[] = [];
    for (const e of allEdges) {
      if (e.source === selectedNode.id || e.target === selectedNode.id) {
        nodeIds.add(e.source === selectedNode.id ? e.target : e.source);
        connectedEdges.push(e);
      }
    }
    const neighborNodes = allNodes.filter((n) => nodeIds.has(n.id));
    return { nodes: neighborNodes, edges: connectedEdges };
  });

  async function loadGraph() {
    isLoading = true;
    error = null;
    try {
      let graph: KGGraph;
      try {
        const labels = await client.getPopularLabels(1);
        const label = labels?.[0];
        if (label) {
          graph = await client.getGraph(label);
        } else {
          graph = await client.getGraph('default');
        }
      } catch {
        const labels = await client.getLabels();
        const label = labels?.[0];
        if (label) {
          graph = await client.getGraph(label);
        } else {
          graph = { nodes: [], edges: [] };
        }
      }
      allNodes = graph.nodes ?? [];
      allEdges = graph.edges ?? [];
      initVisibleNodes();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load graph';
    } finally {
      isLoading = false;
    }
  }

  function handleSelectNode(node: KGNode) {
    selectedNode = node;
    expandedNodeIds = new Set([...expandedNodeIds, node.id]);
    const nodeId = String(node.id);
    const isPhoto = node.labels?.includes('Photo') || node.properties?.entity_type === 'Photo' || nodeId.includes('(Photo)');
    if (isPhoto) {
      photoOverlay?.showPhotoCard(nodeId);
      if (graphStore.photoImages[nodeId]) {
        fullscreenUrl = graphStore.photoImages[nodeId];
      }
    } else {
      photoOverlay?.hidePhotoCard();
    }
  }

  function handleHoverNode(node: KGNode | null) {
    hoveredNode = node;
    hoveredNodeId = node?.id ?? null;
    if (node) {
      const nodeId = String(node.id);
      const isPhoto = node.labels?.includes('Photo') || node.properties?.entity_type === 'Photo' || nodeId.includes('(Photo)');
      if (isPhoto) {
        photoOverlay?.showPhotoCard(nodeId);
      } else {
        photoOverlay?.hidePhotoCard();
      }
    } else {
      if (!selectedNode) {
        photoOverlay?.hidePhotoCard();
      }
    }
  }

  function handleDeselect() {
    selectedNode = null;
    highlightedIds = new Set();
    photoOverlay?.hidePhotoCard();
  }

  async function handleSelectLabel(label: string) {
    // First, check if the entity already exists in the current graph data
    const existingNode = allNodes.find((n) => n.id === label);

    if (existingNode) {
      // Entity is already in the graph — just filter visibility and highlight
      const clusterIds = new Set<string>();
      clusterIds.add(existingNode.id);
      const neighbors = adjacencyMap.get(existingNode.id);
      if (neighbors) {
        for (const nId of neighbors) {
          clusterIds.add(nId);
        }
      }
      visibleNodeIds = clusterIds;
      highlightedIds = new Set([existingNode.id]);
      selectedNode = existingNode;
      expandedNodeIds = new Set([...expandedNodeIds, existingNode.id]);
      return;
    }

    // Entity not in current graph — fetch its neighborhood and merge it in
    try {
      isLoading = true;
      const graph: KGGraph = await client.getGraph(label);
      if (!graph.nodes?.length) {
        isLoading = false;
        return;
      }

      // Merge new nodes/edges into existing data (like handleExpandNeighbors)
      const existingNodeIds = new Set(allNodes.map((n) => n.id));
      const existingEdgeIds = new Set(allEdges.map((e) => e.id));
      const additionalNodes = graph.nodes.filter((n) => !existingNodeIds.has(n.id));
      const additionalEdges = graph.edges.filter((e) => !existingEdgeIds.has(e.id));
      allNodes = [...allNodes, ...additionalNodes];
      allEdges = [...allEdges, ...additionalEdges];

      // Build adjacency from ALL edges (old + new) to find the center node's neighbors
      const fullAdj = new Map<string, string[]>();
      for (const e of allEdges) {
        if (!fullAdj.has(e.source)) fullAdj.set(e.source, []);
        if (!fullAdj.has(e.target)) fullAdj.set(e.target, []);
        fullAdj.get(e.source)!.push(e.target);
        fullAdj.get(e.target)!.push(e.source);
      }

      // Find the center node (exact match to the label)
      const centerNode = allNodes.find((n) => n.id === label);

      // Show only the cluster for the selected node: center + its direct neighbors
      const clusterIds = new Set<string>();
      if (centerNode) {
        clusterIds.add(centerNode.id);
        const neighbors = fullAdj.get(centerNode.id);
        if (neighbors) {
          for (const nId of neighbors) {
            clusterIds.add(nId);
          }
        }
      } else {
        // Fallback: show all newly fetched nodes
        for (const n of additionalNodes) {
          clusterIds.add(n.id);
        }
      }

      visibleNodeIds = clusterIds;
      expandedNodeIds = centerNode ? new Set([...expandedNodeIds, centerNode.id]) : expandedNodeIds;
      highlightedIds = centerNode ? new Set([centerNode.id]) : new Set();
      if (centerNode) selectedNode = centerNode;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load graph for: ' + label;
    } finally {
      isLoading = false;
    }
  }

  function handleSearch(query: string) {
    if (!query.trim()) {
      highlightedIds = new Set();
      // Reset to default view
      initVisibleNodes();
      return;
    }
    const q = query.toLowerCase().trim();
    const matches = allNodes.filter((n) => {
      const name = ((n.properties?.name ?? n.properties?.title ?? n.id) as string).toLowerCase();
      return name.includes(q) || n.id.toLowerCase().includes(q);
    });

    if (matches.length === 0) {
      // No local matches — keep current view, don't clear visible nodes.
      // The search dropdown (GraphSearch) uses the API and will show results
      // independently. Clearing the graph here would leave the user with an
      // empty view when the entity exists but isn't in the currently loaded subset.
      highlightedIds = new Set();
      return;
    }

    highlightedIds = new Set(matches.map((n) => n.id));

    // Prioritize person/entity nodes over image nodes, then by degree
    const entityTypePriority: Record<string, number> = {
      person: 0,
      PERSON: 0,
      location: 1,
      Location: 1,
      organization: 2,
      concept: 3,
      event: 4,
      date: 5,
      Date: 5,
      artifact: 6,
      image: 10,
      content: 10,
    };
    const sortedMatches = [...matches].sort((a, b) => {
      const aType = (a.properties?.entity_type as string) ?? 'unknown';
      const bType = (b.properties?.entity_type as string) ?? 'unknown';
      const aPri = entityTypePriority[aType] ?? 5;
      const bPri = entityTypePriority[bType] ?? 5;
      if (aPri !== bPri) return aPri - bPri;
      // Within same priority, prefer higher-degree nodes
      return (degreeMap.get(b.id) ?? 0) - (degreeMap.get(a.id) ?? 0);
    });

    const center = sortedMatches[0];
    const clusterIds = new Set<string>();
    clusterIds.add(center.id);
    const neighbors = adjacencyMap.get(center.id);
    if (neighbors) {
      for (const nId of neighbors) {
        clusterIds.add(nId);
      }
    }
    for (const m of sortedMatches) {
      clusterIds.add(m.id);
    }
    visibleNodeIds = clusterIds;
  }

  function handleQueryAbout(_node: KGNode) {}

  async function handleExpandNeighbors(node: KGNode) {
    try {
      const graph: KGGraph = await client.getGraph(undefined, node.id, 1);
      const newNodeIds = new Set(allNodes.map((n) => n.id));
      const newEdgeIds = new Set(allEdges.map((e) => e.id));
      const additionalNodes = graph.nodes.filter((n) => !newNodeIds.has(n.id));
      const additionalEdges = graph.edges.filter((e) => !newEdgeIds.has(e.id));
      allNodes = [...allNodes, ...additionalNodes];
      allEdges = [...allEdges, ...additionalEdges];
      expandedNodeIds = new Set([...expandedNodeIds, node.id]);
    } catch {
      // Expansion failed silently
    }
  }

  function handleZoomIn() {
    graphCanvas?.zoomIn();
  }
  function handleZoomOut() {
    graphCanvas?.zoomOut();
  }

  function handleFitView() {
    graphCanvas?.fitView();
  }

  function handleResetView() {
    expandedNodeIds = new Set();
    hoveredNode = null;
    highlightedIds = new Set();
    selectedNode = null;
    activePhotoCluster = null;
    initVisibleNodes();
  }

  // Merge optimistic/sse nodes from graphStore into the local graph data
  $effect(() => {
    const storeNodes = graphStore.nodes;
    const storeEdges = graphStore.edges;
    if (storeNodes.length === 0 && storeEdges.length === 0) return;

    const existingNodeIds = new Set(allNodes.map((n) => n.id));
    const existingEdgeIds = new Set(allEdges.map((e) => e.id));
    let changed = false;
    let newPhotoNodeId: string | null = null;

    for (const node of storeNodes) {
      if (!existingNodeIds.has(node.id)) {
        allNodes = [...allNodes, node];
        existingNodeIds.add(node.id);
        changed = true;
        if (node.labels?.includes('Photo') || node.properties?.entity_type === 'Photo' || node.id.includes('(Photo)')) {
          newPhotoNodeId = node.id;
        }
      }
    }
    for (const edge of storeEdges) {
      if (!existingEdgeIds.has(edge.id)) {
        allEdges = [...allEdges, edge];
        existingEdgeIds.add(edge.id);
        changed = true;
      }
    }

    if (changed) {
      // When a new Photo is attached, show only its cluster.
      // Otherwise (e.g. SSE adding EXIF entities), expand the cluster.
      if (newPhotoNodeId) {
        const clusterIds = new Set<string>();
        clusterIds.add(newPhotoNodeId);
        // Include all nodes connected to the photo via edges
        for (const edge of allEdges) {
          if (edge.source === newPhotoNodeId) clusterIds.add(edge.target);
          if (edge.target === newPhotoNodeId) clusterIds.add(edge.source);
        }
        // Also include any other newly-added nodes (they're likely related)
        for (const node of storeNodes) {
          clusterIds.add(node.id);
        }
        visibleNodeIds = clusterIds;
        highlightedIds = new Set([newPhotoNodeId]);
        activePhotoCluster = clusterIds;
      } else if (activePhotoCluster) {
        // SSE is adding EXIF/visual entities to the existing photo cluster
        const expanded = new Set(activePhotoCluster);
        for (const node of storeNodes) {
          expanded.add(node.id);
        }
        for (const edge of storeEdges) {
          expanded.add(edge.source);
          expanded.add(edge.target);
        }
        visibleNodeIds = expanded;
        activePhotoCluster = expanded;
      } else {
        // No photo cluster — just make new nodes visible alongside existing ones
        const newVisible = new Set(visibleNodeIds);
        for (const node of storeNodes) {
          newVisible.add(node.id);
        }
        for (const edge of storeEdges) {
          newVisible.add(edge.source);
          newVisible.add(edge.target);
        }
        visibleNodeIds = newVisible;
      }
    }

    // Auto-focus on newly attached Photo nodes
    if (newPhotoNodeId) {
      setTimeout(() => graphCanvas?.focusNode(newPhotoNodeId), 300);
    }
  });

  // When photo images arrive, refresh graph so 3D textures update
  $effect(() => {
    const photos = graphStore.photoImages;
    if (Object.keys(photos).length > 0 && graphCanvas) {
      graphCanvas.refreshGraph();
    }
  });

  // When pipeline completes, clear the photo cluster focus so the full graph is visible
  $effect(() => {
    if (graphStore.pipelineDone) {
      activePhotoCluster = null;
      graphStore.pipelineDone = false;
      initVisibleNodes();
    }
  });

  $effect(() => {
    loadGraph();
  });
</script>

<div bind:this={graphContainerEl} data-testid="graph-view" class="graph-view-container relative w-full h-full overflow-hidden bg-cyber-bg {isLoading ? 'animate-pulse-glow' : ''}">
  {#if allNodes.length > 0}
    <div class="absolute bottom-4 right-4 z-10 animate-fade-in-up">
      <div class="bg-cyber-surface/80 backdrop-blur-md rounded-xl border border-cyber-border px-3 py-2 text-xs text-cyber-text-dim flex items-center gap-2">
        <span><span class="text-cyber-cyan font-semibold">{effectiveVisibleIds.size}</span> of {allNodes.length} nodes</span>
        <span class="text-cyber-border">·</span>
        <span><span class="text-cyber-purple font-semibold">{allEdges.filter(e => effectiveVisibleIds.has(e.source) && effectiveVisibleIds.has(e.target)).length}</span> of {allEdges.length} edges</span>
        {#if expandedNodeIds.size > 0}
          <button
            onclick={handleResetView}
            class="ml-1 px-2 py-0.5 rounded text-[10px] bg-cyber-cyan/10 text-cyber-cyan border border-cyber-cyan/30 hover:bg-cyber-cyan/20 transition-colors"
          >
            Reset View
          </button>
        {/if}
      </div>
    </div>
  {/if}
  {#if error}
    <div class="absolute inset-0 flex items-center justify-center z-40">
      <div class="bg-cyber-surface/90 backdrop-blur-md rounded-2xl border border-cyber-red/30 p-6 max-w-md text-center">
        <div class="w-12 h-12 rounded-full bg-cyber-red/10 flex items-center justify-center mx-auto mb-3">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ff3366" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        </div>
        <h3 class="text-cyber-red font-semibold mb-1">Graph Load Failed</h3>
        <p class="text-sm text-cyber-text-dim">{error}</p>
        <button
          onclick={loadGraph}
          class="mt-4 px-4 py-2 rounded-lg text-sm bg-cyber-red/10 text-cyber-red border border-cyber-red/30 hover:bg-cyber-red/20 transition-colors"
        >
          Retry
        </button>
      </div>
    </div>
  {/if}

  {#if isLoading && allNodes.length === 0}
    <div class="absolute inset-0 flex items-center justify-center z-30">
      <div class="flex flex-col items-center gap-3 animate-fade-in-up">
        <div class="w-10 h-10 border-2 border-cyber-cyan border-t-transparent rounded-full animate-spin"></div>
        <p class="text-sm text-cyber-text-dim">Loading knowledge graph...</p>
      </div>
    </div>
  {:else}
    <GraphCanvas
      bind:this={graphCanvas}
      nodes={allNodes}
      edges={allEdges}
      {visibleNodeIds}
      {highlightedIds}
      bind:selectedNode
      bind:hoveredNode
      bind:layout
      bind:nodeSizeMode
      bind:showLabels
      onselectNode={handleSelectNode}
      onhoverNode={handleHoverNode}
      ondeselect={handleDeselect}
      onfitToView={handleFitView}
    />

    <GraphControls
      onzoomIn={handleZoomIn}
      onzoomOut={handleZoomOut}
      onfitView={handleFitView}
    />

    <GraphSearch
      onselectLabel={handleSelectLabel}
      onsearch={handleSearch}
    />

    <NodeDetail
      node={selectedNode}
      neighbors={neighbors}
      onclose={handleDeselect}
      onqueryAbout={handleQueryAbout}
      onexpandNeighbors={handleExpandNeighbors}
    />
  {/if}

  <PhotoOverlay bind:this={photoOverlay} bind:fullscreenUrl {graphCanvas} containerEl={graphContainerEl} />
</div>

<style>
  .graph-view-container {
    touch-action: none;
    -webkit-user-select: none;
    user-select: none;
  }
</style>