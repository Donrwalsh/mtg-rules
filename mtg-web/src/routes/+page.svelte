<script lang="ts">
  import { submitQuery, type QueryResponse } from '$lib/api';
  import CitedAnswer from '$lib/CitedAnswer.svelte';
  import SourcesList from '$lib/SourcesList.svelte';

  let query = '';
  let response: QueryResponse | null = null;
  let error = '';
  let loading = false;

  async function onSubmit() {
    error = '';
    loading = true;
    try {
      response = await submitQuery(query);
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }
</script>

<main>
  <h1>MTG Rules Search (prototype)</h1>
  <p><a href="/history">View query history</a></p>
  <form on:submit|preventDefault={onSubmit}>
    <input type="text" bind:value={query} placeholder="Ask a rules question" />
    <button type="submit" disabled={loading}>{loading ? 'Searching…' : 'Search'}</button>
  </form>

  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  {#if response}
    {#if response.answer}
      <div class="answer">
        <h2>Answer</h2>
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
</style>
