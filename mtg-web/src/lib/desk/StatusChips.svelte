<script lang="ts">
  import type { QueryResponse, ReplayResponse } from '$lib/api';
  import { shortDate } from '$lib/format';
  import { answersLeftLabel, isRunningLow } from '$lib/status';

  let {
    response,
    isAdmin,
    loading,
    compact,
    replay = null,
    onfresh
  }: {
    response: QueryResponse;
    isAdmin: boolean;
    loading: boolean;
    compact: boolean;
    // Set when re-rendering a history row: marks it and hides the fresh button.
    replay?: Pick<ReplayResponse, 'id' | 'created_at'> | null;
    onfresh: () => void;
  } = $props();

  const count = $derived(response.citations.length);
  const remaining = $derived(response.answers_remaining);
</script>

<div
  class="flex flex-wrap items-center gap-2 font-mono text-[11px] text-fg-muted sm:gap-2.5 sm:text-xs"
>
  {#if response.citation_stats.uncited_answer}
    <span class="rounded bg-caution-chip px-2 py-[3px] text-caution">No sources cited</span>
  {:else}
    <span class="rounded bg-teal-chip px-2 py-[3px] text-teal">
      {count}
      {compact ? 'cited' : `source${count === 1 ? '' : 's'} cited`}
    </span>
  {/if}
  {#if replay}
    <span class="rounded bg-chip px-2 py-[3px]">
      replay · #{replay.id} · {shortDate(replay.created_at)}
    </span>
  {/if}
  {#if response.cached_at}
    <span class="rounded bg-chip px-2 py-[3px]">
      cached · {isAdmin ? 'first generated ' : ''}{shortDate(response.cached_at)}
    </span>
    {#if isAdmin && !replay}
      <button
        type="button"
        onclick={onfresh}
        disabled={loading}
        class="min-h-9 cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-3 font-sans text-[13px] text-gold disabled:text-fg-disabled"
        >Get a fresh answer</button
      >
    {/if}
  {/if}
  {#if remaining !== null}
    {#if isRunningLow(remaining)}
      <span class="rounded bg-notice px-2 py-[3px] text-notice-fg">
        {answersLeftLabel(remaining, compact)}
      </span>
    {:else}
      <span>{answersLeftLabel(remaining, compact)}</span>
    {/if}
  {/if}
</div>
