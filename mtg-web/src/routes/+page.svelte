<script lang="ts">
  import { onMount } from 'svelte';
  import { MediaQuery } from 'svelte/reactivity';
  import { page } from '$app/state';
  import { admin } from '$lib/admin.svelte';
  import { RateLimitedError, submitQuery, type QueryResponse } from '$lib/api';
  import AppHeader from '$lib/AppHeader.svelte';
  import AnswerBody from '$lib/desk/AnswerBody.svelte';
  import EmptyState from '$lib/desk/EmptyState.svelte';
  import ErrorState from '$lib/desk/ErrorState.svelte';
  import EvidencePanel from '$lib/desk/EvidencePanel.svelte';
  import Icon from '$lib/desk/Icon.svelte';
  import LoadingState from '$lib/desk/LoadingState.svelte';
  import MatchingSources from '$lib/desk/MatchingSources.svelte';
  import RulesReferenced from '$lib/desk/RulesReferenced.svelte';
  import SearchForm from '$lib/desk/SearchForm.svelte';
  import SourceSheet from '$lib/desk/SourceSheet.svelte';
  import StatusChips from '$lib/desk/StatusChips.svelte';
  import StatusNotice from '$lib/desk/StatusNotice.svelte';
  import { fromCitation, type EvidenceItem } from '$lib/evidence';
  import { loadMeta, meta } from '$lib/meta.svelte';
  import type { Selection } from '$lib/selection';
  import { noticeFor } from '$lib/status';

  type View = 'idle' | 'loading' | 'result' | 'failed';

  let query = $state('');
  let view = $state<View>('idle');
  let response = $state<QueryResponse | null>(null);
  let failure = $state('');
  let rateLimited = $state('');
  let selection = $state<Selection | null>(null);
  let sheetItems = $state<EvidenceItem[]>([]);
  let sheetIndex = $state(0);
  let sheetOpen = $state(false);

  const phone = new MediaQuery('max-width: 639px');
  const desk = new MediaQuery('min-width: 1100px');
  const hover = new MediaQuery('hover: hover');

  const notice = $derived(response ? noticeFor(response) : null);
  const citedItems = $derived(response ? response.citations.map(fromCitation) : []);

  onMount(loadMeta);

  // Dev only: ?mock=<fixture> answers from src/lib/fixtures (see the spec).
  const mock = import.meta.env.DEV ? page.url.searchParams.get('mock') : null;

  async function run(q: string, fresh: boolean): Promise<QueryResponse> {
    if (import.meta.env.DEV && mock) return (await import('$lib/fixtures')).mockQuery(mock);
    return submitQuery(q, { fresh });
  }

  async function ask(fresh = false) {
    // A fresh request must regenerate the question whose cached answer is
    // on screen, not whatever is currently sitting in the input box.
    const q = fresh && response ? response.query : query.trim();
    if (!q) return;
    const previous: View = response ? 'result' : 'idle';
    rateLimited = '';
    selection = null;
    sheetOpen = false;
    view = 'loading';
    try {
      response = await run(q, fresh);
      view = 'result';
    } catch (e) {
      if (e instanceof RateLimitedError) {
        rateLimited = e.message;
        view = previous;
      } else {
        failure = e instanceof Error ? e.message : String(e);
        view = 'failed';
      }
    }
  }

  function openSheet(items: EvidenceItem[], index: number) {
    sheetItems = items;
    sheetIndex = Math.max(0, index);
    sheetOpen = true;
  }

  function select(number: number, occurrence: number) {
    if (phone.current) {
      selection = { number, occurrence };
      openSheet(
        citedItems,
        citedItems.findIndex((i) => i.number === number)
      );
      return;
    }
    const same = selection?.number === number && selection.occurrence === occurrence;
    selection = same ? null : { number, occurrence };
  }

  // Paging through cited sources in the sheet moves the highlight with it.
  function onSheetChange(item: EvidenceItem) {
    if (item.number === null) return;
    if (selection?.number !== item.number) selection = { number: item.number, occurrence: null };
  }

  function onWindowKey(event: KeyboardEvent) {
    // Escape that closes a sheet or zoom shouldn't also clear the selection.
    if (event.key === 'Escape' && !sheetOpen && !page.state.zoom) selection = null;
  }
</script>

<svelte:head>
  <title>MTG Rules</title>
</svelte:head>

<svelte:window onkeydown={onWindowKey} />

{#snippet headerForm()}
  <SearchForm
    bind:value={query}
    loading={view === 'loading'}
    retrievalOnly={!!response?.degraded}
    maxChars={meta.max_query_chars}
    error={rateLimited}
    onsubmit={() => ask()}
  />
{/snippet}

<AppHeader center={view === 'idle' ? undefined : headerForm} />

{#if view === 'idle'}
  <EmptyState
    bind:query
    error={rateLimited}
    onsubmit={() => ask()}
    onexample={(q) => {
      query = q;
      ask();
    }}
  />
{:else if view === 'loading'}
  <LoadingState />
{:else if view === 'failed'}
  <ErrorState message={failure} onretry={() => ask()} />
{:else if response && notice}
  <main class="mx-auto flex max-w-[1280px] flex-col gap-6 px-4 py-6 sm:px-8 desk:px-12 desk:py-9">
    <StatusNotice kind={notice} remaining={response.answers_remaining} />
    <MatchingSources results={response.results} phone={phone.current} onopen={openSheet} />
  </main>
{:else if response?.answer}
  <div class="desk:grid desk:grid-cols-[minmax(0,1fr)_460px] desk:items-start">
    <main
      class="flex flex-col gap-5 border-line px-[18px] py-5 max-desk:border-b sm:px-9 sm:py-8 desk:min-h-[calc(100vh-70px)] desk:border-r desk:px-12 desk:py-9"
    >
      <StatusChips
        {response}
        isAdmin={admin.isAdmin}
        loading={false}
        compact={phone.current}
        onfresh={() => ask(true)}
      />
      <AnswerBody
        answer={response.answer}
        citations={response.citations}
        ruleReferences={response.rule_references}
        {selection}
        canHover={hover.current}
        onselect={select}
      />
      {#if response.citation_stats.uncited_answer}
        <div
          class="flex items-start gap-3 rounded-[10px] border border-caution-line bg-caution-bg px-4 py-3.5"
        >
          <Icon name="info" class="mt-0.5 shrink-0 text-caution" />
          <p class="m-0 text-sm leading-normal text-caution-fg">
            This answer didn't point to any sources, so treat it with care. Check it against the
            passages {desk.current ? 'on the right' : 'below'}, which were retrieved for your
            question.
          </p>
        </div>
      {/if}
      <RulesReferenced ruleIds={response.rule_references} boxed={desk.current} />
    </main>
    <div class="desk:sticky desk:top-0 desk:max-h-screen desk:overflow-y-auto">
      <EvidencePanel
        citations={response.citations}
        results={response.results}
        selectedNumber={selection?.number ?? null}
        layout={desk.current ? 'side' : phone.current ? 'list' : 'grid'}
        onopen={openSheet}
      />
    </div>
  </div>
{/if}

<SourceSheet
  items={sheetItems}
  bind:index={sheetIndex}
  bind:open={sheetOpen}
  onchange={onSheetChange}
/>
