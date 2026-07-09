<script lang="ts">
  import { type KGNode, type KGEdge } from '$lib/constants';
  import ForceGraph3D from '3d-force-graph';
  import type { ForceGraph3DInstance, NodeObject, LinkObject } from '3d-force-graph';
  import * as THREE from 'three';
  import { onMount } from 'svelte';
  import { graphStore } from '$lib/stores/graph.svelte';

  const FALLBACK_COLORS = ['#5a6b80', '#7c8da5', '#4a6fa5', '#6b8f71', '#8b7ec8', '#c77d5a'];
  const TYPE_COLORS: Record<string, string> = {
    person: '#00ffff',
    image: '#d8b4fe',
    location: '#00ff88',
    event: '#ffe033',
    organization: '#ff9f1c',
    concept: '#e879f9',
  };
  const DEFAULT_COLOR = '#5a6b80';
  const BORDER_COLOR = '#1e2d45';
  const CYAN = '#00d4ff';
  const BG_COLOR = '#0a0e17';
  const BASE_RADIUS = 6;
  const MAX_RADIUS = 20;
  const FADE_DURATION = 350;

  interface GraphNode extends NodeObject {
    id: string;
    labels: string[];
    properties: Record<string, unknown>;
    x?: number;
    y?: number;
    z?: number;
    vx?: number;
    vy?: number;
    vz?: number;
    val?: number;
    color?: string;
  }

  interface GraphLink extends LinkObject<GraphNode> {
    id: string;
    type: string;
    properties: Record<string, unknown>;
    color?: string;
  }

  let {
    nodes = $bindable([]),
    edges = $bindable([]),
    visibleNodeIds = $bindable(new Set<string>()),
    highlightedIds = $bindable(new Set<string>()),
    selectedNode = $bindable(null),
    hoveredNode = $bindable(null),
    layout = $bindable('force'),
    nodeSizeMode = $bindable('degree'),
    showLabels = $bindable(true),
    onselectNode = (_node: KGNode) => {},
    onhoverNode = (_node: KGNode | null) => {},
    ondeselect = () => {},
    onfitToView = () => {}
  }: {
    nodes: KGNode[];
    edges: KGEdge[];
    visibleNodeIds: Set<string>;
    highlightedIds: Set<string>;
    selectedNode: KGNode | null;
    hoveredNode: KGNode | null;
    layout: 'force' | 'circular' | 'radial';
    nodeSizeMode: 'degree' | 'uniform';
    showLabels: boolean;
    onselectNode: (node: KGNode) => void;
    onhoverNode: (node: KGNode | null) => void;
    ondeselect: () => void;
    onfitToView: () => void;
  } = $props();

  let container: HTMLDivElement | undefined = $state();
  let graph: ForceGraph3DInstance<GraphNode, GraphLink> | null = null;
  let degreeMap = new Map<string, number>();
  // Cached max degree for node sizing (avoids recomputing on every node)
  let maxDegree = 1;
  // Map: nodeId → the hub it belongs to (its highest-degree neighbor, or itself if it's a hub)
  let hubMap = new Map<string, string>();
  // Set of hub node IDs (nodes with degree above median)
  let hubSet = new Set<string>();
  let tooltipEl: HTMLDivElement | undefined = $state();
  let highlightNodes = new Set<string>();
  let highlightEdges = new Set<string>();
  /** Track the current selection ring mesh so we can remove it without traversing the scene */
  let currentSelectionRing: { parentObj: THREE.Object3D; ring: THREE.Mesh } | null = null;
  let activeHoverId = $state<string | null>(null);
  let rafId = 0;

  // Track per-node opacity for fade animation
  // nodeId → current opacity (0 = hidden, 1 = visible)
  const nodeOpacity = new Map<string, number>();
  // nodeId → target opacity (0, 0.4, or 1)
  const nodeTargetOpacity = new Map<string, number>();
  // Animation frame ID for persistent opacity animation loop
  let opacityRafId = 0;

  function hashColor(label: string): string {
    let hash = 0;
    for (let i = 0; i < label.length; i++) {
      hash = (hash * 31 + label.charCodeAt(i)) | 0;
    }
    return FALLBACK_COLORS[Math.abs(hash) % FALLBACK_COLORS.length];
  }

  function getNodeColor(node: KGNode): string {
    // Prefer entity_type property for deterministic type-based coloring
    const entityType = (node.properties?.entity_type as string)?.toLowerCase();
    if (entityType && TYPE_COLORS[entityType]) {
      return TYPE_COLORS[entityType];
    }
    // Fall back: match on labels (e.g. "Person", "Event", "Image")
    const label = node.labels?.[0]?.toLowerCase() ?? '';
    if (TYPE_COLORS[label]) {
      return TYPE_COLORS[label];
    }
    // Unknown type — hash the label for a stable but non-special color
    return hashColor(node.labels?.[0] ?? 'default');
  }

  function getNodeLabel(node: KGNode): string {
    const props = node.properties ?? {};
    return (props.name as string) ?? (props.title as string) ?? (props.id as string) ?? node.id;
  }

  function buildDegreeMap() {
    degreeMap.clear();
    for (const e of edges) {
      degreeMap.set(e.source, (degreeMap.get(e.source) ?? 0) + 1);
      degreeMap.set(e.target, (degreeMap.get(e.target) ?? 0) + 1);
    }

    // Cache max degree for smooth node sizing
    maxDegree = Math.max(...degreeMap.values(), 1);

    // Determine hubs: nodes with degree >= median, or at least top hubs
    const degrees = Array.from(degreeMap.values()).sort((a, b) => a - b);
    const medianDeg = degrees.length > 0 ? degrees[Math.floor(degrees.length / 2)] : 1;

    hubSet.clear();
    hubMap.clear();

    for (const [nodeId, deg] of degreeMap) {
      if (deg >= medianDeg) {
        hubSet.add(nodeId);
      }
    }

    // Ensure at least one hub exists
    if (hubSet.size === 0 && degrees.length > 0) {
      const maxNodeId = Array.from(degreeMap.entries()).reduce((a, b) => b[1] > a[1] ? b : a)[0];
      hubSet.add(maxNodeId);
    }

    // Build adjacency map for O(1) neighbor lookup instead of O(E) per node
    const adjacency = new Map<string, Array<string>>();
    for (const e of edges) {
      if (!adjacency.has(e.source)) adjacency.set(e.source, []);
      if (!adjacency.has(e.target)) adjacency.set(e.target, []);
      adjacency.get(e.source)!.push(e.target);
      adjacency.get(e.target)!.push(e.source);
    }

    // Assign each node to its highest-degree neighbor (or itself if it's a hub)
    for (const n of nodes) {
      // If this node is a hub, it belongs to itself
      if (hubSet.has(n.id)) {
        hubMap.set(n.id, n.id);
        continue;
      }

      // Find the highest-degree neighbor using the adjacency map
      const neighbors = adjacency.get(n.id) ?? [];
      let bestHub: string | null = null;
      let bestHubDeg = 0;

      for (const neighborId of neighbors) {
        const neighborDeg = degreeMap.get(neighborId) ?? 1;
        if (neighborDeg > bestHubDeg) {
          bestHubDeg = neighborDeg;
          bestHub = neighborId;
        }
      }

      // If no neighbor found (isolated node), point to itself
      hubMap.set(n.id, bestHub ?? n.id);
    }
  }

  function getNodeSize(nodeId: string): number {
    const deg = degreeMap.get(nodeId) ?? 1;
    if (nodeSizeMode === 'uniform') return BASE_RADIUS * 0.6;
    // Scale node size smoothly based on connected entity count.
    // Nodes with the most connections are the largest, surrounded by smaller neighbors.
    // Use degree ratio so sizes are relative to the graph's degree distribution.
    const ratio = deg / maxDegree; // 0..1, where 1 = most-connected node
    // Interpolate from min size (low-degree) to max size (high-degree) using sqrt for smoothness
    const minSize = BASE_RADIUS * 0.35;
    const maxSize = MAX_RADIUS;
    return minSize + (maxSize - minSize) * Math.sqrt(ratio);
  }

  function buildGraphData(): { nodes: GraphNode[]; links: GraphLink[] } {
    const maxDeg = Array.from(degreeMap.values()).reduce((a, b) => Math.max(a, b), 1);

    // Preserve existing node positions from the current graph
    const positionCache = new Map<string, { x: number; y: number; z: number }>();
    if (graph) {
      const currentData = graph.graphData();
      for (const n of currentData.nodes as GraphNode[]) {
        if (n.x !== undefined && n.y !== undefined && n.z !== undefined) {
          positionCache.set(String(n.id), { x: n.x, y: n.y, z: n.z });
        }
      }
    }

    // When an entity is highlighted, position new visible nodes near it
    // so the cluster forms around the selected entity instead of orbiting distant hubs
    let highlightCenter: { x: number; y: number; z: number } | null = null;
    if (highlightedIds.size > 0) {
      // Try to find a cached position for the highlighted node
      for (const hid of highlightedIds) {
        const cached = positionCache.get(hid);
        if (cached) {
          highlightCenter = cached;
          break;
        }
      }
      // If the highlighted node is also new (no cached position),
      // place it at the center of the view
      if (!highlightCenter) {
        highlightCenter = { x: 0, y: 0, z: 0 };
      }
    }

    // Position hubs first in a spread-out layout
    const hubPositions = new Map<string, { x: number; y: number; z: number }>();
    const hubs = Array.from(hubSet);
    hubs.forEach((hubId, i) => {
      if (positionCache.has(hubId)) {
        hubPositions.set(hubId, positionCache.get(hubId)!);
      } else {
        const angle = (2 * Math.PI * i) / hubs.length;
        const radius = hubs.length <= 1 ? 0 : 100 + hubs.length * 10;
        hubPositions.set(hubId, {
          x: radius * Math.cos(angle),
          y: radius * Math.sin(angle),
          z: (Math.random() - 0.5) * 20
        });
      }
    });

    // Count satellites per hub for orbit sizing
    const satellitesPerHub = new Map<string, number>();
    for (const n of nodes) {
      const hub = hubMap.get(n.id) ?? n.id;
      satellitesPerHub.set(hub, (satellitesPerHub.get(hub) ?? 0) + 1);
    }

    // Track orbit index per hub for distributing satellites
    const orbitIndex = new Map<string, number>();
    // Track how many new visible nodes have been placed near the highlight center
    let newVisibleIndex = 0;

    const graphNodes: GraphNode[] = nodes.map((n) => {
      const cached = positionCache.get(n.id);
      const isNew = !positionCache.has(n.id);
      const isVisible = visibleNodeIds.has(n.id);

      let x: number, y: number, z: number;
      if (cached) {
        x = cached.x;
        y = cached.y;
        z = cached.z;
      } else if (highlightCenter && isVisible) {
        // New visible node with a highlighted center — place near the selected entity
        // in a tight cluster so the force simulation can settle them naturally
        const isHighlighted = highlightedIds.has(n.id);
        if (isHighlighted) {
          // The highlighted node goes at the exact center
          x = highlightCenter.x;
          y = highlightCenter.y;
          z = highlightCenter.z;
        } else {
          // Neighbors spread out in a cluster around the center
          const idx = newVisibleIndex++;
          const angle = (2 * Math.PI * idx) / Math.max(visibleNodeIds.size - 1, 1);
          const spread = 8 + Math.sqrt(visibleNodeIds.size) * 3;
          x = highlightCenter.x + spread * Math.cos(angle) + (Math.random() - 0.5) * 2;
          y = highlightCenter.y + spread * Math.sin(angle) + (Math.random() - 0.5) * 2;
          z = highlightCenter.z + (Math.random() - 0.5) * 4;
        }
      } else {
        const hubId = hubMap.get(n.id) ?? n.id;
        const hubPos = hubPositions.get(hubId);

        if (hubSet.has(n.id)) {
          // Hub node — use its hub position directly
          const pos = hubPositions.get(n.id)!;
          x = pos.x;
          y = pos.y;
          z = pos.z;
        } else if (hubPos) {
          // Satellite — orbit around its hub
          const idx = orbitIndex.get(hubId) ?? 0;
          orbitIndex.set(hubId, idx + 1);
          const totalSatellites = satellitesPerHub.get(hubId) ?? 1;
          const hubSize = getNodeSize(hubId);
          const orbitRadius = hubSize + 8 + Math.sqrt(totalSatellites) * 10;
          const angle = (2 * Math.PI * idx) / totalSatellites;
          const jitter = 0.8;
          x = hubPos.x + orbitRadius * Math.cos(angle) + (Math.random() - 0.5) * jitter;
          y = hubPos.y + orbitRadius * Math.sin(angle) + (Math.random() - 0.5) * jitter;
          z = hubPos.z + (Math.random() - 0.5) * 8;
        } else {
          // Fallback: random position near center
          x = (Math.random() - 0.5) * 30;
          y = (Math.random() - 0.5) * 30;
          z = (Math.random() - 0.5) * 15;
        }
      }

      // Initialize opacity tracking for new nodes
      if (!nodeOpacity.has(n.id)) {
        // Visible nodes start at opacity 1 immediately so they're rendered;
        // hidden nodes start at 0. The visibility effect may adjust this.
        nodeOpacity.set(n.id, isVisible ? 1 : 0);
      }
      nodeTargetOpacity.set(n.id, isVisible ? 1 : 0);

      return {
        id: n.id,
        labels: n.labels,
        properties: n.properties,
        val: getNodeSize(n.id),
        color: getNodeColor(n),
        x, y, z
      };
    });

    const graphLinks: GraphLink[] = edges.map((e) => ({
      source: e.source,
      target: e.target,
      id: e.id,
      type: e.type,
      properties: e.properties,
      color: BORDER_COLOR
    }));

    return { nodes: graphNodes, links: graphLinks };
  }

  function applyClusterForce() {
    if (!graph) return;

    if (layout === 'force') {
      // Build a lookup map for hub positions (much faster than .find() per tick)
      const hubPositionLookup = new Map<string, { x: number; y: number; z: number }>();
      const data = graph.graphData();
      for (const node of data.nodes as GraphNode[]) {
        const nodeId = String(node.id);
        if (hubSet.has(nodeId) && node.x !== undefined) {
          hubPositionLookup.set(nodeId, { x: node.x!, y: node.y!, z: node.z! });
        }
      }

      // Strong force pulling satellites toward their hub
      graph.d3Force('clusterX', ((alpha: number) => {
        const currentData = graph?.graphData();
        if (!currentData) return;
        for (const node of currentData.nodes as GraphNode[]) {
          const nodeId = String(node.id);
          const hubId = hubMap.get(nodeId);
          if (!hubId || hubId === nodeId) continue; // hubs don't get pulled

          const hubPos = hubPositionLookup.get(hubId);
          if (!hubPos) continue;

          const dx = (node.x ?? 0) - hubPos.x;
          const dy = (node.y ?? 0) - hubPos.y;
          const dz = (node.z ?? 0) - hubPos.z;
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;

          // Desired orbit radius scales with hub visual size + connection count
          const hubSize = getNodeSize(hubId);
          const orbitRadius = hubSize + 8 + Math.sqrt(degreeMap.get(hubId) ?? 1) * 6;

          // Strong pull toward orbit radius around hub
          if (dist > orbitRadius) {
            const pullStrength = 0.15;
            const factor = (dist - orbitRadius) / dist;
            node.vx = (node.vx ?? 0) - dx * factor * alpha * pullStrength;
            node.vy = (node.vy ?? 0) - dy * factor * alpha * pullStrength;
            node.vz = (node.vz ?? 0) - dz * factor * alpha * pullStrength;
          } else if (dist < orbitRadius * 0.3) {
            // Too close — push away gently
            const pushStrength = 0.05;
            node.vx = (node.vx ?? 0) + dx * alpha * pushStrength;
            node.vy = (node.vy ?? 0) + dy * alpha * pushStrength;
            node.vz = (node.vz ?? 0) + dz * alpha * pushStrength;
          }
        }
      }) as never);
    }
  }

  function applyCircularLayout() {
    if (!graph) return;
    const data = graph.graphData();
    const n = data.nodes.length;
    if (!n) return;

    const nodesArr = data.nodes as GraphNode[];
    nodesArr.forEach((node, i) => {
      const phi = Math.acos(1 - (2 * (i + 0.5)) / n);
      const theta = Math.PI * (1 + Math.sqrt(5)) * i;
      const radius = Math.max(n, 25);
      node.x = radius * Math.cos(theta) * Math.sin(phi);
      node.y = radius * Math.sin(theta) * Math.sin(phi);
      node.z = radius * Math.cos(phi);
    });

    graph.graphData(data);
  }

  function applyRadialLayout() {
    if (!graph) return;
    const data = graph.graphData();
    const n = data.nodes.length;
    if (!n) return;

    const nodesArr = data.nodes as GraphNode[];
    const groups = new Map<string, GraphNode[]>();
    nodesArr.forEach((node) => {
      const label = node.labels?.[0] ?? 'default';
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label)!.push(node);
    });

    const groupArr = Array.from(groups.entries());
    const totalGroups = groupArr.length;

    groupArr.forEach(([, groupNodes], gi) => {
      const zDepth = (gi - totalGroups / 2) * 20;
      const ringRadius = Math.max(groupNodes.length * 3, 15);

      groupNodes.forEach((node, ni) => {
        const angle = (2 * Math.PI * ni) / groupNodes.length;
        node.x = ringRadius * Math.cos(angle);
        node.y = ringRadius * Math.sin(angle);
        node.z = zDepth;
      });
    });

    graph.graphData(data);
  }

  function createNodeThreeObject(node: GraphNode): THREE.Object3D {
    const group = new THREE.Group();
    const nodeId = String(node.id);
    const size = getNodeSize(nodeId);
    const color = hashColor(node.labels?.[0] ?? 'default');
    const opacity = nodeOpacity.get(nodeId) ?? 0;

    // Photo nodes get a distinctive cyan color; the image is shown as an HTML overlay
    const isPhoto = node.labels?.includes('Photo') || (node.properties?.entity_type === 'Photo') || nodeId?.includes('(Photo)');
    const nodeColor = isPhoto ? '#00ffff' : color;

    const geometry = new THREE.SphereGeometry(size, 16, 16);
    const material = new THREE.MeshLambertMaterial({
      color: new THREE.Color(nodeColor),
      transparent: true,
      opacity
    });
    const mesh = new THREE.Mesh(geometry, material);
    group.add(mesh);

    if (showLabels) addLabel(group, node, nodeColor, size, opacity);

    // Store references for opacity animation
    (group as unknown as Record<string, unknown>).__nodeId = nodeId;
    (group as unknown as Record<string, unknown>).__fadeMaterial = material;
    (group as unknown as Record<string, unknown>).__fadeSprite = group.children.find(c => c instanceof THREE.Sprite);

    return group;
  }

  function addLabel(group: THREE.Group, node: GraphNode, color: string, size: number, opacity: number) {
    const label = getNodeLabel(node);
    const canvas2d = document.createElement('canvas');
    const ctx = canvas2d.getContext('2d');
    if (!ctx) return;

    const fontSize = 48;
    ctx.font = `${fontSize}px Inter, system-ui, sans-serif`;
    const textMetrics = ctx.measureText(label);
    canvas2d.width = Math.max(textMetrics.width + 20, 64);
    canvas2d.height = fontSize + 16;

    ctx.font = `${fontSize}px Inter, system-ui, sans-serif`;
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, canvas2d.width / 2, canvas2d.height / 2);

    const texture = new THREE.CanvasTexture(canvas2d);
    texture.needsUpdate = true;
    const spriteMat = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      opacity,
      depthTest: false
    });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.position.y = size + 1.5;
    sprite.scale.set(canvas2d.width / fontSize * 0.5, canvas2d.height / fontSize * 0.5, 1);
    group.add(sprite);
  }

  /** Animate per-node opacity each frame */
  function tickOpacity() {
    if (!graph) return;
    const scene = graph.scene();
    const now = performance.now();
    const hasHighlights = highlightedIds.size > 0;

    scene.traverse((obj: THREE.Object3D) => {
      const rec = obj as unknown as Record<string, unknown>;
      const nodeId = rec.__nodeId as string | undefined;
      if (!nodeId) return;

      const target = nodeTargetOpacity.get(nodeId) ?? 0;
      const current = nodeOpacity.get(nodeId) ?? 0;

      // Animate toward target
      let next: number;
      if (current < target) {
        next = Math.min(current + 0.06, target); // fade in: ~16 frames
      } else if (current > target) {
        next = Math.max(current - 0.08, target); // fade out: ~12 frames
      } else {
        // Already at target — but still need to update emissive for highlight changes
        const material = rec.__fadeMaterial as THREE.MeshLambertMaterial | undefined;
        if (material) {
          if (hasHighlights && highlightedIds.has(nodeId)) {
            material.emissive = new THREE.Color(CYAN);
            material.emissiveIntensity = 0.8;
          } else {
            material.emissive = new THREE.Color(0x000000);
            material.emissiveIntensity = 0;
          }
        }
        return;
      }

      nodeOpacity.set(nodeId, next);

      // Apply to mesh material
      const material = rec.__fadeMaterial as THREE.MeshLambertMaterial | undefined;
      if (material) {
        material.opacity = next;
        // Highlighted nodes get a cyan emissive glow
        if (hasHighlights && highlightedIds.has(nodeId)) {
          material.emissive = new THREE.Color(CYAN);
          material.emissiveIntensity = 0.8;
        } else {
          material.emissive = new THREE.Color(0x000000);
          material.emissiveIntensity = 0;
        }
      }

      // Apply to sprite (label)
      const sprite = rec.__fadeSprite as THREE.Sprite | undefined;
      if (sprite) {
        const spriteMat = sprite.material as THREE.SpriteMaterial;
        spriteMat.opacity = next;
      }

      // Toggle visibility: fully hidden nodes should not be pickable
      obj.visible = next > 0.01;
    });

    // Also update link visibility based on whether both endpoints are visible
    const data = graph.graphData();
    const links = data.links as GraphLink[];
    // ForceGraph3D manages links internally, we control via linkVisibility
  }

  function updateHighlight() {
    if (!graph) return;

    const activeId = selectedNode?.id ?? hoveredNode?.id;

    if (!activeId) {
      highlightNodes.clear();
      highlightEdges.clear();
      activeHoverId = null;
    } else {
      highlightNodes = new Set<string>();
      highlightNodes.add(activeId);
      highlightEdges = new Set<string>();

      for (const e of edges) {
        if (e.source === activeId || e.target === activeId) {
          highlightEdges.add(e.id);
          highlightNodes.add(e.source);
          highlightNodes.add(e.target);
        }
      }
      activeHoverId = activeId;
    }
  }

  function updateSelectionRing() {
    if (!graph) return;
    const scene = graph.scene();
    const selId = selectedNode?.id ?? null;

    // Remove existing ring if present
    if (currentSelectionRing) {
      const { parentObj, ring } = currentSelectionRing;
      ring.geometry?.dispose();
      (ring.material as THREE.Material)?.dispose();
      parentObj.remove(ring);
      currentSelectionRing = null;
    }

    if (!selId) return;

    // Find only the selected node's object — no full scene traversal
    let targetObj: THREE.Object3D | null = null;
    for (const child of scene.children) {
      const rec = child as unknown as Record<string, unknown>;
      if (rec.__nodeId === selId) {
        targetObj = child;
        break;
      }
    }
    // ForceGraph3D stores node objects as direct children of a group, check one level deeper
    if (!targetObj) {
      for (const group of scene.children) {
        if (group.children) {
          for (const child of group.children) {
            const rec = child as unknown as Record<string, unknown>;
            if (rec.__nodeId === selId) {
              targetObj = child;
              break;
            }
          }
        }
        if (targetObj) break;
      }
    }

    if (!targetObj) return;

    const size = getNodeSize(selId);
    const ringGeo = new THREE.RingGeometry(size * 1.4, size * 1.8, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color('#ffffff'),
      transparent: true,
      opacity: 0.7,
      side: THREE.DoubleSide
    });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.lookAt(new THREE.Vector3(0, 0, 1));
    (ring as unknown as Record<string, unknown>).__isSelectionRing = true;
    targetObj.add(ring);
    currentSelectionRing = { parentObj: targetObj, ring };
  }

  function setupGraph() {
    if (!container) return;

    const width = container.clientWidth;
    const height = container.clientHeight;

    const fg = new ForceGraph3D(container, {
      rendererConfig: { antialias: true, alpha: true }
    }) as unknown as ForceGraph3DInstance<GraphNode, GraphLink>;

    graph = fg
      .backgroundColor(BG_COLOR)
      .width(width)
      .height(height)
      .showNavInfo(false)
      .enableNodeDrag(false)
      .enablePointerInteraction(true)
      .linkVisibility(() => false)
      .linkOpacity(0)
      .linkColor(BORDER_COLOR)
      .linkWidth(0)
      .linkDirectionalArrowLength(0)
      .nodeVisibility((node: NodeObject) => {
        const nodeId = String(node.id);
        const opacity = nodeOpacity.get(nodeId) ?? 0;
        return opacity > 0.01;
      })
      .linkVisibility((link: LinkObject<GraphNode>) => {
        const linkObj = link as GraphLink;
        const sourceId = String(linkObj.source);
        const targetId = String(linkObj.target);
        const sourceVis = nodeTargetOpacity.get(sourceId) ?? 0;
        const targetVis = nodeTargetOpacity.get(targetId) ?? 0;
        return sourceVis > 0.01 && targetVis > 0.01;
      })
      .d3AlphaDecay(0.1)
      .d3VelocityDecay(0.3)
      .d3AlphaMin(0.05)
      .warmupTicks(50)
      .cooldownTicks(100)
      .cooldownTime(3000)
      .onEngineStop(() => {
        // Freeze simulation once layout settles — prevent any drift on interaction
        fg.d3AlphaDecay(1);
        fg.d3VelocityDecay(1);
        fg.d3AlphaMin(0.5);
      })
      .onNodeClick((node: NodeObject) => {
        const gn = node as GraphNode;
        selectedNode = gn;
        onselectNode(gn);
      })
      .onNodeHover((node: NodeObject | null) => {
        if (node) {
          const gn = node as GraphNode;
          hoveredNode = gn;
          onhoverNode(gn);
        } else {
          hoveredNode = null;
          onhoverNode(null);
        }
        updateTooltip(node as GraphNode | null);
      })
      .onBackgroundClick(() => {
        selectedNode = null;
        ondeselect();
      })
      .nodeThreeObject((node: NodeObject) => createNodeThreeObject(node as GraphNode))
      .nodeThreeObjectExtend(false)
      .nodeVal((node: NodeObject) => getNodeSize(String(node.id)))
      .graphData(buildGraphData());

    applyClusterForce();

    fg.d3Force('link')?.distance(40);
    fg.d3Force('charge')?.strength(-80);

    fg.d3Force('center', null);
    fg.d3Force('center', ((alpha: number) => {
      const data = fg.graphData();
      let cx = 0, cy = 0, cz = 0;
      const n = data.nodes.length;
      if (!n) return;
      for (const node of data.nodes as GraphNode[]) {
        cx += node.x ?? 0;
        cy += node.y ?? 0;
        cz += node.z ?? 0;
      }
      cx /= n; cy /= n; cz /= n;
      for (const node of data.nodes as GraphNode[]) {
        node.vx = (node.vx ?? 0) - cx * alpha * 0.1;
        node.vy = (node.vy ?? 0) - cy * alpha * 0.1;
        node.vz = (node.vz ?? 0) - cz * alpha * 0.1;
      }
    }) as never);

    setTimeout(() => {
      fg.zoomToFit(500, 20);
    }, 1000);
  }

  function updateTooltip(node: GraphNode | null) {
    if (!tooltipEl) return;

    if (!node) {
      tooltipEl.style.display = 'none';
      return;
    }

    const label = getNodeLabel(node);
    const color = hashColor(node.labels?.[0] ?? 'default');
    const labels = node.labels ?? [];

    let html = `<div style="font-weight:700;color:${color};margin-bottom:4px;">${label}</div>`;
    if (labels.length) {
      html += '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px;">';
      for (const l of labels) {
        const lc = hashColor(l);
        html += `<span style="background:${lc}22;color:${lc};padding:1px 6px;border-radius:4px;font-size:10px;border:1px solid ${lc}44;">${l}</span>`;
      }
      html += '</div>';
    }

    const props = node.properties ?? {};
    const entries = Object.entries(props).slice(0, 5);
    if (entries.length) {
      html += '<div style="font-size:10px;color:#5a6b80;margin-top:2px;">';
      for (const [k, v] of entries) {
        if (k === 'name' || k === 'title') continue;
        html += `<div><span style="color:#8a9bb5;">${k}:</span> ${String(v).slice(0, 40)}</div>`;
      }
      html += '</div>';
    }

    tooltipEl.innerHTML = html;
    tooltipEl.style.display = 'block';
  }

  function positionTooltip() {
    if (!tooltipEl || !graph || !activeHoverId) return;

    const data = graph.graphData();
    let hoveredGraphNode: GraphNode | undefined;
    for (const n of data.nodes as GraphNode[]) {
      if (String(n.id) === activeHoverId) {
        hoveredGraphNode = n;
        break;
      }
    }

    if (!hoveredGraphNode || hoveredGraphNode.x === undefined) {
      tooltipEl.style.display = 'none';
      return;
    }

    try {
      const coords = graph.graph2ScreenCoords(
        hoveredGraphNode.x!,
        hoveredGraphNode.y!,
        hoveredGraphNode.z!
      );
      const rect = container?.getBoundingClientRect();
      if (rect) {
        tooltipEl.style.left = `${coords.x - rect.left + 15}px`;
        tooltipEl.style.top = `${coords.y - rect.top - 10}px`;
      }
    } catch {
      tooltipEl.style.display = 'none';
    }
  }

  function refreshGraph() {
    if (!graph) return;
    buildDegreeMap();
    graph.graphData(buildGraphData());
    applyClusterForce();
    updateHighlight();
  }

  // Only re-register nodeThreeObject when showLabels changes.
  let prevShowLabels = showLabels;
  $effect(() => {
    if (!graph) return;
    if (showLabels !== prevShowLabels) {
      prevShowLabels = showLabels;
      graph.nodeThreeObject((node: NodeObject) => createNodeThreeObject(node as GraphNode));
    }
  });

  // Update highlights when selection or hover changes
  $effect(() => {
    const _selected = selectedNode;
    const _hovered = hoveredNode;
    if (graph) {
      updateHighlight();
    }
  });

  // Selection ring only updates on selection change, not hover
  $effect(() => {
    const _selected = selectedNode;
    if (graph) {
      updateSelectionRing();
    }
  });

  // React to node/edge data changes (only on API load, NOT on hover)
  $effect(() => {
    const _nodes = nodes;
    const _edges = edges;
    const _layout = layout;
    const _mode = nodeSizeMode;

    if (graph) {
      buildDegreeMap();

      if (_layout === 'circular') {
        applyCircularLayout();
      } else if (_layout === 'radial') {
        applyRadialLayout();
      } else {
        refreshGraph();
      }
    }
  });

  // React to visibility changes — only update opacity targets, no graph rebuild
  $effect(() => {
    const _visible = visibleNodeIds;
    const _highlighted = highlightedIds;

    if (!graph) return;

    const hasHighlights = _highlighted.size > 0;

    // Update target opacity for all known nodes
    for (const n of nodes) {
      if (hasHighlights && _highlighted.has(n.id)) {
        // Highlighted node: full opacity + cyan glow
        nodeTargetOpacity.set(n.id, 1);
      } else if (hasHighlights && _visible.has(n.id)) {
        // Visible but NOT highlighted (connected neighbor): slightly dimmed
        nodeTargetOpacity.set(n.id, 0.7);
      } else if (_visible.has(n.id)) {
        // No highlights active: normal full opacity for visible nodes
        nodeTargetOpacity.set(n.id, 1);
      } else {
        // Hidden node
        nodeTargetOpacity.set(n.id, 0);
      }
    }

    // Sync visibility immediately for nodes not yet in opacity map (they start hidden)
    for (const n of nodes) {
      if (!nodeOpacity.has(n.id)) {
        nodeOpacity.set(n.id, _visible.has(n.id) ? (hasHighlights && !_highlighted.has(n.id) ? 0.7 : 1) : 0);
      }
    }

    // Force 3d-force-graph to re-evaluate nodeVisibility and linkVisibility
    // callbacks, since they depend on nodeOpacity/nodeTargetOpacity which we
    // just changed.
    graph.refresh();

    // Links are hidden — clustering layout only
  });

  $effect(() => {
    if (!container) return;

    const observer = new ResizeObserver(() => {
      if (!graph || !container) return;
      graph.width(container.clientWidth).height(container.clientHeight);
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
    };
  });

  $effect(() => {
    const _hover = activeHoverId;

    cancelAnimationFrame(rafId);
    function tick() {
      positionTooltip();
      rafId = requestAnimationFrame(tick);
    }
    if (_hover) {
      tick();
    } else {
      positionTooltip();
    }

    return () => cancelAnimationFrame(rafId);
  });

  // Persistent opacity animation loop — runs every frame so highlights
  // and visibility changes animate even after the force simulation stops.
  $effect(() => {
    const _ = nodes; // depend on nodes so rebuild re-triggers
    if (!graph) return;

    function animLoop() {
      tickOpacity();
      opacityRafId = requestAnimationFrame(animLoop);
    }
    animLoop();

    return () => cancelAnimationFrame(opacityRafId);
  });

  export function zoomIn() {
    if (!graph) return;
    const camera = graph.camera();
    const newPos = camera.position.clone().multiplyScalar(0.7);
    graph.cameraPosition(newPos, undefined, 400);
  }

  export function zoomOut() {
    if (!graph) return;
    const camera = graph.camera();
    const newPos = camera.position.clone().multiplyScalar(1.4);
    graph.cameraPosition(newPos, undefined, 400);
  }

  export function fitView() {
    if (!graph) return;
    graph.zoomToFit(500, 20);
  }

  export function getGraph() {
    return graph;
  }

  export function focusNode(nodeId: string) {
    if (!graph) return;
    const data = graph.graphData();
    const node = data?.nodes?.find((n: NodeObject) => String(n.id) === nodeId);
    if (!node || node.x === undefined) return;
    graph.cameraPosition(
      { x: node.x, y: node.y, z: node.z },
      undefined,
      600
    );
  }

  onMount(() => {
    if (typeof window === 'undefined') return;
    buildDegreeMap();
    setupGraph();

    return () => {
      cancelAnimationFrame(rafId);
      cancelAnimationFrame(opacityRafId);
      if (graph) {
        graph._destructor();
        graph = null;
      }
    };
  });
</script>

<div bind:this={container} data-testid="graph-canvas" class="w-full h-full relative overflow-hidden">
  <div
    bind:this={tooltipEl}
    style="display:none; position:absolute; z-index:50; pointer-events:none; background:rgba(17,24,39,0.95); border:1px solid #1e2d45; border-radius:8px; padding:8px 12px; font-size:12px; color:#c8d6e5; max-width:250px; backdrop-filter:blur(8px); box-shadow:0 4px 20px rgba(0,0,0,0.5);"
  ></div>
</div>

<style>
  :global(.force-graph-container canvas) {
    outline: none !important;
  }
</style>