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
    RateLimitedError,
    streamQuery,
    type Citation,
    type QueryResponse,
    type ReplayResponse,
    type StreamHandlers
  } from '$lib/api';
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
  import { liveCitations, liveResults, visibleDraft } from '$lib/stream';

  type View = 'idle' | 'loading' | 'streaming' | 'result' | 'failed';

  let query = $state('');
  let view = $state<View>('idle');
  let response = $state<QueryResponse | null>(null);
  let failure = $state('');
  let rateLimited = $state('');
  let selection = $state<Selection | null>(null);
  let sheetItems = $state<EvidenceItem[]>([]);
  let sheetIndex = $state(0);
  const sheetOpen = $derived(!!page.state.sheet);
  // Admin: /?replay=<history id> re-renders a saved answer without asking again.
  let replay = $state<ReplayResponse | null>(null);
  let replayFailed = $state<{ id: string; kind: 'admin' | 'missing' | 'other' } | null>(null);
  let replayLoads = 0;
  // While an answer streams: the sources its markers may point to, the raw
  // text so far, and a copy of it refreshed at most once per frame.
  let sources = $state<Citation[]>([]);
  let draft = '';
  let shownDraft = $state('');
  let frame = 0;
  let controller: AbortController | null = null;

  const phone = new MediaQuery('max-width: 639px');
  const desk = new MediaQuery('min-width: 1100px');
  const hover = new MediaQuery('hover: hover');

  const live = $derived(view === 'streaming');
  // What the page shows: the response, or while streaming, the draft with
  // live citations in place of the validated ones.
  const shown = $derived.by((): QueryResponse | null => {
    if (!response || !live) return response;
    const citations = liveCitations(shownDraft, sources);
    return {
      ...response,
      answer: visibleDraft(shownDraft),
      citations,
      results: liveResults(response.results, citations)
    };
  });
  const notice = $derived(shown && !live ? noticeFor(shown) : null);
  const citedItems = $derived(shown ? shown.citations.map(fromCitation) : []);

  onMount(loadMeta);

  // Dev only: ?mock=<fixture> answers from src/lib/fixtures (see the spec).
  const mock = import.meta.env.DEV ? page.url.searchParams.get('mock') : null;

  function showDraftNextFrame() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      shownDraft = draft;
    });
  }

  function stopDraftFrames() {
    cancelAnimationFrame(frame);
    frame = 0;
  }

  async function run(
    q: string,
    fresh: boolean,
    signal: AbortSignal,
    progress: { results: boolean }
  ): Promise<void> {
    const on: StreamHandlers = {
      results: ({ sources: s, ...head }) => {
        progress.results = true;
        response = {
          query: q,
          ...head,
          answer: null,
          citations: [],
          rule_references: [],
          citation_stats: { cited_count: 0, invalid_count: 0, uncited_answer: false },
          answer_complete: null
        };
        sources = s;
        draft = '';
        shownDraft = '';
        view = 'streaming';
      },
      thinking: () => {},
      delta: (text) => {
        draft += text;
        showDraftNextFrame();
      },
      // `done` follows with no answer, which shows the "couldn't write" notice.
      error: () => {},
      done: (d) => {
        stopDraftFrames();
        if (response) response = { ...response, ...d };
        view = 'result';
      }
    };
    if (import.meta.env.DEV && mock) {
      return (await import('$lib/fixtures')).mockStream(mock, on, signal);
    }
    return streamQuery(q, { fresh, signal }, on);
  }

  const replayParam = $derived(page.url.searchParams.get('replay'));

  $effect(() => {
    const id = replayParam;
    untrack(() => {
      if (id) loadReplay(id);
      else if (replay || replayFailed) reset();
    });
  });

  function reset() {
    replay = null;
    replayFailed = null;
    response = null;
    selection = null;
    query = '';
    view = 'idle';
  }

  async function loadReplay(id: string) {
    controller?.abort();
    stopDraftFrames();
    const load = ++replayLoads;
    rateLimited = '';
    selection = null;
    replayFailed = null;
    view = 'loading';
    try {
      const r = await fetchReplay(id);
      if (load !== replayLoads) return;
      replay = r;
      response = r;
      query = r.query;
      view = 'result';
    } catch (e) {
      if (load !== replayLoads) return;
      replay = null;
      replayFailed = {
        id,
        kind:
          e instanceof AdminRequiredError
            ? 'admin'
            : e instanceof NotFoundError
              ? 'missing'
              : 'other'
      };
      failure = e instanceof Error ? e.message : String(e);
      view = 'failed';
    }
  }

  const REPLAY_ERRORS = {
    admin: { title: 'Sign in to replay', hint: 'Replaying a past answer is for admins only.' },
    missing: { title: 'Replay not found', hint: 'That history entry is missing or has no answer.' },
    other: { title: "Replay didn't load", hint: "The server didn't answer." }
  };

  async function ask(fresh = false) {
    // A fresh request must regenerate the question whose cached answer is
    // on screen, not whatever is currently sitting in the input box.
    const q = fresh && response ? response.query : query.trim();
    // The server keeps writing an abandoned answer and holds this visitor's
    // one generation slot until it's done, so asking now would get a 429.
    if (!q || live) return;
    if (replayParam) {
      // Asking for real ends the replay.
      replay = null;
      replayFailed = null;
      replayLoads++;
      goto('/', { keepFocus: true, noScroll: true });
    }
    // End anything still in flight (a question still loading, or a replay).
    controller?.abort();
    stopDraftFrames();
    const ctrl = (controller = new AbortController());
    const progress = { results: false };
    const previous: View = response ? 'result' : 'idle';
    rateLimited = '';
    selection = null;
    view = 'loading';
    try {
      await run(q, fresh, ctrl.signal, progress);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      if (e instanceof RateLimitedError) {
        rateLimited = e.message;
        view = previous;
      } else if (progress.results && response) {
        // The stream broke after the evidence arrived: keep what was written.
        stopDraftFrames();
        const answer = visibleDraft(draft).trimEnd() || null;
        const citations = answer ? liveCitations(answer, sources) : [];
        response = {
          ...response,
          answer,
          citations,
          results: liveResults(response.results, citations),
          answer_complete: answer ? false : null
        };
        view = 'result';
      } else {
        failure = e instanceof Error ? e.message : String(e);
        view = 'failed';
      }
    }
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
    loading={view === 'loading'}
    answering={live}
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
