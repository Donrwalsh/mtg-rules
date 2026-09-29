<script lang="ts">
  import type { UsageDay } from '$lib/api';
  import { calendarDate } from '$lib/format';
  import { barHeight, chartScale } from '$lib/usageChart';

  let { days, budget }: { days: UsageDay[]; budget: number } = $props();

  const HEIGHT = 220;
  const dollars = (n: number) => `$${n.toFixed(2)}`;
  // "Sep 23" from "2026-09-23".
  const dayLabel = (date: string) => calendarDate(date).replace(/, \d{4}$/, '');

  const scale = $derived(chartScale(days.map((d) => d.spend_usd), budget));
  const summary = $derived(
    `Daily spend, ${days
      .map((d) => `${dayLabel(d.date)} ${dollars(d.spend_usd)}`)
      .join(', ')}. Budget ${dollars(budget)} a day.`
  );
</script>

<!-- One series, so no legend: the title names it. The table below the
     chart is its table view. -->
<div class="flex flex-col gap-2">
  <div class="flex gap-2">
    <!-- y axis labels -->
    <div class="relative w-12 shrink-0" style:height="{HEIGHT}px" aria-hidden="true">
      {#each scale.ticks as t (t)}
        <span
          class="absolute right-0 font-mono text-[11px] text-fg-muted"
          style:bottom="{barHeight(t, scale.max, HEIGHT) - 7}px">{dollars(t)}</span
        >
      {/each}
    </div>
    <div class="relative min-w-0 flex-1" style:height="{HEIGHT}px" role="img" aria-label={summary}>
      {#each scale.ticks as t (t)}
        <div
          class={[
            'absolute right-0 left-0 border-t',
            t === budget ? 'border-dashed border-fg-muted' : 'border-chip'
          ]}
          style:bottom="{barHeight(t, scale.max, HEIGHT)}px"
        ></div>
      {/each}
      {#if !scale.ticks.includes(budget)}
        <div
          class="absolute right-0 left-0 border-t border-dashed border-fg-muted"
          style:bottom="{barHeight(budget, scale.max, HEIGHT)}px"
        ></div>
      {/if}
      <div class="absolute inset-0 flex items-end gap-[2px]">
        {#each days as day, i (day.date)}
          {@const h = barHeight(day.spend_usd, scale.max, HEIGHT)}
          <div class="relative flex h-full flex-1 items-end justify-center">
            <div
              title="{dayLabel(day.date)}: {dollars(day.spend_usd)}"
              class="w-[60%] max-w-16 rounded-t bg-gold"
              style:height="{h}px"
            ></div>
            {#if i === days.length - 1}
              <span
                class="absolute font-mono text-xs text-fg"
                style:bottom="{h + 6}px">{dollars(day.spend_usd)}</span
              >
            {/if}
          </div>
        {/each}
      </div>
    </div>
  </div>
  <div class="ml-14 flex font-mono text-[11px] text-fg-muted" aria-hidden="true">
    {#each days as day (day.date)}
      <span class="flex-1 text-center">{dayLabel(day.date)}</span>
    {/each}
  </div>
</div>
