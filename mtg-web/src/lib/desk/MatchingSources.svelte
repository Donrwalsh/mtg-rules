<script lang="ts">
  import type { QueryResult } from '$lib/api';
  import { fromResult, groupByKind, type EvidenceItem, type SourceKind } from '$lib/evidence';
  import EvidenceCard from './EvidenceCard.svelte';

  let {
    results,
    phone,
    onopen
  }: {
    results: QueryResult[];
    phone: boolean;
    onopen: (items: EvidenceItem[], index: number) => void;
  } = $props();

  const KINDS: SourceKind[] = ['rule', 'card', 'ruling'];
  const FILTERS: ('all' | SourceKind)[] = ['all', ...KINDS];
  const LABELS: Record<SourceKind, string> = { rule: 'Rules', card: 'Cards', ruling: 'Rulings' };

  const items = $derived(results.map(fromResult));
  const groups = $derived(groupByKind(items));
  let filter = $state<'all' | SourceKind>('all');
  const shown = $derived(filter === 'all' ? items : groups[filter]);
</script>

<div class="flex items-baseline justify-between gap-4">
  <h2 class="m-0 text-base font-semibold">Matching sources · {items.length}</h2>
  <span class="font-mono text-xs text-fg-muted">best match first</span>
</div>

{#if phone}
  <div role="group" aria-label="Filter sources" class="flex flex-wrap gap-2">
    {#each FILTERS as kind (kind)}
      <button
        type="button"
        aria-pressed={filter === kind}
        onclick={() => (filter = kind)}
        class={[
          'min-h-9 cursor-pointer rounded-[18px] border px-3 font-mono text-xs',
          filter === kind
            ? 'border-gold bg-gold text-gold-ink'
            : 'border-line-strong bg-transparent text-fg-soft'
        ]}
        >{kind === 'all' ? 'All' : LABELS[kind]} · {kind === 'all'
          ? items.length
          : groups[kind].length}</button
      >
    {/each}
  </div>
  <div class="flex flex-col gap-2.5">
    {#each shown as item, i (item.key)}
      <EvidenceCard {item} compact onopen={() => onopen(shown, i)} />
    {/each}
  </div>
{:else}
  <div class="grid gap-6 desk:grid-cols-3">
    {#each KINDS as kind (kind)}
      {#if groups[kind].length}
        <section class="flex flex-col gap-3">
          <h3 class="m-0 font-mono text-xs tracking-[0.08em] text-fg-muted uppercase">
            {LABELS[kind]} · {groups[kind].length}
          </h3>
          {#each groups[kind] as item (item.key)}
            <EvidenceCard {item} />
          {/each}
        </section>
      {/if}
    {/each}
  </div>
{/if}
