<script lang="ts">
  import { isExternalUrl, type Citation } from '$lib/api';
  import { segmentAnswer } from '$lib/segments';

  let {
    answer,
    citations = [],
    ruleReferences = [],
    // Keeps popover ids unique when several answers share a page (history).
    idPrefix = 'answer'
  }: { answer: string; citations?: Citation[]; ruleReferences?: string[]; idPrefix?: string } =
    $props();

  const byNumber = $derived(new Map(citations.map((c) => [c.number, c])));
  const segments = $derived(
    segmentAnswer(answer, new Set(byNumber.keys()), new Set(ruleReferences))
  );

  // Popovers are keyed per marker occurrence ("<segment>-<number>"), not per
  // source number: the same source can be cited several times in one answer.
  let open = $state<string | null>(null);

  function hide(key: string) {
    if (open === key) open = null;
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') open = null;
  }

  function href(c: Citation): string {
    return c.url ?? `#${idPrefix}-source-${c.number}`;
  }
</script>

<!-- Every piece of model output is rendered as text; nothing uses {@html}. -->
<p class="leading-relaxed whitespace-pre-wrap text-fg-body">{#each segments as seg, i}{#if seg.kind === 'text'}{seg.text}{:else if seg.kind === 'rule'}<a class="font-mono" href="/rules/{seg.ruleId}">{seg.ruleId}</a>{:else}{#each seg.numbers as n}{@const c = byNumber.get(n)}{@const key = `${i}-${n}`}{#if c}<span class="relative"><sup><a
            class="rounded border border-line-muted px-1 font-mono text-xs text-fg-body no-underline"
            href={href(c)}
            target={isExternalUrl(c.url) ? '_blank' : undefined}
            rel={isExternalUrl(c.url) ? 'noopener noreferrer' : undefined}
            aria-describedby="{idPrefix}-pop-{key}"
            onmouseenter={() => (open = key)}
            onmouseleave={() => hide(key)}
            onfocus={() => (open = key)}
            onblur={() => hide(key)}
            onkeydown={onKeydown}>{n}</a></sup><span
          role="tooltip"
          id="{idPrefix}-pop-{key}"
          class="absolute bottom-[1.8em] left-0 z-10 w-[min(28rem,80vw)] rounded-lg border border-line-strong bg-card px-3 py-2 text-sm leading-snug whitespace-normal text-fg shadow-[0_8px_24px_rgb(0_0_0/0.4)]"
          hidden={open !== key}><strong class="font-mono text-xs text-fg-muted">{c.title}</strong><span class="mt-1 block whitespace-pre-wrap text-fg-body">{c.text}</span></span></span>{/if}{/each}{/if}{/each}</p>
