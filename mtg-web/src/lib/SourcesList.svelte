<script lang="ts">
  import { isExternalUrl, type Citation, type QueryResult } from '$lib/api';

  export let citations: Citation[] = [];
  export let results: QueryResult[] = [];
  export let idPrefix = 'answer';
  // Open "Also retrieved" by default, e.g. when there is no answer to cite from.
  export let expanded = false;

  $: uncited = results.filter((r) => !r.cited);
</script>

{#if citations.length}
  <h3>Sources</h3>
  <ol class="sources">
    {#each citations as c (c.number)}
      <li value={c.number} id="{idPrefix}-source-{c.number}">
        {#if c.url}
          <a
            href={c.url}
            target={isExternalUrl(c.url) ? '_blank' : undefined}
            rel={isExternalUrl(c.url) ? 'noopener noreferrer' : undefined}>{c.title}</a
          >
        {:else}
          {c.title}
        {/if}
        <p>{c.text}</p>
      </li>
    {/each}
  </ol>
{/if}

{#if uncited.length}
  <details open={expanded}>
    <summary>Also retrieved ({uncited.length})</summary>
    <ul>
      {#each uncited as result}
        <li>
          <strong>{result.title}</strong> ({result.source}, score {result.score})
          <p>{result.text}</p>
        </li>
      {/each}
    </ul>
  </details>
{/if}

<style>
  .sources p,
  details p {
    margin: 0.2rem 0 0.6rem;
    white-space: pre-wrap;
  }
  summary {
    cursor: pointer;
  }
</style>
