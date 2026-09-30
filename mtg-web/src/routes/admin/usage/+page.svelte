<script lang="ts">
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchUsage, type UsageSummary } from '$lib/api';

  const OUTCOMES = ['generated', 'cached', 'degraded_ip', 'degraded_global', 'error'];
  const th =
    'border-b border-line-strong px-2 py-1.5 text-left font-mono text-xs font-normal text-fg-muted';
  const td = 'border-b border-line px-2 py-1.5 align-top';

  let usage = $state<UsageSummary | null>(null);
  let error = $state('');

  fetchUsage()
    .then((u) => (usage = u))
    .catch((e) => (error = String(e)));

  const dollars = (n: number) => `$${n.toFixed(4)}`;
  const today = $derived(usage ? usage.days[usage.days.length - 1] : null);
</script>

<AppHeader />

<main class="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-8 sm:px-8">
  <h1 class="m-0 text-2xl font-medium">Usage</h1>
  {#if error}
    <p role="alert" class="m-0 text-danger">{error}</p>
  {/if}

  {#if usage && today}
    <p class="m-0 rounded-[10px] border border-line bg-card px-4 py-3 text-fg-body">
      Today (UTC): <strong class="font-mono text-fg">{dollars(today.spend_usd)}</strong> of
      <span class="font-mono">{dollars(usage.budget_usd)}</span>
      ({Math.round((today.spend_usd / usage.budget_usd) * 100)}%). Cache hit rate:
      <span class="font-mono"
        >{usage.cache_hit_rate === null ? 'n/a' : `${Math.round(usage.cache_hit_rate * 100)}%`}</span
      >
    </p>

    <h2 class="mt-4 mb-0 text-lg font-medium">Last 7 days</h2>
    <div class="overflow-x-auto">
      <table class="w-full border-collapse text-sm">
        <thead>
          <tr>
            <th class={th}>Date</th>
            <th class={th}>Spend</th>
            {#each OUTCOMES as o (o)}<th class={th}>{o}</th>{/each}
          </tr>
        </thead>
        <tbody>
          {#each [...usage.days].reverse() as day (day.date)}
            <tr>
              <td class="{td} font-mono">{day.date}</td>
              <td class="{td} font-mono">{dollars(day.spend_usd)}</td>
              {#each OUTCOMES as o (o)}<td class="{td} font-mono">{day.outcomes[o] ?? 0}</td>{/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <h2 class="mt-4 mb-0 text-lg font-medium">Top IP buckets today</h2>
    {#if usage.top_ip_buckets.length === 0}
      <p class="m-0 text-sm text-fg-muted">No requests yet today.</p>
    {:else}
      <div class="overflow-x-auto">
        <table class="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th class={th}>IP bucket</th>
              <th class={th}>Requests</th>
              <th class={th}>AI answers</th>
              <th class={th}>Spend</th>
            </tr>
          </thead>
          <tbody>
            {#each usage.top_ip_buckets as b (b.ip_bucket)}
              <tr>
                <td class="{td} font-mono">{b.ip_bucket}</td>
                <td class="{td} font-mono">{b.requests}</td>
                <td class="{td} font-mono">{b.answers}</td>
                <td class="{td} font-mono">{dollars(b.spend_usd)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}
</main>
