<script lang="ts">
  import { fetchUsage, type UsageSummary } from '$lib/api';

  const OUTCOMES = ['generated', 'cached', 'degraded_ip', 'degraded_global', 'error'];

  let usage: UsageSummary | null = null;
  let error = '';

  fetchUsage()
    .then((u) => (usage = u))
    .catch((e) => (error = String(e)));

  const dollars = (n: number) => `$${n.toFixed(4)}`;
  $: today = usage ? usage.days[usage.days.length - 1] : null;
</script>

<main>
  <h1>Usage</h1>
  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  {#if usage && today}
    <p>
      Today (UTC): <strong>{dollars(today.spend_usd)}</strong> of {dollars(usage.budget_usd)}
      ({Math.round((today.spend_usd / usage.budget_usd) * 100)}%). Cache hit rate:
      {usage.cache_hit_rate === null ? 'n/a' : `${Math.round(usage.cache_hit_rate * 100)}%`}
    </p>

    <h2>Last 7 days</h2>
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Spend</th>
          {#each OUTCOMES as o}<th>{o}</th>{/each}
        </tr>
      </thead>
      <tbody>
        {#each [...usage.days].reverse() as day}
          <tr>
            <td>{day.date}</td>
            <td>{dollars(day.spend_usd)}</td>
            {#each OUTCOMES as o}<td>{day.outcomes[o] ?? 0}</td>{/each}
          </tr>
        {/each}
      </tbody>
    </table>

    <h2>Top IP buckets today</h2>
    {#if usage.top_ip_buckets.length === 0}
      <p>No requests yet today.</p>
    {:else}
      <table>
        <thead>
          <tr><th>IP bucket</th><th>Requests</th><th>AI answers</th><th>Spend</th></tr>
        </thead>
        <tbody>
          {#each usage.top_ip_buckets as b}
            <tr>
              <td>{b.ip_bucket}</td>
              <td>{b.requests}</td>
              <td>{b.answers}</td>
              <td>{dollars(b.spend_usd)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</main>

<style>
  table {
    border-collapse: collapse;
    font-size: 0.9rem;
  }
  th,
  td {
    border-bottom: 1px solid #ddd;
    padding: 0.25rem 0.6rem;
    text-align: left;
  }
</style>
