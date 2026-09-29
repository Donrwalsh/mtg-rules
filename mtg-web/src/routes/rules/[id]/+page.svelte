<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { MediaQuery } from 'svelte/reactivity';
  import { page } from '$app/state';
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchRule, NotFoundError, type RuleDetail, type RuleSummary } from '$lib/api';
  import { parentRule } from '$lib/evidence';
  import { calendarDate } from '$lib/format';
  import { loadMeta, meta } from '$lib/meta.svelte';
  import GoToRule from '$lib/pages/GoToRule.svelte';
  import RuleText from '$lib/pages/RuleText.svelte';
  import {
    CR_SECTIONS,
    entryIdOf,
    isTopLevel,
    neighbours,
    normalizeRuleId,
    sectionOf,
    windowAround
  } from '$lib/rules';

  let entry = $state<RuleDetail | null>(null);
  let parent = $state<RuleDetail | null>(null);
  let notFound = $state(false);
  let suggestion = $state<RuleDetail | null>(null);
  let error = $state('');
  let loading = $state(false);

  const desk = new MediaQuery('min-width: 1100px');
  const phone = new MediaQuery('max-width: 639px');
  const reduceMotion = new MediaQuery('prefers-reduced-motion: reduce');

  const id = $derived(normalizeRuleId(page.params.id ?? ''));

  onMount(loadMeta);

  $effect(() => {
    load(id);
  });

  const quiet = <T,>(p: Promise<T>) => p.catch(() => null);

  async function load(ruleId: string) {
    loading = true;
    entry = parent = suggestion = null;
    notFound = false;
    error = '';
    const entryId = entryIdOf(ruleId);
    const upId = parentRule(entryId);
    try {
      const [fetched, parentDetail] = await Promise.all([
        fetchRule(entryId),
        upId ? quiet(fetchRule(upId)) : null
      ]);
      // Ignore a slow response for a rule the user already navigated away from.
      if (ruleId !== id) return;
      if (ruleId !== entryId && !fetched.subrules.some((r) => r.rule_id === ruleId)) {
        // 702.99z: the entry exists but this subrule doesn't.
        notFound = true;
        suggestion = fetched;
        return;
      }
      entry = fetched;
      parent = parentDetail;
      loading = false;
      await tick();
      document
        .getElementById(`r${ruleId}`)
        ?.scrollIntoView({ block: 'center', behavior: reduceMotion.current ? 'auto' : 'smooth' });
    } catch (e) {
      if (ruleId !== id) return;
      if (e instanceof NotFoundError) {
        notFound = true;
        suggestion = upId ? await quiet(fetchRule(upId)) : null;
      } else {
        error = String(e);
      }
    } finally {
      if (ruleId === id) loading = false;
    }
  }

  // An entry's own text is its title ("Deathtouch") unless it's prose.
  const ownTitle = $derived(entry ? entry.heading === entry.text : false);
  const cards = $derived(entry ? (ownTitle ? entry.subrules : [entry, ...entry.subrules]) : []);
  const siblings = $derived(parent?.subrules ?? []);
  const near = $derived(entry ? neighbours(siblings, entry.rule_id) : { prev: null, next: null });
  const section = $derived(sectionOf(id));
  const rulesDate = $derived(entry?.rules_ingested_at ?? meta.rules_as_of);

  const isTitle = (text: string) => text.length <= 60 && !/[.:)]$/.test(text.trim());
  const label = (r: RuleSummary) => (isTitle(r.text) ? r.text : '');
</script>

<svelte:head>
  <title>Rule {id} — MTG Rules</title>
</svelte:head>

{#snippet neighbourLink(r: RuleSummary, dir: 'Previous' | 'Next')}
  <a
    href="/rules/{r.rule_id}"
    class={[
      'flex min-h-14 min-w-0 flex-col justify-center rounded-[10px] border border-line-strong px-4 no-underline',
      dir === 'Next' && 'col-start-2 items-end text-right'
    ]}
  >
    <span class="text-xs text-fg-muted">{dir}</span>
    <span class="w-full truncate font-mono text-sm">{r.rule_id} {label(r)}</span>
  </a>
{/snippet}

<AppHeader />

{#if loading}
  <main class="mx-auto max-w-[1280px] px-4 py-10 sm:px-10">
    <p class="m-0 text-sm text-fg-muted">Loading rule {id}…</p>
  </main>
{:else if notFound}
  <main class="flex flex-col items-center gap-4 px-4 py-16 text-center sm:py-24">
    <div class="font-mono text-[15px] text-fg-muted">{id}</div>
    <h1 class="m-0 text-[28px] font-semibold">There's no rule {id}</h1>
    <p class="m-0 max-w-[520px] text-base leading-normal text-fg-soft">
      It isn't in the Comprehensive Rules loaded here{#if meta.rules_as_of}
        (as of {calendarDate(meta.rules_as_of)}){/if}.{#if suggestion}
        The closest rule that exists is its parent.{/if}
    </p>
    <div class="mt-2 flex flex-wrap justify-center gap-3">
      {#if suggestion}
        <a
          href="/rules/{suggestion.rule_id}"
          class="flex min-h-11 items-center rounded-lg bg-gold px-5 text-[15px] font-semibold text-gold-ink no-underline hover:text-gold-ink"
          >Go to {suggestion.rule_id}{#if suggestion.heading}&nbsp;· {suggestion.heading}{/if}</a
        >
      {/if}
      <a
        href="/rules"
        class="flex min-h-11 items-center rounded-lg border border-line-strong px-5 text-[15px] font-medium no-underline"
        >Browse all rules</a
      >
    </div>
    <GoToRule class="mt-5 w-full max-w-[280px]" />
  </main>
{:else if error}
  <main class="mx-auto max-w-[1280px] px-4 py-10 sm:px-10">
    <p role="alert" class="m-0 text-danger">{error}</p>
  </main>
{:else if entry}
  <div
    class={[
      'mx-auto grid max-w-[1280px] gap-12 px-4 pt-5 pb-12 sm:px-10 sm:pt-10',
      desk.current && siblings.length ? 'grid-cols-[minmax(0,1fr)_320px]' : 'grid-cols-1'
    ]}
  >
    <main class="flex max-w-[800px] min-w-0 flex-col gap-5">
      {#if phone.current}
        {@const up = entry.ancestors.at(-1)}
        <nav aria-label="Rule hierarchy" class="font-mono text-xs">
          <a href={up ? `/rules/${up.rule_id}` : '/rules'} class="no-underline"
            >‹ {up ? `${up.rule_id} ${up.text}` : 'All rules'}</a
          >
        </nav>
      {:else}
        <nav aria-label="Rule hierarchy">
          <ol class="m-0 flex list-none flex-wrap gap-2 p-0 font-mono text-[13px] text-fg-muted">
            <li><a href="/rules" class="no-underline">Rules</a></li>
            {#if section}
              <li aria-hidden="true">›</li>
              <li><a href="/rules" class="no-underline">{section} {CR_SECTIONS[section]}</a></li>
            {/if}
            {#each entry.ancestors as ancestor (ancestor.rule_id)}
              <li aria-hidden="true">›</li>
              <li>
                <a href="/rules/{ancestor.rule_id}" class="no-underline"
                  >{ancestor.rule_id} {ancestor.text}</a
                >
              </li>
            {/each}
          </ol>
        </nav>
      {/if}

      <h1 class="m-0 text-[26px] font-semibold sm:text-[32px]">
        <span class="font-mono font-medium text-gold">{entry.rule_id}</span>
        {#if ownTitle}{entry.text}{:else if entry.heading}<span class="text-fg-muted"
            >{entry.heading}</span
          >{/if}
      </h1>

      {#if isTopLevel(entry.rule_id)}
        <div class="flex flex-col">
          {#each entry.subrules as r (r.rule_id)}
            <a
              href="/rules/{r.rule_id}"
              class="flex min-h-10 items-center gap-3 border-t border-chip text-sm text-fg-body no-underline hover:text-fg"
              ><span class="w-[52px] shrink-0 font-mono text-[13px] text-gold">{r.rule_id}</span
              ><span class="truncate">{r.text}</span></a
            >
          {/each}
        </div>
      {:else}
        <div class="flex flex-col gap-2.5">
          {#each cards as r (r.rule_id)}
            {@const linked = r.rule_id === id && id !== entry.rule_id}
            <div
              id="r{r.rule_id}"
              aria-current={linked ? 'true' : undefined}
              class={[
                'flex scroll-mt-6 flex-col gap-1.5 rounded-[10px] border px-3.5 py-3 sm:px-[18px] sm:py-3.5',
                linked ? 'border-gold bg-gold-wash' : 'border-line bg-card'
              ]}
            >
              <div class="flex items-center justify-between">
                <a href="#r{r.rule_id}" class="font-mono text-sm no-underline">{r.rule_id}</a>
                {#if linked}
                  <span class="font-mono text-[11px] tracking-[0.06em] text-gold">LINKED RULE</span>
                {/if}
              </div>
              <p class="m-0 text-[15px] leading-[1.6] whitespace-pre-wrap text-fg-body sm:text-base"><RuleText text={r.text} /></p>
            </div>
          {/each}
        </div>
      {/if}

      {#if near.prev || near.next}
        <div class="grid grid-cols-2 gap-3">
          {#if near.prev}{@render neighbourLink(near.prev, 'Previous')}{/if}
          {#if near.next}{@render neighbourLink(near.next, 'Next')}{/if}
        </div>
      {/if}

      {#if rulesDate}
        <p class="m-0 text-[13px] text-fg-muted">
          Comprehensive Rules as of {calendarDate(rulesDate)}.
        </p>
      {/if}
    </main>

    {#if desk.current && parent && siblings.length}
      <aside aria-labelledby="siblings-h" class="flex flex-col gap-2.5">
        <div id="siblings-h" class="font-mono text-xs tracking-[0.08em] text-fg-muted uppercase">
          In {parent.rule_id}
          {parent.text}
        </div>
        <nav
          aria-labelledby="siblings-h"
          class="flex flex-col gap-0.5 rounded-[10px] border border-line bg-panel p-2"
        >
          {#each windowAround(siblings, entry.rule_id, 14) as r (r.rule_id)}
            {@const current = r.rule_id === entry.rule_id}
            <a
              href="/rules/{r.rule_id}"
              aria-current={current ? 'page' : undefined}
              class={[
                'flex min-h-9 items-center gap-2.5 rounded-[7px] px-3 text-sm no-underline',
                current ? 'bg-chip text-fg hover:text-fg' : 'text-fg-body hover:text-fg'
              ]}
              ><span class="w-[52px] shrink-0 font-mono text-[13px] text-gold">{r.rule_id}</span
              ><span class="truncate">{r.text}</span></a
            >
          {/each}
          <a
            href="/rules/{parent.rule_id}"
            class="flex min-h-9 items-center px-3 text-[13px] no-underline"
            >All {siblings.length} entries in {parent.rule_id} →</a
          >
        </nav>
      </aside>
    {/if}
  </div>
{/if}
