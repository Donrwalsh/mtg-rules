<script lang="ts">
  import {
    MAX_QUERY_CHARS,
    RateLimitedError,
    submitQuery,
    type QueryResponse
  } from '$lib/api';
  import { isAdmin } from '$lib/admin';
  import CitedAnswer from '$lib/CitedAnswer.svelte';
  import SourcesList from '$lib/SourcesList.svelte';

  let query = '';
  let response: QueryResponse | null = null;
  let error = '';
  let loading = false;

  async function ask(fresh = false) {
    error = '';
    loading = true;
    try {
      response = await submitQuery(query, { fresh });
    } catch (e) {
      error = e instanceof RateLimitedError ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  // Quotas and the budget reset at UTC midnight; show it in local time.
  function resetTime(): string {
    const now = new Date();
    const next = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)
    );
    return next.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
</script>

<main>
  <h1>MTG Rules Search (prototype)</h1>
  <form on:submit|preventDefault={() => ask()}>
    <input
      type="text"
      bind:value={query}
      maxlength={MAX_QUERY_CHARS}
      placeholder="Ask a rules question"
    />
    <button type="submit" disabled={loading}>{loading ? 'Searching…' : 'Search'}</button>
  </form>
  {#if query.length > MAX_QUERY_CHARS - 100}
    <p class="note">{query.length}/{MAX_QUERY_CHARS} characters</p>
  {/if}

  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  {#if response}
    {#if response.degraded === 'global_budget'}
      <p class="notice">
        AI answers are paused for today. They resume at {resetTime()}. Here are the matching
        rules, rulings and cards.
      </p>
    {:else if response.degraded === 'ip_quota'}
      <p class="notice">
        {#if response.answers_remaining}
          You're asking quickly, so AI answers pause for a few minutes. Here are the matching
          rules, rulings and cards.
        {:else}
          You've used today's AI answers. They reset at {resetTime()}. Here are the matching
          rules, rulings and cards.
        {/if}
      </p>
    {/if}

    {#if response.answer}
      <div class="answer">
        <h2>Answer</h2>
        {#if response.cached_at}
          <p class="badge">
            Cached answer · first generated {formatDate(response.cached_at)}
            {#if $isAdmin}
              <button type="button" on:click={() => ask(true)} disabled={loading}>
                Get a fresh answer
              </button>
            {/if}
          </p>
        {/if}
        <CitedAnswer
          answer={response.answer}
          citations={response.citations}
          ruleReferences={response.rule_references}
        />
        {#if response.citation_stats.uncited_answer}
          <p class="note">No sources cited</p>
        {/if}
      </div>
    {/if}

    {#if response.answers_remaining !== null && !response.degraded}
      <p class="note">
        {response.answers_remaining} AI answer{response.answers_remaining === 1 ? '' : 's'} left
        today
      </p>
    {/if}

    <SourcesList
      citations={response.citations}
      results={response.results}
      expanded={!response.answer}
    />
  {/if}
</main>

<style>
  .note {
    color: #666;
    font-size: 0.9rem;
    font-style: italic;
  }
  .badge {
    display: inline-block;
    background: #eef3fb;
    border: 1px solid #c9d8f0;
    border-radius: 4px;
    padding: 0.2rem 0.5rem;
    font-size: 0.85rem;
  }
  .notice {
    background: #fff8e1;
    border: 1px solid #f0d98c;
    border-radius: 4px;
    padding: 0.5rem 0.75rem;
  }
</style>
