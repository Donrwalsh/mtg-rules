<script lang="ts">
  import { fetchHistory, type QueryHistoryRow } from '$lib/api';

  const PAGE_SIZE = 20;

  let rows: QueryHistoryRow[] = [];
  let offset = 0;
  let error = '';
  let expandedId: number | null = null;

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

<main>
  <h1>Query History</h1>
  <p><a href="/">&larr; Back to search</a></p>

  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Query</th>
        <th>Answer</th>
        <th>Model</th>
        <th>Error</th>
        <th>Created</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as row (row.id)}
        <tr on:click={() => toggle(row.id)} style="cursor: pointer">
          <td>{row.id}</td>
          <td>{row.query}</td>
          <td>{truncate(row.answer)}</td>
          <td>{row.model}</td>
          <td>{row.error ?? ''}</td>
          <td>{row.created_at}</td>
        </tr>
        {#if expandedId === row.id}
          <tr>
            <td colspan="6">
              <h3>Full answer</h3>
              <p>{row.answer ?? '(none)'}</p>
              <h3>Retrieved results</h3>
              <pre>{JSON.stringify(row.results, null, 2)}</pre>
            </td>
          </tr>
        {/if}
      {/each}
    </tbody>
  </table>

  <button on:click={prev} disabled={offset === 0}>Prev</button>
  <button on:click={next} disabled={rows.length < PAGE_SIZE}>Next</button>
</main>

<style>
  table {
    border-collapse: collapse;
    width: 100%;
  }
  th,
  td {
    border: 1px solid #ccc;
    padding: 0.4rem;
    text-align: left;
    vertical-align: top;
  }
  pre {
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
