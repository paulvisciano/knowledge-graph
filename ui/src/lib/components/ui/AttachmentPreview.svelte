<script lang="ts">
  import type { Attachment } from '$lib/utils/file-utils';
  import { isImageType, isTextType } from '$lib/utils/file-utils';

  let {
    attachments = [] as Attachment[],
    onRemove,
  }: {
    attachments: Attachment[];
    onRemove: (id: string) => void;
  } = $props();
</script>

{#if attachments.length > 0}
  <div class="flex flex-wrap gap-2 px-4 pt-2">
    {#each attachments as att (att.id)}
      <div class="group/att relative flex items-center gap-2 rounded-lg border border-cyber-border bg-cyber-surface-2 px-2 py-1.5 text-xs transition-all hover:border-cyber-cyan/40">
        {#if isImageType(att.mimeType) && att.thumbnailUrl}
          <img
            src={att.thumbnailUrl}
            alt={att.name}
            class="h-8 w-8 rounded object-cover"
          />
          <span class="max-w-[100px] truncate text-cyber-text-dim">{att.name}</span>
        {:else if isTextType(att.mimeType)}
          <svg class="h-4 w-4 shrink-0 text-cyber-text-dim" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span class="max-w-[100px] truncate text-cyber-text-dim">{att.name}</span>
        {/if}
        <button
          type="button"
          onclick={() => onRemove(att.id)}
          class="ml-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-cyber-text-dim/60 transition-colors hover:bg-cyber-red/20 hover:text-cyber-red"
          title="Remove attachment"
        >
          <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    {/each}
  </div>
{/if}