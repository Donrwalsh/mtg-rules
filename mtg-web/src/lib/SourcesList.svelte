<script lang="ts">
  import { isExternalUrl, type Citation, type QueryResult } from '$lib/api';

  let {
    citations = [],
    results = [],
    idPrefix = 'answer',
    // Open "Also retrieved" by default, e.g. when there is no answer to cite from.
    expanded = false
  }: {
    citations?: Citation[];
    results?: QueryResult[];
    idPrefix?: string;
    expanded?: boolean;
  } = $props();

  const uncited = $derived(results.filter((r) => !r.cited));
</script>

{#if citations.length}
  <h3 class="mt-6 mb-2 font-mono text-xs tracking-widest text-fg-muted uppercase">Sources</h3>
  <ol class="flex list-none flex-col gap-3 p-0">
    {#each citations as c (c.number)}
      <li
        value={c.number}
        id="{idPrefix}-source-{c.number}"
        class="rounded-[10px] border border-line bg-card px-4 py-3"
      >
        <span class="font-mono text-xs text-fg-muted">{c.number} · </span>
        {#if c.url}
          <a
            href={c.url}
            target={isExternalUrl(c.url) ? '_blank' : undefined}
            rel={isExternalUrl(c.url) ? 'noopener noreferrer' : undefined}>{c.title}</a
          >
        {:else}
          {c.title}
        {/if}
        <p class="mt-1 mb-0 text-sm leading-normal whitespace-pre-wrap text-fg-body">{c.text}</p>
      </li>
    {/each}
  </ol>
{/if}

{#if uncited.length}
  <details open={expanded} class="mt-6">
    <summary class="cursor-pointer text-sm text-fg-muted">Also retrieved ({uncited.length})</summary>
    <ul class="mt-3 flex list-none flex-col gap-3 p-0">
      {#each uncited as result}
        <li class="rounded-[10px] border border-line bg-card px-4 py-3">
          <strong>{result.title}</strong>
          <span class="font-mono text-xs text-fg-muted">({result.source}, score {result.score})</span>
          <p class="mt-1 mb-0 text-sm leading-normal whitespace-pre-wrap text-fg-body">
            {result.text}
          </p>
        </li>
      {/each}
    </ul>
  </details>
{/if}
