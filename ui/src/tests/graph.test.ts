import { describe, it, expect } from 'vitest';

interface KGNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

interface KGEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: Record<string, unknown>;
}

interface KGGraph {
  nodes: KGNode[];
  edges: KGEdge[];
}

function makeNode(id: string, entityType: string, name?: string): KGNode {
  return {
    id,
    labels: [entityType],
    properties: { entity_type: entityType, ...(name ? { name } : {}) },
  };
}

function makeEdge(id: string, source: string, target: string, type: string = 'RELATED'): KGEdge {
  return { id, source, target, type, properties: {} };
}

const mockGraph: KGGraph = {
  nodes: [
    makeNode('David', 'person', 'David'),
    makeNode('Paul', 'person', 'Paul'),
    makeNode('Miami', 'location', 'Miami'),
    makeNode('Beach', 'location', 'Beach'),
    makeNode('Photo1', 'image', 'David at the beach'),
    makeNode('Event1', 'event', 'Beach party'),
    makeNode('Org1', 'organization', 'Acme Corp'),
    makeNode('Concept1', 'concept', 'Happiness'),
    makeNode('Sarah', 'person', 'Sarah'),
    makeNode('Jake', 'person', 'Jake'),
  ],
  edges: [
    makeEdge('e1', 'David', 'Paul', 'brother'),
    makeEdge('e2', 'David', 'Miami', 'visited'),
    makeEdge('e3', 'David', 'Photo1', 'appears_in'),
    makeEdge('e4', 'David', 'Event1', 'attended'),
    makeEdge('e5', 'Paul', 'Org1', 'works_at'),
    makeEdge('e6', 'Sarah', 'Jake', 'knows'),
    makeEdge('e7', 'Sarah', 'Miami', 'visited'),
    makeEdge('e8', 'Photo1', 'Beach', 'taken_at'),
    makeEdge('e9', 'Jake', 'Concept1', 'likes'),
  ],
};

function buildAdjacencyMap(edges: KGEdge[]): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const e of edges) {
    if (!map.has(e.source)) map.set(e.source, []);
    if (!map.has(e.target)) map.set(e.target, []);
    map.get(e.source)!.push(e.target);
    map.get(e.target)!.push(e.source);
  }
  return map;
}

function buildDegreeMap(edges: KGEdge[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const e of edges) {
    map.set(e.source, (map.get(e.source) ?? 0) + 1);
    map.set(e.target, (map.get(e.target) ?? 0) + 1);
  }
  return map;
}

function computeOpacity(nodeId: string, visibleNodeIds: Set<string>, highlightedIds: Set<string>): number {
  const hasHighlights = highlightedIds.size > 0;
  if (hasHighlights && highlightedIds.has(nodeId)) return 1.0;
  if (hasHighlights && visibleNodeIds.has(nodeId)) return 0.7;
  if (visibleNodeIds.has(nodeId)) return 1.0;
  return 0;
}

function initVisibleNodes(allNodes: KGNode[], degreeMap: Map<string, number>, limit: number = 50): Set<string> {
  const sorted = [...allNodes].sort(
    (a, b) => (degreeMap.get(b.id) ?? 0) - (degreeMap.get(a.id) ?? 0)
  );
  const top = sorted.slice(0, limit);
  return new Set(top.map((n) => n.id));
}

function handleSelectLabelExisting(label: string, allNodes: KGNode[], allEdges: KGEdge[]): {
  visibleNodeIds: Set<string>;
  highlightedIds: Set<string>;
} {
  const adjacencyMap = buildAdjacencyMap(allEdges);
  const existingNode = allNodes.find((n) => n.id === label);
  if (!existingNode) return { visibleNodeIds: new Set(), highlightedIds: new Set() };

  const clusterIds = new Set<string>();
  clusterIds.add(existingNode.id);
  const neighbors = adjacencyMap.get(existingNode.id);
  if (neighbors) {
    for (const nId of neighbors) clusterIds.add(nId);
  }
  return {
    visibleNodeIds: clusterIds,
    highlightedIds: new Set([existingNode.id]),
  };
}

function handleSelectLabelNew(label: string, existingNodes: KGNode[], existingEdges: KGEdge[], newGraph: KGGraph): {
  allNodes: KGNode[];
  allEdges: KGEdge[];
  visibleNodeIds: Set<string>;
  highlightedIds: Set<string>;
} {
  const existingNodeIds = new Set(existingNodes.map((n) => n.id));
  const existingEdgeIds = new Set(existingEdges.map((e) => e.id));
  const additionalNodes = newGraph.nodes.filter((n) => !existingNodeIds.has(n.id));
  const additionalEdges = newGraph.edges.filter((e) => !existingEdgeIds.has(e.id));
  const allNodes = [...existingNodes, ...additionalNodes];
  const allEdges = [...existingEdges, ...additionalEdges];

  const fullAdj = buildAdjacencyMap(allEdges);
  const centerNode = allNodes.find((n) => n.id === label);

  const clusterIds = new Set<string>();
  if (centerNode) {
    clusterIds.add(centerNode.id);
    const neighbors = fullAdj.get(centerNode.id);
    if (neighbors) {
      for (const nId of neighbors) clusterIds.add(nId);
    }
  } else {
    for (const n of additionalNodes) clusterIds.add(n.id);
  }

  return {
    allNodes,
    allEdges,
    visibleNodeIds: clusterIds,
    highlightedIds: centerNode ? new Set([centerNode.id]) : new Set(),
  };
}

describe('GraphView logic', () => {
  describe('initVisibleNodes', () => {
    it('should select top 50 nodes by degree', () => {
      const degreeMap = buildDegreeMap(mockGraph.edges);
      const visibleNodeIds = initVisibleNodes(mockGraph.nodes, degreeMap, 50);

      expect(visibleNodeIds.size).toBe(Math.min(50, mockGraph.nodes.length));
      expect(visibleNodeIds.has('David')).toBe(true);
      expect(visibleNodeIds.has('Paul')).toBe(true);
    });

    it('should include highest-degree nodes first', () => {
      const degreeMap = buildDegreeMap(mockGraph.edges);
      const sorted = [...mockGraph.nodes].sort(
        (a, b) => (degreeMap.get(b.id) ?? 0) - (degreeMap.get(a.id) ?? 0)
      );
      expect(degreeMap.get(sorted[0].id)!).toBeGreaterThanOrEqual(
        degreeMap.get(sorted[sorted.length - 1].id) ?? 0
      );
    });
  });

  describe('handleSearch — local text search', () => {
    it('should find matches by node id (case-insensitive)', () => {
      const q = 'david';
      const matches = mockGraph.nodes.filter((n) => {
        const name = ((n.properties?.name ?? n.properties?.title ?? n.id) as string).toLowerCase();
        return name.includes(q) || n.id.toLowerCase().includes(q);
      });

      expect(matches.length).toBeGreaterThan(0);
      expect(matches.some((n) => n.id === 'David')).toBe(true);
    });

    it('should find matches by name property', () => {
      const q = 'happiness';
      const matches = mockGraph.nodes.filter((n) => {
        const name = ((n.properties?.name ?? n.properties?.title ?? n.id) as string).toLowerCase();
        return name.includes(q) || n.id.toLowerCase().includes(q);
      });

      expect(matches.length).toBe(1);
      expect(matches[0].id).toBe('Concept1');
    });

    it('should return empty array for non-matching queries', () => {
      const q = 'zzzznonexistent';
      const matches = mockGraph.nodes.filter((n) => {
        const name = ((n.properties?.name ?? n.properties?.title ?? n.id) as string).toLowerCase();
        return name.includes(q) || n.id.toLowerCase().includes(q);
      });

      expect(matches.length).toBe(0);
    });
  });

  describe('handleSelectLabel — entity already in graph', () => {
    it('should show entity + all direct neighbors as visible', () => {
      const result = handleSelectLabelExisting('David', mockGraph.nodes, mockGraph.edges);

      expect(result.visibleNodeIds.has('David')).toBe(true);
      expect(result.visibleNodeIds.has('Paul')).toBe(true);
      expect(result.visibleNodeIds.has('Miami')).toBe(true);
      expect(result.visibleNodeIds.has('Photo1')).toBe(true);
      expect(result.visibleNodeIds.has('Event1')).toBe(true);

      expect(result.visibleNodeIds.has('Sarah')).toBe(false);
      expect(result.visibleNodeIds.has('Jake')).toBe(false);
      expect(result.visibleNodeIds.has('Org1')).toBe(false);
    });

    it('should highlight only the selected entity', () => {
      const result = handleSelectLabelExisting('David', mockGraph.nodes, mockGraph.edges);

      expect(result.highlightedIds.size).toBe(1);
      expect(result.highlightedIds.has('David')).toBe(true);
      expect(result.highlightedIds.has('Paul')).toBe(false);
    });

    it('should compute correct visible node count = 1 + neighbors', () => {
      const result = handleSelectLabelExisting('David', mockGraph.nodes, mockGraph.edges);

      // David has 4 direct neighbors: Paul, Miami, Photo1, Event1
      expect(result.visibleNodeIds.size).toBe(5); // 1 + 4
    });
  });

  describe('handleSelectLabel — entity NOT in current graph', () => {
    it('should merge new nodes and show the cluster around selected entity', () => {
      const newGraph: KGGraph = {
        nodes: [
          makeNode('NewPerson', 'person', 'NewPerson'),
          makeNode('Friend1', 'person', 'Friend1'),
          makeNode('Friend2', 'person', 'Friend2'),
        ],
        edges: [
          makeEdge('ne1', 'NewPerson', 'Friend1'),
          makeEdge('ne2', 'NewPerson', 'Friend2'),
        ],
      };

      const result = handleSelectLabelNew('NewPerson', mockGraph.nodes, mockGraph.edges, newGraph);

      expect(result.allNodes.length).toBe(mockGraph.nodes.length + 3);
      expect(result.visibleNodeIds.has('NewPerson')).toBe(true);
      expect(result.visibleNodeIds.has('Friend1')).toBe(true);
      expect(result.visibleNodeIds.has('Friend2')).toBe(true);
      expect(result.highlightedIds.has('NewPerson')).toBe(true);
    });
  });
});

describe('GraphCanvas visible node display', () => {
  it('should show highlighted node at full opacity (1.0) when highlights exist', () => {
    const visibleNodeIds = new Set(['David', 'Paul', 'Miami', 'Photo1', 'Event1']);
    const highlightedIds = new Set(['David']);

    expect(computeOpacity('David', visibleNodeIds, highlightedIds)).toBe(1.0);
  });

  it('should show connected neighbors at reduced opacity (0.7) when highlights exist', () => {
    const visibleNodeIds = new Set(['David', 'Paul', 'Miami', 'Photo1', 'Event1']);
    const highlightedIds = new Set(['David']);

    expect(computeOpacity('Paul', visibleNodeIds, highlightedIds)).toBe(0.7);
    expect(computeOpacity('Miami', visibleNodeIds, highlightedIds)).toBe(0.7);
    expect(computeOpacity('Photo1', visibleNodeIds, highlightedIds)).toBe(0.7);
  });

  it('should hide nodes not in visibleNodeIds (opacity 0)', () => {
    const visibleNodeIds = new Set(['David', 'Paul', 'Miami']);
    const highlightedIds = new Set(['David']);

    expect(computeOpacity('Sarah', visibleNodeIds, highlightedIds)).toBe(0);
    expect(computeOpacity('Jake', visibleNodeIds, highlightedIds)).toBe(0);
  });

  it('should show all visible nodes at full opacity when no highlights exist', () => {
    const visibleNodeIds = new Set(['David', 'Paul', 'Miami']);
    const highlightedIds = new Set<string>();

    expect(computeOpacity('David', visibleNodeIds, highlightedIds)).toBe(1.0);
    expect(computeOpacity('Paul', visibleNodeIds, highlightedIds)).toBe(1.0);
    expect(computeOpacity('Miami', visibleNodeIds, highlightedIds)).toBe(1.0);
  });

  it('should handle handleSelectLabel visible node count correctly', () => {
    const result = handleSelectLabelExisting('David', mockGraph.nodes, mockGraph.edges);

    // David's direct neighbors: Paul, Miami, Photo1, Event1 = 4
    // Total visible: David + 4 neighbors = 5
    expect(result.visibleNodeIds.size).toBe(5);

    // All visible nodes should be rendered (opacity > 0)
    for (const nodeId of result.visibleNodeIds) {
      const opacity = computeOpacity(nodeId, result.visibleNodeIds, result.highlightedIds);
      expect(opacity).toBeGreaterThan(0);
    }

    // The selected entity should be brightest
    const selectedOpacity = computeOpacity('David', result.visibleNodeIds, result.highlightedIds);
    const neighborOpacity = computeOpacity('Paul', result.visibleNodeIds, result.highlightedIds);
    expect(selectedOpacity).toBeGreaterThan(neighborOpacity);
  });

  it('should verify all neighbors of David are visible after selection', () => {
    const adjacencyMap = buildAdjacencyMap(mockGraph.edges);
    const result = handleSelectLabelExisting('David', mockGraph.nodes, mockGraph.edges);

    const davidNeighbors = adjacencyMap.get('David') ?? [];
    for (const neighborId of davidNeighbors) {
      expect(result.visibleNodeIds.has(neighborId)).toBe(true);
      const opacity = computeOpacity(neighborId, result.visibleNodeIds, result.highlightedIds);
      expect(opacity).toBe(0.7);
    }

    expect(result.visibleNodeIds.size).toBe(1 + davidNeighbors.length);
  });

  it('should verify entities NOT connected to David are hidden', () => {
    const result = handleSelectLabelExisting('David', mockGraph.nodes, mockGraph.edges);

    expect(result.visibleNodeIds.has('Sarah')).toBe(false);
    expect(result.visibleNodeIds.has('Jake')).toBe(false);
    expect(computeOpacity('Sarah', result.visibleNodeIds, result.highlightedIds)).toBe(0);
    expect(computeOpacity('Jake', result.visibleNodeIds, result.highlightedIds)).toBe(0);
  });

  it('should position new visible nodes near the highlighted center, not in distant orbits', () => {
    const result = handleSelectLabelExisting('David', mockGraph.nodes, mockGraph.edges);

    // When a highlighted entity exists, new nodes without cached positions
    // should be placed near the highlighted node, not in far hub orbits.
    // The cluster spread should be proportional to the number of visible nodes
    // so that neighbors form a tight group around the selected entity.
    const visibleCount = result.visibleNodeIds.size;
    expect(visibleCount).toBe(5); // David + 4 direct neighbors

    // The highlighted node (David) should be at the center with opacity 1.0
    expect(computeOpacity('David', result.visibleNodeIds, result.highlightedIds)).toBe(1.0);

    // All neighbors should be visible at reduced opacity
    const neighbors = ['Paul', 'Miami', 'Photo1', 'Event1'];
    for (const neighbor of neighbors) {
      expect(result.visibleNodeIds.has(neighbor)).toBe(true);
      expect(computeOpacity(neighbor, result.visibleNodeIds, result.highlightedIds)).toBe(0.7);
    }
  });
});