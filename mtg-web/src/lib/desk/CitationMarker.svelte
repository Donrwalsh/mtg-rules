<script lang="ts">
  import type { Citation } from '$lib/api';
  import { sourceKind } from '$lib/evidence';
  import Popover from './Popover.svelte';

  let {
    citation,
    occurrence,
    active,
    canHover,
    onselect
  }: {
    citation: Citation;
    occurrence: number;
    active: boolean;
    canHover: boolean;
    onselect: (number: number, occurrence: number) => void;
  } = $props();

  let open = $state(false);
  const popId = $derived(`cite-pop-${occurrence}-${citation.number}`);
  const ruling = $derived(sourceKind(citation.source_type) === 'ruling');
  const title = $derived(
    citation.rule_id && citation.heading
      ? `${citation.rule_id} · ${citation.heading}`
      : citation.title
  );

  function onclick(event: MouseEvent) {
    event.preventDefault();
    open = false;
    onselect(citation.number, occurrence);
  }
</script>

<span class="relative inline-block"
  ><a
    href="#source-{citation.number}"
    aria-current={active ? 'true' : undefined}
    aria-describedby={canHover ? popId : undefined}
    {onclick}
    onmouseenter={() => canHover && (open = true)}
    onmouseleave={() => (open = false)}
    onfocus={() => canHover && (open = true)}
    onblur={() => (open = false)}
    onkeydown={(e) => e.key === 'Escape' && (open = false)}
    class={[
      'inline-flex items-center rounded-[5px] border px-[5px] py-px font-mono text-xs leading-none no-underline max-sm:min-h-8 max-sm:px-2 max-sm:text-[13px]',
      active && ruling && 'border-teal bg-teal text-gold-ink hover:text-gold-ink',
      active && !ruling && 'border-gold bg-gold text-gold-ink hover:text-gold-ink',
      !active && 'border-line-muted text-fg-body hover:text-fg'
    ]}>{citation.number}</a
  >{#if canHover}<Popover id={popId} {open} {title} text={citation.text} />{/if}</span
>
