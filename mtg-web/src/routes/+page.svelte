<script lang="ts">
  import { MAX_QUERY_CHARS, RateLimitedError, submitQuery, type QueryResponse } from '$lib/api';
  import { admin } from '$lib/admin.svelte';
  import AppHeader from '$lib/AppHeader.svelte';
  import CitedAnswer from '$lib/CitedAnswer.svelte';
  import SourcesList from '$lib/SourcesList.svelte';

  let query = $state('');
  let response = $state<QueryResponse | null>(null);
  let error = $state('');
  let loading = $state(false);

  async function ask(fresh = false) {
    // A fresh request must regenerate the question whose cached answer is
    // on screen, not whatever is currently sitting in the input box.
    const q = fresh && response ? response.query : query;
    error = '';
    loading = true;
    try {
      response = await submitQuery(q, { fresh });
    } catch (e) {
      error = e instanceof RateLimitedError ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function onSubmit(event: SubmitEvent) {
    event.preventDefault();
    ask();
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  // Quotas and the budget reset at UTC midnight; show it in local time.
  function resetTime(): string {
    const now = new Date();
    const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
    return next.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
</script>

<AppHeader />

<main class="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-8 sm:px-8">
  <h1 class="m-0 text-2xl font-medium">Ask a rules question</h1>
  <form
    class="flex items-center gap-3 rounded-[10px] border border-line-strong bg-field py-1.5 pr-1.5 pl-4"
    onsubmit={onSubmit}
  >
    <label for="q" class="font-mono text-[13px] text-fg-muted">Q</label>
    <input
      id="q"
      type="text"
      class="min-h-8 min-w-0 flex-1 border-0 bg-transparent text-base text-fg outline-none"
      bind:value={query}
      maxlength={MAX_QUERY_CHARS}
      placeholder="Ask a rules question"
    />
    <button
      type="submit"
      class="min-h-10 cursor-pointer rounded-[7px] border-0 bg-gold px-[18px] text-sm font-semibold text-gold-ink disabled:cursor-default disabled:bg-gold-off disabled:text-gold-off-fg"
      disabled={loading}>{loading ? 'Searching…' : 'Search'}</button
    >
  </form>
  {#if query.length > MAX_QUERY_CHARS - 100}
    <p class="m-0 text-right font-mono text-xs text-fg-muted">{query.length} / {MAX_QUERY_CHARS}</p>
  {/if}

  {#if error}
    <p role="alert" class="m-0 text-sm text-danger">{error}</p>
  {/if}

  {#if response}
    {#if response.degraded}
      <p
        role="status"
        class="m-0 rounded-[10px] border border-notice-line bg-notice px-4 py-3 text-sm text-notice-fg"
      >
        {#if response.degraded === 'global_budget'}
          AI answers are paused for today. They resume at {resetTime()}. Here are the matching
          rules, rulings and cards.
        {:else if response.answers_remaining}
          You're asking quickly, so AI answers pause for a few minutes. Here are the matching
          rules, rulings and cards.
        {:else}
          You've used today's AI answers. They reset at {resetTime()}. Here are the matching
          rules, rulings and cards.
        {/if}
      </p>
    {/if}

    {#if response.answer}
      <section class="flex flex-col gap-2">
        {#if response.cached_at}
          <p class="m-0 flex items-center gap-3 font-mono text-xs text-fg-muted">
            <span class="rounded bg-chip px-2 py-0.5"
              >cached · first generated {formatDate(response.cached_at)}</span
            >
            {#if admin.isAdmin}
              <button
                type="button"
                class="min-h-9 cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-3 font-sans text-sm text-gold"
                onclick={() => ask(true)}
                disabled={loading}>Get a fresh answer</button
              >
            {/if}
          </p>
        {/if}
        <CitedAnswer
          answer={response.answer}
          citations={response.citations}
          ruleReferences={response.rule_references}
        />
        {#if response.citation_stats.uncited_answer}
          <p class="m-0 text-sm text-caution">No sources cited</p>
        {/if}
      </section>
    {/if}

    {#if response.answers_remaining !== null && !response.degraded}
      <p class="m-0 font-mono text-xs text-fg-muted">
        {response.answers_remaining} AI answer{response.answers_remaining === 1 ? '' : 's'} left today
      </p>
    {/if}

    <SourcesList
      citations={response.citations}
      results={response.results}
      expanded={!response.answer}
    />
  {/if}
</main>
