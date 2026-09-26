<script lang="ts">
  import { page } from '$app/stores';
  import { fetchRule, NotFoundError, type RuleDetail } from '$lib/api';

  let rule: RuleDetail | null = null;
  let notFound = false;
  let error = '';
  let loading = false;

  $: id = $page.params.id;
  $: load(id);

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
    const rule = ruleId.match(/^(\d{3})\.\d+$/);
    return rule ? rule[1] : null;
  }

  $: parent = parentGuess(id);
</script>

<svelte:head>
  <title>Rule {id} — MTG Rules</title>
</svelte:head>

<main>
  <p><a href="/">&larr; Back to search</a></p>

  {#if loading}
    <p>Loading rule {id}…</p>
  {:else if notFound}
    <h1>No rule {id}</h1>
    <p>There's no rule {id} in the Comprehensive Rules loaded here.</p>
    {#if parent}
      <p>Try <a href="/rules/{parent}">rule {parent}</a> instead.</p>
    {/if}
  {:else if error}
    <p style="color: red">{error}</p>
  {:else if rule}
    {#if rule.ancestors.length}
      <nav aria-label="Rule hierarchy">
        <ol class="crumbs">
          {#each rule.ancestors as ancestor}
            <li><a href="/rules/{ancestor.rule_id}">{ancestor.rule_id} {ancestor.text}</a></li>
          {/each}
        </ol>
      </nav>
    {/if}

    <h1>Rule {rule.rule_id}</h1>
    <p class="rule-text">{rule.text}</p>

    {#if rule.subrules.length}
      <h2>Subrules</h2>
      <ul class="subrules">
        {#each rule.subrules as subrule}
          <li><a href="/rules/{subrule.rule_id}">{subrule.rule_id}</a> {subrule.text}</li>
        {/each}
      </ul>
    {/if}

    {#if rule.rules_ingested_at}
      <p class="meta">Comprehensive Rules as ingested {rule.rules_ingested_at}.</p>
    {/if}
  {/if}
</main>

<style>
  .crumbs {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    list-style: none;
    padding: 0;
    margin: 0 0 0.5rem;
    font-size: 0.9rem;
  }
  .crumbs li:not(:last-child)::after {
    content: '›';
    margin-left: 0.4rem;
    color: #888;
  }
  .rule-text {
    white-space: pre-wrap;
    font-size: 1.05rem;
  }
  .subrules li {
    margin-bottom: 0.4rem;
  }
  .meta {
    color: #666;
    font-size: 0.85rem;
  }
</style>
