<script lang="ts">
  import { page } from '$app/state';
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchRule, NotFoundError, type RuleDetail } from '$lib/api';

  let rule = $state<RuleDetail | null>(null);
  let notFound = $state(false);
  let error = $state('');
  let loading = $state(false);

  const id = $derived(page.params.id ?? '');

  $effect(() => {
    load(id);
  });

  async function load(ruleId: string) {
    loading = true;
    rule = null;
    notFound = false;
    error = '';
    try {
      const fetched = await fetchRule(ruleId);
      // Ignore a slow response for a rule the user already navigated away from.
      if (ruleId === id) rule = fetched;
    } catch (e) {
      if (ruleId !== id) return;
      if (e instanceof NotFoundError) notFound = true;
      else error = String(e);
    } finally {
      if (ruleId === id) loading = false;
    }
  }

  // Best guess at an unknown rule's parent: 702.99z -> 702.99, 702.99 -> 702.
  function parentGuess(ruleId: string): string | null {
    const sub = ruleId.match(/^(\d{3}\.\d+)[a-z]$/);
    if (sub) return sub[1];
    const top = ruleId.match(/^(\d{3})\.\d+$/);
    return top ? top[1] : null;
  }

  const parent = $derived(parentGuess(id));
</script>

<svelte:head>
  <title>Rule {id} — MTG Rules</title>
</svelte:head>

<AppHeader />

<main class="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-8 sm:px-8">
  <p class="m-0 text-sm"><a href="/">&larr; Back to search</a></p>

  {#if loading}
    <p class="m-0 text-sm text-fg-muted">Loading rule {id}…</p>
  {:else if notFound}
    <h1 class="m-0 font-mono text-2xl font-medium">No rule {id}</h1>
    <p class="m-0 text-fg-body">There's no rule {id} in the Comprehensive Rules loaded here.</p>
    {#if parent}
      <p class="m-0 text-fg-body">Try <a href="/rules/{parent}">rule {parent}</a> instead.</p>
    {/if}
  {:else if error}
    <p role="alert" class="m-0 text-danger">{error}</p>
  {:else if rule}
    {#if rule.ancestors.length}
      <nav aria-label="Rule hierarchy">
        <ol class="m-0 flex list-none flex-wrap gap-1.5 p-0 font-mono text-[13px]">
          {#each rule.ancestors as ancestor, i (ancestor.rule_id)}
            <li class="flex gap-1.5">
              <a href="/rules/{ancestor.rule_id}">{ancestor.rule_id} {ancestor.text}</a>
              {#if i < rule.ancestors.length - 1}
                <span aria-hidden="true" class="text-fg-muted">›</span>
              {/if}
            </li>
          {/each}
        </ol>
      </nav>
    {/if}

    <h1 class="m-0 font-mono text-2xl font-medium">Rule {rule.rule_id}</h1>
    <p class="m-0 text-[17px] leading-7 whitespace-pre-wrap text-fg-body">{rule.text}</p>

    {#if rule.subrules.length}
      <h2 class="mt-4 mb-0 text-lg font-medium">Subrules</h2>
      <ul class="m-0 flex list-none flex-col gap-2 p-0">
        {#each rule.subrules as subrule (subrule.rule_id)}
          <li
            class="rounded-[10px] border border-line bg-card px-4 py-3 text-sm leading-normal text-fg-body"
          >
            <a class="mr-2 font-mono" href="/rules/{subrule.rule_id}">{subrule.rule_id}</a>{subrule.text}
          </li>
        {/each}
      </ul>
    {/if}

    {#if rule.rules_ingested_at}
      <p class="text-[13px] text-fg-muted">
        Comprehensive Rules as ingested {rule.rules_ingested_at}.
      </p>
    {/if}
  {/if}
</main>
