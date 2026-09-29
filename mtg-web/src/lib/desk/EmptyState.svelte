<script lang="ts">
  import { MediaQuery } from 'svelte/reactivity';
  import { calendarDate } from '$lib/format';
  import { meta } from '$lib/meta.svelte';
  import SearchForm from './SearchForm.svelte';

  let {
    query = $bindable(''),
    error = '',
    onsubmit,
    onexample
  }: { query?: string; error?: string; onsubmit: () => void; onexample: (q: string) => void } =
    $props();

  const EXAMPLES = [
    'Does trample plus deathtouch only need 1 damage on each blocker?',
    'What happens when two replacement effects apply to the same event?',
    "Can I cast an instant during my opponent's cleanup step?"
  ];
  const phone = new MediaQuery('max-width: 639px');
  const examples = $derived(phone.current ? [EXAMPLES[0], EXAMPLES[2]] : EXAMPLES);
</script>

<main class="mx-auto flex w-full max-w-[720px] flex-col gap-7 px-4 pt-12 pb-12 sm:px-8 sm:pt-24">
  <div class="flex flex-col gap-2">
    <h1 class="m-0 text-[28px] leading-tight font-medium sm:text-4xl">Ask a rules question</h1>
    <p class="m-0 text-base text-fg-soft">
      Answers cite the Comprehensive Rules, {phone.current ? '' : 'Scryfall '}card text and official
      rulings.
    </p>
  </div>
  <SearchForm bind:value={query} variant="hero" maxChars={meta.max_query_chars} {error} {onsubmit} />
  <div class="flex flex-col gap-2.5">
    <div class="font-mono text-xs tracking-[0.08em] text-fg-muted uppercase">Try one of these</div>
    {#each examples as example (example)}
      <button
        type="button"
        onclick={() => onexample(example)}
        class="min-h-11 cursor-pointer rounded-[10px] border border-line bg-card px-4 py-3 text-left font-sans text-[15px] text-fg-body hover:border-line-strong hover:text-fg"
        >{example}</button
      >
    {/each}
  </div>
  <div class="flex flex-wrap gap-x-5 gap-y-2 font-mono text-xs text-fg-muted">
    {#if meta.rules_as_of && !phone.current}
      <span>Comprehensive Rules · as of {calendarDate(meta.rules_as_of)}</span>
    {/if}
    {#if !phone.current}<span>Scryfall oracle text &amp; rulings</span>{/if}
    {#if meta.answers_per_day}<span>{meta.answers_per_day} AI answers a day</span>{/if}
  </div>
</main>
