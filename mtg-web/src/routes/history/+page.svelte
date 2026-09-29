<script lang="ts">
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchHistory, type QueryHistoryRow } from '$lib/api';
  import CitedAnswer from '$lib/CitedAnswer.svelte';
  import SourcesList from '$lib/SourcesList.svelte';

  const PAGE_SIZE = 20;
  const th =
    'border-b border-line-strong px-2 py-1.5 text-left font-mono text-xs font-normal text-fg-muted';
  const td = 'border-b border-line px-2 py-1.5 align-top';
  const pager =
    'min-h-10 cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-4 text-sm text-fg disabled:cursor-default disabled:text-fg-disabled';

  let rows = $state<QueryHistoryRow[]>([]);
  let offset = $state(0);
  let error = $state('');
  let expandedId = $state<number | null>(null);

  async function load() {
    error = '';
    try {
      rows = await fetchHistory(PAGE_SIZE, offset);
    } catch (e) {
      error = String(e);
    }
  }

  function truncate(text: string | null, length = 120): string {
    if (!text) return '';
    return text.length > length ? text.slice(0, length) + '…' : text;
  }

  function toggle(id: number) {
    expandedId = expandedId === id ? null : id;
  }

  function next() {
    offset += PAGE_SIZE;
    load();
  }

  function prev() {
    offset = Math.max(0, offset - PAGE_SIZE);
    load();
  }

  load();
</script>

<AppHeader />

<main class="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 sm:px-8">
  <h1 class="m-0 text-2xl font-medium">Query History</h1>

  {#if error}
    <p role="alert" class="m-0 text-danger">{error}</p>
  {/if}

  <div class="overflow-x-auto">
    <table class="w-full border-collapse text-sm">
      <thead>
        <tr>
          <th class={th}>ID</th>
          <th class={th}>Query</th>
          <th class={th}>Answer</th>
          <th class={th}>Model</th>
          <th class={th}>Error</th>
          <th class={th}>Created</th>
        </tr>
      </thead>
      <tbody>
        {#each rows as row (row.id)}
          <tr class="cursor-pointer hover:bg-card" onclick={() => toggle(row.id)}>
            <td class="{td} font-mono text-fg-muted">{row.id}</td>
            <td class={td}>
              {row.query}
              {#if row.cached}
                <span class="ml-1 rounded bg-chip px-1.5 font-mono text-[11px] text-fg-muted"
                  >cached</span
                >
              {/if}
            </td>
            <td class="{td} text-fg-body">{truncate(row.answer)}</td>
            <td class="{td} font-mono text-xs text-fg-muted">{row.model}</td>
            <td class="{td} text-danger">{row.error ?? ''}</td>
            <td class="{td} font-mono text-xs whitespace-nowrap text-fg-muted">{row.created_at}</td>
          </tr>
          {#if expandedId === row.id}
            <tr>
              <td colspan="6" class="border-b border-line bg-panel px-4 py-4">
                <h3 class="mt-0 mb-2 text-base font-medium">Full answer</h3>
                {#if row.answer}
                  <!-- Rows saved before citations existed have null citations
                       and render as plain text. -->
                  <CitedAnswer
                    answer={row.answer}
                    citations={row.citations ?? []}
                    ruleReferences={row.rule_references ?? []}
                    idPrefix="h{row.id}"
                  />
                  {#if row.citation_stats?.uncited_answer}
                    <p class="text-sm text-caution">No sources cited</p>
                  {/if}
                {:else}
                  <p class="text-fg-muted">(none)</p>
                {/if}
                <SourcesList
                  citations={row.citations ?? []}
                  results={row.results}
                  idPrefix="h{row.id}"
                />
                <details class="mt-4">
                  <summary class="cursor-pointer text-sm text-fg-muted">Raw results</summary>
                  <pre
                    class="overflow-x-auto rounded-lg bg-well p-3 font-mono text-xs break-words whitespace-pre-wrap">{JSON.stringify(
                      row.results,
                      null,
                      2
                    )}</pre>
                </details>
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  </div>

  <div class="flex gap-3">
    <button type="button" class={pager} onclick={prev} disabled={offset === 0}>Prev</button>
    <button type="button" class={pager} onclick={next} disabled={rows.length < PAGE_SIZE}
      >Next</button
    >
  </div>
</main>
