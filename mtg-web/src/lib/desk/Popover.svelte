<script lang="ts">
  let { id, open, title, text }: { id: string; open: boolean; title: string; text: string } =
    $props();
</script>

{#if open}
  <span
    role="tooltip"
    {id}
    class="absolute bottom-[calc(100%+6px)] left-0 z-20 block w-[min(28rem,80vw)] rounded-lg border border-line-strong bg-card px-3.5 py-2.5 text-left font-sans text-sm leading-snug font-normal whitespace-normal text-fg shadow-[0_8px_24px_rgb(0_0_0/0.4)]"
  >
    <span class="block font-mono text-xs text-fg-muted">{title}</span>
    <span class="mt-1 line-clamp-4 block text-fg-body">{text}</span>
  </span>
{/if}
