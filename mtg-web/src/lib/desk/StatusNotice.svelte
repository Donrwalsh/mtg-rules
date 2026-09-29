<script lang="ts">
  import { resetTime } from '$lib/format';
  import { meta } from '$lib/meta.svelte';
  import type { Notice } from '$lib/status';
  import Icon from './Icon.svelte';

  let { kind, remaining }: { kind: Notice; remaining: number | null } = $props();

  const reset = resetTime();
  const perDay = $derived(meta.answers_per_day);
  const paused = $derived(kind === 'paused');
</script>

<div
  role="status"
  class={[
    'flex items-start gap-4 rounded-xl border px-5 py-[18px] max-sm:gap-3 max-sm:px-4 max-sm:py-3.5',
    paused ? 'border-paused-line bg-paused-bg' : 'border-notice-line bg-notice'
  ]}
>
  <Icon
    name={paused ? 'pause' : kind === 'no_answer' ? 'info' : 'clock'}
    size={22}
    class={['mt-0.5 shrink-0', paused ? 'text-paused' : 'text-gold']}
  />
  {#if kind === 'breather'}
    <div class="text-sm leading-normal text-notice-fg">
      <strong class="text-fg">Taking a short breather.</strong> AI answers pause for a few minutes
      when you ask quickly. You still have {remaining} today. Matching sources are below.
    </div>
  {:else}
    <div class="flex flex-col gap-1">
      <div class={['text-[17px] font-semibold', paused ? 'text-paused-fg' : 'text-fg']}>
        {#if paused}
          AI answers are paused for today
        {:else if kind === 'quota_used'}
          You've used today's {perDay ? `${perDay} ` : ''}AI answers
        {:else}
          Couldn't write an answer this time
        {/if}
      </div>
      <div class="text-sm leading-normal text-notice-fg">
        {#if paused}
          The site has reached its daily limit for everyone, not just you. Answers resume at {reset}
          (midnight UTC). Here are the matching rules, cards and rulings.
        {:else if kind === 'quota_used'}
          They reset at {reset} (midnight UTC). Search still works: here are the rules, cards and
          rulings that match your question.
        {:else}
          Try asking again in a moment. Here are the rules, cards and rulings that match your
          question.
        {/if}
      </div>
    </div>
  {/if}
</div>
