<script lang="ts">
  import { submitQuery, type QueryResult } from '$lib/api';

  let query = '';
  let results: QueryResult[] = [];
  let error = '';

  async function onSubmit() {
    error = '';
    try {
      const resp = await submitQuery(query);
      results = resp.results;
    } catch (e) {
      error = String(e);
    }
  }
</script>

<main>
  <h1>MTG Rules Search (prototype)</h1>
  <form on:submit|preventDefault={onSubmit}>
    <input type="text" bind:value={query} placeholder="Ask a rules question" />
    <button type="submit">Search</button>
  </form>

  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  <ul>
    {#each results as result}
      <li>
        <strong>{result.title}</strong> ({result.source}, score {result.score})
        <p>{result.text}</p>
      </li>
    {/each}
  </ul>
</main>
