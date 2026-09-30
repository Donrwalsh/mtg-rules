<script lang="ts">
  import { MediaQuery } from 'svelte/reactivity';
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchHistory, type QueryHistoryRow } from '$lib/api';
  import AnswerBody from '$lib/desk/AnswerBody.svelte';
  import EvidenceCard from '$lib/desk/EvidenceCard.svelte';
  import { fromCitation, uncitedItems } from '$lib/evidence';
  import { historyTime } from '$lib/format';
  import type { Selection } from '$lib/selection';

  const PAGE_SIZE = 20;
  const pager =
    'min-h-10 cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-4 font-sans text-sm text-fg disabled:cursor-default disabled:border-line disabled:text-fg-disabled';

  let rows = $state<QueryHistoryRow[]>([]);
  let offset = $state(0);
  let error = $state('');
  let loaded = $state(false);
  let expandedId = $state<number | null>(null);
  let selection = $state<Selection | null>(null);

  const hover = new MediaQuery('hover: hover');

  async function load() {
    error = '';
    try {
      rows = await fetchHistory(PAGE_SIZE, offset);
    } catch (e) {
      error = String(e);
    } finally {
      loaded = true;
    }
  }

  function toggle(id: number) {
    expandedId = expandedId === id ? null : id;
    selection = null;
  }

  function page(delta: number) {
    offset = Math.max(0, offset + delta * PAGE_SIZE);
    expandedId = null;
    load();
  }

  function selectIn(number: number, occurrence: number) {
    const same = selection?.number === number && selection.occurrence === occurrence;
    selection = same ? null : { number, occurrence };
  }

  const preview = (row: QueryHistoryRow) => row.answer?.split('\n')[0] ?? '(no answer)';

  load();
</script>

<svelte:head>
  <title>Query history — MTG Rules</title>
</svelte:head>

{#snippet chip(text: string, tone: 'neutral' | 'teal' | 'caution' | 'danger')}
  <span
    class={[
      'rounded px-[7px] py-0.5 whitespace-nowrap',
      tone === 'neutral' && 'bg-chip text-fg-muted',
      tone === 'teal' && 'bg-teal-chip text-teal',
      tone === 'caution' && 'bg-caution-chip text-caution',
      tone === 'danger' && 'bg-chip text-danger'
    ]}>{text}</span
  >
{/snippet}

<AppHeader />

<main class="mx-auto flex max-w-[1280px] flex-col gap-5 px-4 pt-6 pb-12 sm:px-10 sm:pt-10">
  <div class="flex items-end justify-between gap-4">
    <div class="flex flex-col gap-1.5">
      <h1 class="m-0 text-2xl font-semibold sm:text-[28px]">Query history</h1>
      <p class="m-0 text-sm text-fg-soft">Every question asked, newest first. 20 per page.</p>
    </div>
    <span class="font-mono text-xs text-fg-muted">Page {offset / PAGE_SIZE + 1}</span>
  </div>

  {#if error}
    <p role="alert" class="m-0 text-danger">{error}</p>
  {:else if loaded && rows.length === 0}
    <p class="m-0 text-sm text-fg-muted">No questions yet.</p>
  {/if}

  <ol class="m-0 flex list-none flex-col gap-2 p-0">
    {#each rows as row (row.id)}
      {@const open = expandedId === row.id}
      <li
        class={[
          'rounded-[10px] border',
          open ? 'border-line-strong bg-panel' : 'border-line bg-card'
        ]}
      >
        <button
          type="button"
          aria-expanded={open}
          aria-controls="h{row.id}"
          onclick={() => toggle(row.id)}
          class="grid w-full cursor-pointer grid-cols-1 items-start gap-x-5 gap-y-1.5 border-0 bg-transparent px-4 py-3.5 text-left font-sans text-fg sm:grid-cols-[120px_minmax(0,1fr)_auto] sm:px-[18px]"
        >
          <span class="pt-0.5 font-mono text-xs text-fg-muted">{historyTime(row.created_at)}</span>
          <span class="flex min-w-0 flex-col gap-1">
            <span class="text-[15px] font-medium">{row.query}</span>
            <span class="truncate text-sm text-fg-muted">{preview(row)}</span>
          </span>
          <span class="flex flex-wrap gap-1.5 font-mono text-[11px]">
            {#if row.error && row.answer}{@render chip('cut off', 'caution')}{:else if row.error}{@render chip('error', 'danger')}{/if}
            {#if row.citation_stats?.uncited_answer}{@render chip('No sources', 'caution')}{/if}
            {#if row.cached}{@render chip('cached', 'neutral')}{/if}
            {#if row.citations?.length}{@render chip(`${row.citations.length} cited`, 'teal')}{/if}
          </span>
        </button>

        {#if open}
          <div
            id="h{row.id}"
            class="flex flex-col gap-3.5 border-t border-line px-4 pt-[18px] pb-5 sm:pr-[18px] sm:pl-[158px]"
          >
            {#if row.answer}
              <AnswerBody
                answer={row.answer}
                citations={row.citations ?? []}
                ruleReferences={row.rule_references ?? []}
                {selection}
                canHover={hover.current}
                onselect={selectIn}
              />
            {:else}
              <p class="m-0 text-sm text-fg-muted">
                No answer was written{row.error ? `: ${row.error}` : '.'}
              </p>
            {/if}
            {#if row.citations?.length}
              <div class="grid grid-cols-1 gap-2.5 sm:grid-cols-2 desk:grid-cols-3">
                {#each row.citations.map(fromCitation) as item (item.key)}
                  <EvidenceCard {item} active={selection?.number === item.number} />
                {/each}
              </div>
            {/if}
            <div class="flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px] text-fg-muted">
              <span>Also retrieved · {uncitedItems(row.results).length}</span>
              <span>Model <span class="font-mono">{row.model}</span></span>
              {#if row.answer}
                <a href="/?replay={row.id}" class="text-gold">Open on desk</a>
              {/if}
              <details class="w-full">
                <summary class="cursor-pointer text-gold">Raw results</summary>
                <pre
                  class="mt-2 max-h-96 overflow-auto rounded-lg bg-well p-3 font-mono text-xs break-words whitespace-pre-wrap text-fg-body">{JSON.stringify(
                    row.results,
                    null,
                    2
                  )}</pre>
              </details>
            </div>
          </div>
        {/if}
      </li>
    {/each}
  </ol>

  <div class="flex justify-between">
    <button type="button" class={pager} onclick={() => page(-1)} disabled={offset === 0}
      >← Newer</button
    >
    <button type="button" class={pager} onclick={() => page(1)} disabled={rows.length < PAGE_SIZE}
      >Older →</button
    >
  </div>
</main>
