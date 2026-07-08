import type { KGNode, KGEdge } from '$lib/constants';
import { lightragClient } from '$lib/services/lightrag-client';
import { eventBus } from './event-bus.svelte';

class GraphStore {
  nodes = $state<KGNode[]>([]);
  edges = $state<KGEdge[]>([]);
  selectedNode: KGNode | null = $state(null);
  hoveredNode: KGNode | null = $state(null);
  searchQuery = $state('');
  isLoading = $state(false);

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
      const graph = await lightragClient.getGraph(label, nodeId, depth);
      this.nodes = graph.nodes;
      this.edges = graph.edges;
      eventBus.pushEvent({
        id: crypto.randomUUID(),
        type: 'graph_update',
        title: 'Graph loaded',
        description: `${graph.nodes.length} nodes, ${graph.edges.length} edges`,
        timestamp: Date.now(),
        status: 'completed',
        meta: { nodeCount: graph.nodes.length, edgeCount: graph.edges.length },
      });
    } catch (err) {
      eventBus.pushEvent({
        id: crypto.randomUUID(),
        type: 'graph_update',
        title: 'Graph load failed',
        description: err instanceof Error ? err.message : 'Unknown error',
        timestamp: Date.now(),
        status: 'error',
      });
    } finally {
      this.isLoading = false;
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

  reset() {
    this.nodes = [];
    this.edges = [];
    this.selectedNode = null;
    this.hoveredNode = null;
    this.searchQuery = '';
  }
}

export const graphStore = new GraphStore();