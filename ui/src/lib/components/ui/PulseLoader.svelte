<script lang="ts">
  let { color = '#00d4ff', size = 6, gap = 4 }: { color?: string; size?: number; gap?: number } = $props();
</script>

<div class="flex items-center" style:gap="{gap}px">
  {#each [0, 1, 2] as i}
    <span
      class="pulse-dot rounded-full"
      style:width="{size}px"
      style:height="{size}px"
      style:background-color={color}
      style:animation-delay="{i * 0.15}s"
    ></span>
  {/each}
</div>

<style>
  .pulse-dot {
    animation: pulse-loader 1.4s ease-in-out infinite;
  }

  @keyframes pulse-loader {
    0%,
    80%,
    100% {
      opacity: 0.3;
      transform: scale(0.8);
    }
    40% {
      opacity: 1;
      transform: scale(1.2);
    }
  }
</style>