<script lang="ts">
  let { ruleIds, boxed }: { ruleIds: string[]; boxed: boolean } = $props();
</script>

{#if ruleIds.length}
  <div
    class={boxed
      ? 'mt-auto flex flex-col gap-2.5 rounded-[10px] border border-line bg-well px-[18px] py-4'
      : 'flex flex-wrap items-center gap-2'}
  >
    <div
      class={boxed
        ? 'font-mono text-xs tracking-[0.08em] text-fg-muted uppercase'
        : 'mr-1 font-mono text-xs text-fg-muted max-sm:sr-only'}
    >
      Rules referenced
    </div>
    <div class="flex flex-wrap gap-2 font-mono text-[13px]">
      {#each ruleIds as id (id)}
        <a href="/rules/{id}" class="rounded-md bg-chip px-2.5 py-1.5 no-underline max-sm:p-3"
          >{id}</a
        >
      {/each}
    </div>
  </div>
{/if}
