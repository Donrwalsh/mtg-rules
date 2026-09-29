<script lang="ts">
  import { tick } from 'svelte';
  import { MediaQuery } from 'svelte/reactivity';
  import type { Citation, QueryResult } from '$lib/api';
  import { fromCitation, uncitedItems, type EvidenceItem } from '$lib/evidence';
  import EvidenceCard from './EvidenceCard.svelte';

  let {
    citations,
    results,
    selectedNumber,
    layout,
    onopen
  }: {
    citations: Citation[];
    results: QueryResult[];
    selectedNumber: number | null;
    layout: 'side' | 'grid' | 'list';
    onopen: (items: EvidenceItem[], index: number) => void;
  } = $props();

  const OTHERS_PREVIEW = 3;
  const reduceMotion = new MediaQuery('prefers-reduced-motion: reduce');

  const cited = $derived(citations.map(fromCitation));
  const others = $derived(uncitedItems(results));
  let chosen = $state<'cited' | 'other'>('cited');
  let showAll = $state(false);
  const tab = $derived(cited.length ? chosen : 'other');
  const list = $derived(
    tab === 'cited' ? cited : showAll ? others : others.slice(0, OTHERS_PREVIEW)
  );
  const hidden = $derived(tab === 'other' && !showAll ? others.length - list.length : 0);

  // Selecting a citation in the answer brings its card into view.
  $effect(() => {
    if (selectedNumber === null || layout === 'list') return;
    chosen = 'cited';
    const id = `source-${selectedNumber}`;
    tick().then(() =>
      document
        .getElementById(id)
        ?.scrollIntoView({ block: 'nearest', behavior: reduceMotion.current ? 'auto' : 'smooth' })
    );
  });

  // ARIA tabs pattern: arrow keys move between the two tabs.
  function onTabKey(event: KeyboardEvent) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    if (!cited.length) return;
    chosen = tab === 'cited' ? 'other' : 'cited';
    tick().then(() => document.getElementById(`tab-${chosen}`)?.focus());
  }

  const tabClass = (on: boolean) => [
    'min-h-11 cursor-pointer border-0 border-b-2 bg-transparent px-3.5 font-sans text-sm disabled:cursor-default disabled:text-fg-disabled max-sm:min-h-12 max-sm:flex-1',
    on ? 'border-gold font-semibold text-fg' : 'border-transparent text-fg-muted'
  ];
</script>

<section aria-label="Evidence" class="flex min-h-0 flex-col bg-panel">
  <div
    role="tablist"
    tabindex="-1"
    aria-label="Evidence"
    onkeydown={onTabKey}
    class="flex gap-1 border-b border-line px-5 pt-3.5 max-desk:px-7 max-desk:pt-2.5 max-sm:px-2 max-sm:pt-1"
  >
    <button
      type="button"
      role="tab"
      id="tab-cited"
      aria-controls="evidence-panel"
      aria-selected={tab === 'cited'}
      tabindex={tab === 'cited' ? 0 : -1}
      disabled={!cited.length}
      onclick={() => (chosen = 'cited')}
      class={tabClass(tab === 'cited')}>Cited · {cited.length}</button
    >
    <button
      type="button"
      role="tab"
      id="tab-other"
      aria-controls="evidence-panel"
      aria-selected={tab === 'other'}
      tabindex={tab === 'other' ? 0 : -1}
      onclick={() => (chosen = 'other')}
      class={tabClass(tab === 'other')}
      >{cited.length ? 'Also retrieved' : 'Retrieved'} · {others.length}</button
    >
  </div>
  <div
    role="tabpanel"
    id="evidence-panel"
    aria-labelledby="tab-{tab}"
    class={layout === 'grid'
      ? 'grid grid-cols-2 gap-3.5 px-7 py-6'
      : 'flex flex-col gap-3 p-5 max-sm:gap-2.5 max-sm:p-4'}
  >
    {#each list as item, i (item.key)}
      <div class={layout === 'grid' && item.kind === 'ruling' ? 'col-span-2' : ''}>
        <EvidenceCard
          {item}
          active={item.number !== null && item.number === selectedNumber}
          compact={layout === 'list'}
          onopen={() => onopen(list, i)}
        />
      </div>
    {/each}
    {#if hidden > 0}
      <button
        type="button"
        onclick={() => (showAll = true)}
        class={[
          'min-h-11 cursor-pointer rounded-[10px] border border-line-strong bg-transparent font-sans text-sm text-fg-soft',
          layout === 'grid' && 'col-span-2'
        ]}>Show {hidden} more</button
      >
    {/if}
  </div>
</section>
