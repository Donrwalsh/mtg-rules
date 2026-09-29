<script lang="ts">
  import { cardSummary, statLine, type EvidenceItem } from '$lib/evidence';
  import { calendarDate } from '$lib/format';
  import Icon from './Icon.svelte';

  let {
    item,
    active = false,
    compact = false,
    onopen
  }: { item: EvidenceItem; active?: boolean; compact?: boolean; onopen?: () => void } = $props();

  const label = $derived(
    `${item.number !== null ? `${item.number} · ` : ''}${item.kind.toUpperCase()}`
  );
  const domId = $derived(item.number !== null ? `source-${item.number}` : `evidence-${item.key}`);
  const name = $derived(item.card?.name ?? item.cardName ?? item.title);
  const external = { target: '_blank', rel: 'noopener noreferrer' };
</script>

{#snippet art(w: number, h: number)}
  {#if item.card?.image_small}
    <img
      src={item.card.image_small}
      alt={name}
      width={w}
      height={h}
      loading="lazy"
      decoding="async"
      class="shrink-0 rounded-md"
      style:width="{w}px"
      style:height="{h}px"
    />
  {:else}
    <span
      aria-hidden="true"
      class="flex shrink-0 items-center justify-center rounded-md border border-line-muted bg-art font-mono text-[10px] text-fg-muted"
      style:width="{w}px"
      style:height="{h}px">art</span
    >
  {/if}
{/snippet}

{#if compact}
  <button
    type="button"
    id={domId}
    onclick={onopen}
    class={[
      'flex w-full cursor-pointer rounded-[10px] border text-left font-sans text-fg',
      item.kind === 'ruling' ? 'border-teal-line bg-teal-wash' : 'border-line bg-card',
      item.kind === 'card' ? 'items-center gap-3 p-2.5' : 'flex-col gap-1 px-3.5 py-3'
    ]}
  >
    {#if item.kind === 'card'}
      {@render art(52, 72)}
      <span class="flex min-w-0 flex-1 flex-col gap-[3px]">
        <span class="font-mono text-xs text-fg-muted">{label}</span>
        <span class="text-[15px] font-semibold">{name}</span>
        <span class="truncate text-[13px] text-fg-muted">{cardSummary(item)}</span>
      </span>
      <Icon name="chevron-right" class="shrink-0 text-fg-muted" />
    {:else if item.kind === 'ruling'}
      <span class="flex justify-between font-mono text-xs text-fg-muted">
        <span class="text-teal">{label}</span>
        {#if item.publishedAt}<span>{calendarDate(item.publishedAt)}</span>{/if}
      </span>
      <span class="text-[13px] text-fg-muted">on {item.cardName}</span>
      <span class="line-clamp-2 text-sm leading-normal text-fg-body italic">“{item.text}”</span>
    {:else}
      <span class="flex justify-between font-mono text-xs text-fg-muted">
        <span>{label}</span><span class="text-gold">{item.ruleId}</span>
      </span>
      <span class="line-clamp-2 text-sm leading-normal text-fg-body">{item.text}</span>
    {/if}
  </button>
{:else if item.kind === 'card'}
  <article
    id={domId}
    class={[
      'flex scroll-mt-4 gap-3.5 rounded-[10px] border p-3.5',
      active ? 'border-gold bg-gold-wash' : 'border-line bg-card'
    ]}
  >
    {@render art(96, 134)}
    <div class="flex min-w-0 flex-col gap-1.5">
      <div class={['font-mono text-xs', active ? 'text-gold' : 'text-fg-muted']}>{label}</div>
      <div class="text-base font-semibold">
        {name}
        {#if item.card?.mana_cost}<span class="font-mono text-xs font-normal text-fg-muted"
            >{item.card.mana_cost}</span
          >{/if}
      </div>
      {#if item.card}<div class="text-[13px] text-fg-muted">{statLine(item.card)}</div>{/if}
      <p class="m-0 text-sm leading-normal whitespace-pre-line text-fg-body">{item.text}</p>
      {#if item.url}<a href={item.url} {...external} class="text-[13px]">Scryfall ↗</a>{/if}
    </div>
  </article>
{:else if item.kind === 'ruling'}
  <article
    id={domId}
    class={[
      'flex scroll-mt-4 flex-col gap-2 rounded-[10px] border bg-teal-wash px-4 py-3.5',
      active ? 'border-teal' : 'border-teal-line'
    ]}
  >
    <div class="flex justify-between font-mono text-xs text-fg-muted">
      <span class="text-teal">{label}</span>
      {#if item.publishedAt}<span>{calendarDate(item.publishedAt)}</span>{/if}
    </div>
    <div class="text-[13px] text-fg-muted">
      Ruling on {item.cardName}{#if item.url}&nbsp;· <a href={item.url} {...external}>Scryfall ↗</a
        >{/if}
    </div>
    <blockquote class="m-0 border-l-2 border-teal pl-3 text-sm leading-[1.55] text-fg-body italic">
      “{item.text}”
    </blockquote>
  </article>
{:else}
  <article
    id={domId}
    class={[
      'flex scroll-mt-4 flex-col gap-1.5 rounded-[10px] border px-4 py-3.5',
      active ? 'border-gold bg-gold-wash' : 'border-line bg-card'
    ]}
  >
    <div class="flex justify-between font-mono text-xs text-fg-muted">
      <span class={active ? 'text-gold' : ''}>{label}</span>
      <a href="/rules/{item.ruleId}">{item.ruleId}</a>
    </div>
    {#if item.heading}<div class="text-sm font-medium text-fg">{item.heading}</div>{/if}
    <p class="m-0 line-clamp-3 text-sm leading-[1.55] text-fg-body">{item.text}</p>
  </article>
{/if}
