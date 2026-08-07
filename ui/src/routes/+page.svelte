<script lang="ts">
  import { graphStore } from '$lib/stores/graph.svelte';
  import { activeTab, selectedNodeId } from '$lib/stores/ui';
  import CanvasView from '$lib/components/canvas/CanvasView.svelte';
  import IngestionPanel from '$lib/components/ingestion/IngestionPanel.svelte';
  import NodeDetail from '$lib/components/graph/NodeDetail.svelte';
  import ChatPanel from '$lib/components/canvas/ChatPanel.svelte';

  let chatPanelRef: { handleQueryAbout: (node: { id: string; labels?: string[]; properties?: Record<string, unknown> }) => void; handleSelectConversation: (id: string) => void } = $state({ handleQueryAbout: () => {}, handleSelectConversation: () => {} });
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="relative h-full w-full overflow-hidden">
  {#if $activeTab === 'graph'}
    <div class="absolute inset-0">
      <CanvasView
        onqueryAbout={(node: { id: string; labels?: string[]; properties?: Record<string, unknown> }) => chatPanelRef.handleQueryAbout(node)}
        onselectconversation={(id: string) => chatPanelRef.handleSelectConversation(id)}
      />
    </div>

    <ChatPanel ref={chatPanelRef} />

    {#if $selectedNodeId}
      {@const selNode = graphStore.nodes.find(n => n.id === $selectedNodeId)}
      {@const nbrEdges = graphStore.edges.filter(e => e.source === $selectedNodeId || e.target === $selectedNodeId)}
      {@const nbrIds = new Set(nbrEdges.flatMap(e => [e.source, e.target]).filter(id => id !== $selectedNodeId))}
      {@const nbrNodes = graphStore.nodes.filter(n => nbrIds.has(n.id))}
      <div class="node-detail-overlay">
        <NodeDetail
          node={selNode ?? null}
          neighbors={{ nodes: nbrNodes, edges: nbrEdges }}
          onclose={() => selectedNodeId.set(null)}
        />
      </div>
    {/if}

  {:else if $activeTab === 'ingestion'}
    <div class="h-full w-full overflow-hidden p-2 md:pt-14 md:pl-14 md:pr-4 md:pb-4">
      <IngestionPanel />
    </div>
  {/if}
</div>

<style>
  .node-detail-overlay {
    position: absolute;
    right: 16px;
    top: 16px;
    z-index: 20;
    width: 20rem;
  }

  @media (max-width: 768px) {
    .node-detail-overlay {
      right: 8px;
      left: 8px;
      bottom: 120px;
      top: auto;
      width: auto;
      max-height: 50dvh;
      overflow-y: auto;
    }
  }
</style>