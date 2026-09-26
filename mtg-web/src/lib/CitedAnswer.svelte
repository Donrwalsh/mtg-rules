<script lang="ts">
  import { isExternalUrl, type Citation } from '$lib/api';
  import { segmentAnswer } from '$lib/segments';

  export let answer: string;
  export let citations: Citation[] = [];
  export let ruleReferences: string[] = [];
  // Keeps popover ids unique when several answers share a page (history).
  export let idPrefix = 'answer';

  $: byNumber = new Map(citations.map((c) => [c.number, c]));
  $: segments = segmentAnswer(answer, new Set(byNumber.keys()), new Set(ruleReferences));

  let open: number | null = null;

  function show(n: number) {
    open = n;
  }

  function hide(n: number) {
    if (open === n) open = null;
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') open = null;
  }

  function href(c: Citation): string {
    return c.url ?? `#${idPrefix}-source-${c.number}`;
  }
</script>

<!-- Every piece of model output is rendered as text; nothing uses {@html}. -->
<p class="answer-text">{#each segments as seg}{#if seg.kind === 'text'}{seg.text}{:else if seg.kind === 'rule'}<a href="/rules/{seg.ruleId}">{seg.ruleId}</a>{:else}{#each seg.numbers as n}{@const c = byNumber.get(n)}{#if c}<span class="cite"><sup><a
            href={href(c)}
            target={isExternalUrl(c.url) ? '_blank' : undefined}
            rel={isExternalUrl(c.url) ? 'noopener noreferrer' : undefined}
            aria-describedby="{idPrefix}-pop-{n}"
            on:mouseenter={() => show(n)}
            on:mouseleave={() => hide(n)}
            on:focus={() => show(n)}
            on:blur={() => hide(n)}
            on:keydown={onKeydown}>[{n}]</a></sup><span
          role="tooltip"
          id="{idPrefix}-pop-{n}"
          class="popover"
          hidden={open !== n}><strong>{c.title}</strong><span class="pop-text">{c.text}</span></span></span>{/if}{/each}{/if}{/each}</p>

<style>
  .answer-text {
    white-space: pre-wrap;
    line-height: 1.5;
  }
  .cite {
    position: relative;
  }
  sup a {
    text-decoration: none;
    padding: 0 0.1em;
  }
  sup a:focus-visible {
    outline: 2px solid #1a5fb4;
    outline-offset: 1px;
  }
  .popover {
    position: absolute;
    left: 0;
    bottom: 1.8em;
    z-index: 10;
    width: min(28rem, 80vw);
    padding: 0.5rem 0.6rem;
    background: #fff;
    color: #222;
    border: 1px solid #ccc;
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    font-size: 0.9rem;
    line-height: 1.35;
    white-space: normal;
  }
  .popover[hidden] {
    display: none;
  }
  .pop-text {
    display: block;
    margin-top: 0.25rem;
    white-space: pre-wrap;
  }
</style>
