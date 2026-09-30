<script lang="ts">
  import Icon from './Icon.svelte';

  const bar = 'h-3.5 rounded bg-skeleton';
</script>

<div aria-busy="true" class="desk:grid desk:grid-cols-[minmax(0,1fr)_460px]">
  <main
    class="flex flex-col gap-5 px-[18px] py-5 sm:px-9 sm:py-8 desk:border-r desk:border-line desk:px-12 desk:py-9"
  >
    <div role="status" class="flex items-center gap-2.5 font-mono text-[13px] text-fg-soft">
      <Icon name="spinner" size={16} class="animate-spin text-gold motion-reduce:animate-none" />
      Searching rules, cards and rulings, then writing an answer…
    </div>
    <div class="h-7 w-3/5 rounded bg-skeleton"></div>
    <div class="flex flex-col gap-3">
      <div class="{bar} w-full"></div>
      <div class="{bar} w-[96%]"></div>
      <div class="{bar} w-[92%]"></div>
      <div class="{bar} w-2/3"></div>
    </div>
    <div class="flex flex-col gap-3">
      <div class="{bar} w-[94%]"></div>
      <div class="{bar} w-1/2"></div>
    </div>
    <p class="m-0 text-sm text-fg-muted">This usually takes a few seconds.</p>
  </main>
  <aside aria-label="Evidence" class="flex flex-col gap-3 bg-panel p-5">
    <div class="flex gap-5 border-b border-line pb-3 text-sm text-fg-disabled">
      <span>Cited</span><span>Also retrieved</span>
    </div>
    {#each [0, 1, 2] as i (i)}
      <div class="h-24 rounded-[10px] border border-line bg-card"></div>
    {/each}
  </aside>
</div>
