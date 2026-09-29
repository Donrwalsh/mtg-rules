<script lang="ts">
  import { onMount } from 'svelte';
  import { MediaQuery } from 'svelte/reactivity';
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchRulesIndex, type RulesContents, type RulesSection } from '$lib/api';
  import { calendarDate } from '$lib/format';
  import GoToRule from '$lib/pages/GoToRule.svelte';

  let contents = $state<RulesContents | null>(null);
  let error = $state('');
  // Phone accordions: the first section starts open.
  let open = $state<Record<number, boolean>>({ 1: true });

  const phone = new MediaQuery('max-width: 639px');
  const desk = new MediaQuery('min-width: 1100px');

  onMount(() => {
    fetchRulesIndex()
      .then((c) => (contents = c))
      .catch((e) => (error = String(e)));
  });

  const total = $derived(contents?.sections.reduce((n, s) => n + s.rules.length, 0) ?? 0);

  // Sections stay in reading order down each column.
  const columns = $derived.by(() => {
    const s = contents?.sections ?? [];
    const by = (nums: number[]) => s.filter((x) => nums.includes(x.number));
    return desk.current
      ? [by([1, 2, 3]), by([4, 5, 6]), by([7, 8, 9])]
      : [by([1, 2, 3, 4]), by([5, 6, 7, 8, 9])];
  });
</script>

<svelte:head>
  <title>Comprehensive Rules — MTG Rules</title>
</svelte:head>

{#snippet ruleLinks(section: RulesSection, compact: boolean)}
  {#each section.rules as rule (rule.rule_id)}
    <a
      href="/rules/{rule.rule_id}"
      class={[
        'flex gap-3 text-fg-body no-underline hover:text-fg',
        compact
          ? 'min-h-10 items-center border-t border-chip px-3.5 text-sm'
          : 'py-[3px] text-sm leading-[1.45]'
      ]}
      ><span class="w-[30px] shrink-0 font-mono text-[13px] text-gold">{rule.rule_id}</span
      >{rule.text}</a
    >
  {/each}
{/snippet}

<AppHeader />

<main class="mx-auto flex max-w-[1280px] flex-col gap-9 px-4 pt-6 pb-12 sm:px-10 sm:pt-11">
  <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
    <div class="flex flex-col gap-2">
      <h1 class="m-0 text-2xl font-semibold sm:text-[32px]">Comprehensive Rules</h1>
      {#if contents}
        <p class="m-0 text-[15px] text-fg-soft">
          {total} rules in {contents.sections.length} sections{#if contents.rules_as_of}
            · <span class="font-mono text-[13px]">as of {calendarDate(contents.rules_as_of)}</span
            >{/if}
        </p>
      {/if}
    </div>
    <GoToRule class="w-full sm:w-[280px]" />
  </div>

  {#if error}
    <p role="alert" class="m-0 text-danger">Couldn't load the rules: {error}</p>
  {:else if !contents}
    <p class="m-0 text-sm text-fg-muted">Loading the rules…</p>
  {:else if phone.current}
    <div class="flex flex-col gap-2">
      {#each contents.sections as section (section.number)}
        <div class="rounded-[10px] border border-line bg-card">
          <button
            type="button"
            aria-expanded={!!open[section.number]}
            aria-controls="sec-{section.number}"
            onclick={() => (open[section.number] = !open[section.number])}
            class="flex min-h-[52px] w-full cursor-pointer items-center gap-2.5 border-0 bg-transparent px-3.5 text-left font-sans text-[15px] font-medium text-fg"
          >
            <span class="w-3.5 font-mono text-[13px] text-fg-muted">{section.number}</span>
            <span class="flex-1">{section.title}</span>
            <span class="font-mono text-xs text-fg-muted">{section.rules.length}</span>
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
              class={['text-fg-muted transition-transform', open[section.number] && 'rotate-180']}
              ><path d="M6 9l6 6 6-6" /></svg
            >
          </button>
          {#if open[section.number]}
            <div id="sec-{section.number}" class="flex flex-col pb-1.5">
              {@render ruleLinks(section, true)}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {:else}
    <div class={['grid items-start gap-x-10', desk.current ? 'grid-cols-3' : 'grid-cols-2']}>
      {#each columns as column, i (i)}
        <div class="flex flex-col gap-8">
          {#each column as section (section.number)}
            <section aria-labelledby="s{section.number}" class="flex flex-col gap-2">
              <h2
                id="s{section.number}"
                class="m-0 flex items-baseline justify-between border-b border-line pb-2 font-mono text-xs font-medium tracking-[0.08em] text-fg-muted uppercase"
              >
                <span>{section.number} · {section.title}</span><span>{section.rules.length}</span>
              </h2>
              <div class="flex flex-col">{@render ruleLinks(section, false)}</div>
            </section>
          {/each}
        </div>
      {/each}
    </div>
  {/if}
</main>
