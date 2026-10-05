<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { goto } from '$app/navigation';
  import { MediaQuery } from 'svelte/reactivity';
  import { page } from '$app/state';
  import { admin } from '$lib/admin.svelte';
  import {
    AdminRequiredError,
    fetchReplay,
    NotFoundError,
    TOO_MANY_REQUESTS,
    type ReplayResponse
  } from '$lib/api';
  import { createAnswerStream } from '$lib/answer-stream/answerStream.svelte';
  import { httpSource, type AnswerSource } from '$lib/answer-stream/sources';
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
  import { closeSheet, openSheet as pushSheet } from '$lib/overlays';
  import { noticeFor } from '$lib/status';

  let query = $state('');
  let rateLimited = $state('');
  let selection = $state<Selection | null>(null);
  let sheetItems = $state<EvidenceItem[]>([]);
  let sheetIndex = $state(0);
  const sheetOpen = $derived(!!page.state.sheet);
  // Admin: /?replay=<history id> re-renders a saved answer without asking again.
  let replay = $state<ReplayResponse | null>(null);
  let replayFailed = $state<{ id: string; kind: 'admin' | 'missing' | 'other' } | null>(null);

  const phone = new MediaQuery('max-width: 639px');
  const desk = new MediaQuery('min-width: 1100px');
  const hover = new MediaQuery('hover: hover');

  // Dev only: ?mock=<fixture> answers from src/lib/fixtures (see the spec),
  // imported on first use so the fixtures stay out of the production bundle.
  const mock = import.meta.env.DEV ? page.url.searchParams.get('mock') : null;
  const source: AnswerSource = mock
    ? async function* (q, opts) {
        yield* (await import('$lib/fixtures')).fixtureSource(mock)(q, opts);
      }
    : httpSource;
  const answer = createAnswerStream(source);

  const phase = $derived(answer.state.phase);
  const live = $derived(phase === 'streaming');
  // What the page shows: the response, or while streaming, the draft with
  // live citations in place of the validated ones.
  const shown = $derived(answer.shown);
  const failure = $derived.by(() => {
    const s = answer.state;
    return s.phase === 'failed' ? s.message : '';
  });
  const notice = $derived(shown && !live ? noticeFor(shown) : null);
  const citedItems = $derived(shown ? shown.citations.map(fromCitation) : []);

  onMount(loadMeta);

  const replayParam = $derived(page.url.searchParams.get('replay'));

  $effect(() => {
    const id = replayParam;
    untrack(() => {
      if (id) loadReplay(id);
      else if (replay || replayFailed) reset();
    });
  });

  function reset() {
    answer.reset();
    replay = null;
    replayFailed = null;
    selection = null;
    query = '';
  }

  async function loadReplay(id: string) {
    rateLimited = '';
    selection = null;
    replayFailed = null;
    const r = await answer.load(() => fetchReplay(id));
    if (r === 'aborted') return;
    if (r instanceof Error) {
      replay = null;
      replayFailed = {
        id,
        kind:
          r instanceof AdminRequiredError
            ? 'admin'
            : r instanceof NotFoundError
              ? 'missing'
              : 'other'
      };
      return;
    }
    replay = r;
    query = r.query;
  }

  const REPLAY_ERRORS = {
    admin: { title: 'Sign in to replay', hint: 'Replaying a past answer is for admins only.' },
    missing: { title: 'Replay not found', hint: 'That history entry is missing or has no answer.' },
    other: { title: "Replay didn't load", hint: "The server didn't answer." }
  };

  async function ask(fresh = false) {
    // A fresh request must regenerate the question whose cached answer is
    // on screen, not whatever is currently sitting in the input box.
    const q = fresh && shown ? shown.query : query.trim();
    // While an answer loads or streams the server holds this visitor's one
    // generation slot, so asking waits (the answer stream's busy rule).
    if (!q || answer.busy) return;
    if (replayParam) {
      // Asking for real ends the replay.
      replay = null;
      replayFailed = null;
      goto('/', { keepFocus: true, noScroll: true });
    }
    rateLimited = '';
    selection = null;
    if ((await answer.ask(q, fresh)) === 'rate-limited') rateLimited = TOO_MANY_REQUESTS;
  }

  function openSheet(items: EvidenceItem[], index: number) {
    sheetItems = items;
    sheetIndex = Math.max(0, index);
    if (!page.state.sheet) pushSheet();
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
    loading={phase === 'loading'}
    answering={live}
    retrievalOnly={!!shown?.degraded}
    maxChars={meta.max_query_chars}
    error={rateLimited}
    onsubmit={() => ask()}
  />
{/snippet}

<AppHeader center={phase === 'idle' ? undefined : headerForm} />

{#if phase === 'idle'}
  <EmptyState
    bind:query
    error={rateLimited}
    onsubmit={() => ask()}
    onexample={(q) => {
      query = q;
      ask();
    }}
  />
{:else if phase === 'loading'}
  <LoadingState />
{:else if phase === 'failed'}
  {#if replayFailed}
    {@const { id, kind } = replayFailed}
    <ErrorState
      message={failure}
      title={REPLAY_ERRORS[kind].title}
      hint={REPLAY_ERRORS[kind].hint}
      onretry={() => loadReplay(id)}
    />
  {:else}
    <ErrorState message={failure} onretry={() => ask()} />
  {/if}
{:else if shown && notice}
  <main class="mx-auto flex max-w-[1280px] flex-col gap-6 px-4 py-6 sm:px-8 desk:px-12 desk:py-9">
    <StatusNotice kind={notice} remaining={shown.answers_remaining} />
    <MatchingSources results={shown.results} phone={phone.current} onopen={openSheet} />
  </main>
{:else if shown && (live || shown.answer)}
  <div class="desk:grid desk:grid-cols-[minmax(0,1fr)_460px] desk:items-start">
    <main
      class="flex flex-col gap-5 border-line px-[18px] py-5 max-desk:border-b sm:px-9 sm:py-8 desk:min-h-[calc(100vh-70px)] desk:border-r desk:px-12 desk:py-9"
    >
      <StatusChips
        response={shown}
        isAdmin={admin.isAdmin}
        loading={live}
        compact={phone.current}
        {replay}
        onfresh={() => ask(true)}
      />
      {#if shown.answer}
        <AnswerBody
          answer={shown.answer}
          citations={shown.citations}
          ruleReferences={shown.rule_references}
          {selection}
          canHover={hover.current}
          onselect={select}
        />
      {:else}
        <div role="status" class="flex items-center gap-2.5 font-mono text-[13px] text-fg-soft">
          <Icon
            name="spinner"
            size={16}
            class="animate-spin text-gold motion-reduce:animate-none"
          />
          Thinking…
        </div>
      {/if}
      {#if !live && shown.answer_complete === false}
        <div
          class="flex items-start gap-3 rounded-[10px] border border-caution-line bg-caution-bg px-4 py-3.5"
        >
          <Icon name="info" class="mt-0.5 shrink-0 text-caution" />
          <p class="m-0 text-sm leading-normal text-caution-fg">
            This answer was cut off before it finished, so it may be missing something. Check it
            against the passages {desk.current ? 'on the right' : 'below'}.
          </p>
        </div>
      {/if}
      {#if !live && shown.citation_stats.uncited_answer}
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
      <RulesReferenced ruleIds={shown.rule_references} boxed={desk.current} />
    </main>
    <div class="desk:sticky desk:top-0 desk:max-h-screen desk:overflow-y-auto">
      <EvidencePanel
        citations={shown.citations}
        results={shown.results}
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
  open={sheetOpen}
  onclose={closeSheet}
  onchange={onSheetChange}
/>
