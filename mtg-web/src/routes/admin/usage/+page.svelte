<script lang="ts">
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchUsage, type UsageSummary } from '$lib/api';
  import { calendarDate } from '$lib/format';
  import SpendChart from '$lib/pages/SpendChart.svelte';

  const OUTCOMES: [string, string][] = [
    ['generated', 'Answered'],
    ['cached', 'Cached'],
    ['degraded_ip', 'Visitor limit'],
    ['degraded_global', 'Site budget'],
    ['error', 'Error']
  ];
  const th =
    'border-b border-line-strong px-2.5 py-2 text-left font-mono text-[11px] font-normal whitespace-nowrap text-fg-muted';
  const td = 'border-b border-chip px-2.5 py-2 font-mono text-[13px] whitespace-nowrap';
  const card = 'rounded-[10px] border border-line bg-card';

  let usage = $state<UsageSummary | null>(null);
  let error = $state('');

  fetchUsage()
    .then((u) => (usage = u))
    .catch((e) => (error = String(e)));

  const dollars = (n: number) => `$${n.toFixed(2)}`;
  const today = $derived(usage ? usage.days[usage.days.length - 1] : null);
  const count = (key: string) => today?.outcomes[key] ?? 0;
  const spentPct = $derived(
    usage && today ? Math.round((today.spend_usd / usage.budget_usd) * 100) : 0
  );
</script>

<svelte:head>
  <title>Usage — MTG Rules</title>
</svelte:head>

{#snippet tile(label: string, value: string, sub: string, meter: number | null)}
  <div class="{card} flex flex-col gap-2 px-[18px] py-4">
    <span class="text-[13px] text-fg-soft">{label}</span>
    <span class="font-mono text-[26px] font-medium">{value}</span>
    {#if meter !== null}
      <div class="h-1.5 rounded-sm bg-chip" aria-hidden="true">
        <div class="h-1.5 rounded-sm bg-gold" style:width="{Math.min(100, meter)}%"></div>
      </div>
    {/if}
    <span class="text-xs text-fg-muted">{sub}</span>
  </div>
{/snippet}

<AppHeader />

<main class="mx-auto flex max-w-[1280px] flex-col gap-6 px-4 pt-6 pb-12 sm:px-10 sm:pt-10">
  <div class="flex flex-col gap-1.5">
    <h1 class="m-0 text-2xl font-semibold sm:text-[28px]">Usage</h1>
    {#if usage}
      <p class="m-0 text-sm text-fg-soft">
        UTC days · daily budget <span class="font-mono">{dollars(usage.budget_usd)}</span> · limits
        reset at midnight UTC
      </p>
    {/if}
  </div>

  {#if error}
    <p role="alert" class="m-0 text-danger">{error}</p>
  {:else if !usage || !today}
    <p class="m-0 text-sm text-fg-muted">Loading usage…</p>
  {:else}
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 desk:grid-cols-4">
      {@render tile(
        "Today's spend",
        dollars(today.spend_usd),
        `${spentPct}% of the ${dollars(usage.budget_usd)} budget`,
        spentPct
      )}
      {@render tile(
        'AI answers today',
        String(count('generated')),
        `plus ${count('cached')} served from cache`,
        null
      )}
      {@render tile(
        'Cache hit rate',
        usage.cache_hit_rate === null ? 'n/a' : `${Math.round(usage.cache_hit_rate * 100)}%`,
        'today',
        null
      )}
      {@render tile(
        'Limited today',
        String(count('degraded_ip') + count('degraded_global')),
        'answers held back by a visitor limit or the site budget',
        null
      )}
    </div>

    <section aria-labelledby="spend-h" class="{card} flex flex-col gap-3.5 px-4 py-[18px] sm:px-[22px]">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="spend-h" class="m-0 text-base font-semibold">Spend, last 7 days</h2>
        <span class="font-mono text-xs text-fg-muted"
          >dashed line: {dollars(usage.budget_usd)} budget</span
        >
      </div>
      <SpendChart days={usage.days} budget={usage.budget_usd} />
    </section>

    <div class="grid grid-cols-1 items-start gap-4 desk:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <section aria-labelledby="out-h" class="{card} min-w-0 px-[18px] py-4">
        <h2 id="out-h" class="mt-0 mb-2 text-base font-semibold">Outcomes by day</h2>
        <div class="overflow-x-auto">
          <table class="w-full border-collapse">
            <thead>
              <tr>
                <th class={th}>Date</th>
                <th class={th}>Spend</th>
                {#each OUTCOMES as [, label] (label)}<th class={th}>{label}</th>{/each}
              </tr>
            </thead>
            <tbody>
              {#each [...usage.days].reverse() as day (day.date)}
                <tr>
                  <td class={td}>{calendarDate(day.date).replace(/, \d{4}$/, '')}</td>
                  <td class={td}>{dollars(day.spend_usd)}</td>
                  {#each OUTCOMES as [key] (key)}<td class={td}>{day.outcomes[key] ?? 0}</td>{/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="ip-h" class="{card} min-w-0 px-[18px] py-4">
        <h2 id="ip-h" class="mt-0 mb-2 text-base font-semibold">Busiest visitors today</h2>
        {#if usage.top_ip_buckets.length === 0}
          <p class="m-0 text-sm text-fg-muted">No requests yet today.</p>
        {:else}
          <div class="overflow-x-auto">
            <table class="w-full border-collapse">
              <thead>
                <tr>
                  <th class={th}>IP bucket</th>
                  <th class={th}>Requests</th>
                  <th class={th}>Answers</th>
                  <th class={th}>Spend</th>
                </tr>
              </thead>
              <tbody>
                {#each usage.top_ip_buckets as b (b.ip_bucket)}
                  <tr>
                    <td class={td}>{b.ip_bucket}</td>
                    <td class={td}>{b.requests}</td>
                    <td class={td}>{b.answers}</td>
                    <td class={td}>{dollars(b.spend_usd)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </section>
    </div>
  {/if}
</main>
