<script lang="ts">
  import type { Attachment } from '$lib/utils/file-utils';
  import { fileToAttachment, isImageType, MAX_ATTACHMENTS, revokeAttachmentUrls } from '$lib/utils/file-utils';
  import AttachmentPreview from '$lib/components/ui/AttachmentPreview.svelte';
  import AttachmentMenu from '$lib/components/ui/AttachmentMenu.svelte';

  let {
    onSend,
    disabled = false,
    isStreaming = false,
    onCancel,
    models = [] as string[],
    selectedModel = '',
    onModelChange,
  }: {
    onSend: (text: string, attachments: Attachment[]) => void;
    disabled?: boolean;
    isStreaming?: boolean;
    onCancel?: () => void;
    models?: string[];
    selectedModel?: string;
    onModelChange?: (model: string) => void;
  } = $props();

  let text = $state('');
  let textareaEl: HTMLTextAreaElement | undefined = $state();
  let imageFileInput: HTMLInputElement | undefined = $state();
  let docFileInput: HTMLInputElement | undefined = $state();
  let showModelDropdown = $state(false);
  let attachments = $state<Attachment[]>([]);
  let attachError = $state('');

  function autoResize() {
    if (!textareaEl) return;
    textareaEl.style.height = 'auto';
    const lineH = parseInt(getComputedStyle(textareaEl).lineHeight) || 24;
    const maxH = lineH * 6;
    textareaEl.style.height = Math.min(textareaEl.scrollHeight, maxH) + 'px';
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function send() {
    const trimmed = text.trim();
    if ((!trimmed && attachments.length === 0) || disabled) return;
    onSend(trimmed, [...attachments]);
    text = '';
    revokeAttachmentUrls(attachments);
    attachments = [];
    attachError = '';
    requestAnimationFrame(() => {
      if (textareaEl) {
        textareaEl.style.height = 'auto';
      }
    });
  }

  function openImagePicker() {
    imageFileInput?.click();
  }

  function openDocumentPicker() {
    docFileInput?.click();
  }

  async function handleImageFiles(fileList: FileList | null) {
    if (!fileList) return;
    attachError = '';
    const remaining = MAX_ATTACHMENTS - attachments.length;
    if (remaining <= 0) {
      attachError = `Maximum ${MAX_ATTACHMENTS} attachments allowed`;
      return;
    }
    const files = Array.from(fileList).slice(0, remaining);
    for (const file of files) {
      try {
        const att = await fileToAttachment(file);
        attachments = [...attachments, att];
      } catch (e) {
        attachError = e instanceof Error ? e.message : 'Failed to attach file';
      }
    }
    if (imageFileInput) imageFileInput.value = '';
  }

  async function handleDocFiles(fileList: FileList | null) {
    if (!fileList) return;
    attachError = '';
    const remaining = MAX_ATTACHMENTS - attachments.length;
    if (remaining <= 0) {
      attachError = `Maximum ${MAX_ATTACHMENTS} attachments allowed`;
      return;
    }
    const files = Array.from(fileList).slice(0, remaining);
    for (const file of files) {
      try {
        const att = await fileToAttachment(file);
        attachments = [...attachments, att];
      } catch (e) {
        attachError = e instanceof Error ? e.message : 'Failed to attach file';
      }
    }
    if (docFileInput) docFileInput.value = '';
  }

  function removeAttachment(id: string) {
    const att = attachments.find((a) => a.id === id);
    if (att?.thumbnailUrl) URL.revokeObjectURL(att.thumbnailUrl);
    attachments = attachments.filter((a) => a.id !== id);
  }

  function toggleModelDropdown() {
    showModelDropdown = !showModelDropdown;
  }

  function selectModel(m: string) {
    onModelChange?.(m);
    showModelDropdown = false;
  }
</script>

<div class="border-t border-cyber-border bg-cyber-surface px-4 pb-4 pt-3">
  <div class="mx-auto max-w-3xl">
    {#if models.length > 0}
      <div class="mb-2 flex items-center justify-end">
        <div class="relative">
          <button
            onclick={toggleModelDropdown}
            class="flex items-center gap-1.5 rounded border border-cyber-border px-2 py-1 text-xs text-cyber-text-dim transition-colors hover:border-cyber-cyan/40 hover:text-cyber-cyan"
          >
            <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            <span class="font-mono">{selectedModel || 'Select model'}</span>
            <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {#if showModelDropdown}
            <div class="absolute bottom-full right-0 mb-1 max-h-48 overflow-y-auto rounded-lg border border-cyber-border bg-cyber-surface-2 shadow-lg">
              {#each models as model}
                <button
                  onclick={() => selectModel(model)}
                  class="block w-full px-3 py-1.5 text-left text-xs font-mono text-cyber-text-dim transition-colors hover:bg-cyber-cyan/10 hover:text-cyber-cyan {selectedModel === model ? 'text-cyber-cyan' : ''}"
                >
                  {model}
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {/if}

    <AttachmentPreview {attachments} onRemove={removeAttachment} />

    {#if attachError}
      <div class="mx-4 mb-1 text-xs text-cyber-red">{attachError}</div>
    {/if}

    <div class="flex items-end gap-2">
      <input
        bind:this={imageFileInput}
        type="file"
        accept="image/*"
        multiple
        class="hidden"
        onchange={(e) => handleImageFiles((e.target as HTMLInputElement).files)}
      />
      <input
        bind:this={docFileInput}
        type="file"
        accept=".txt,.md,.csv,.json,.html,.htm,.xml,.yaml,.yml,.log"
        multiple
        class="hidden"
        onchange={(e) => handleDocFiles((e.target as HTMLInputElement).files)}
      />
      <AttachmentMenu
        disabled={disabled || attachments.length >= MAX_ATTACHMENTS}
        onPickImage={openImagePicker}
        onPickDocument={openDocumentPicker}
      />

      <div class="relative flex-1">
        <textarea
          bind:this={textareaEl}
          bind:value={text}
          oninput={autoResize}
          onkeydown={handleKeydown}
          rows="1"
          placeholder={disabled ? 'Waiting for response...' : 'Message Nexus...'}
          disabled={disabled}
          class="w-full resize-none rounded-xl border border-cyber-border bg-cyber-bg px-4 py-2.5 text-sm text-cyber-text placeholder-cyber-text-dim/50 transition-all duration-200 focus:border-cyber-cyan/40 focus:outline-none focus:ring-1 focus:ring-cyber-cyan/20 disabled:cursor-not-allowed disabled:opacity-50"
        ></textarea>
      </div>

      {#if isStreaming && onCancel}
        <button
          onclick={onCancel}
          class="mb-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-cyber-red/40 bg-cyber-red/10 text-cyber-red transition-all duration-200 hover:border-cyber-red/60 hover:bg-cyber-red/20"
          title="Stop generating"
        >
          <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="1.5" />
          </svg>
        </button>
      {:else}
        <button
          onclick={send}
          disabled={disabled || (!text.trim() && attachments.length === 0)}
          class="mb-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-cyber-border bg-cyber-bg text-cyber-cyan transition-all duration-200 hover:border-cyber-cyan/60 hover:glow-cyan disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:glow-none"
          title="Send message"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </button>
      {/if}
    </div>
  </div>
</div>