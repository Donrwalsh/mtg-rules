<script lang="ts">
  import type { Citation } from '$lib/api';
  import { assignSentences, layoutAnswer, type Piece } from '$lib/answer/layout';
  import { sourceKind } from '$lib/evidence';
  import { segmentAnswer } from '$lib/segments';
  import type { Selection } from '$lib/selection';
  import CitationMarker from './CitationMarker.svelte';
  import RuleLink from './RuleLink.svelte';

  let {
    answer,
    citations,
    ruleReferences,
    selection,
    canHover,
    onselect
  }: {
    answer: string;
    citations: Citation[];
    ruleReferences: string[];
    selection: Selection | null;
    canHover: boolean;
    onselect: (number: number, occurrence: number) => void;
  } = $props();

  const byNumber = $derived(new Map(citations.map((c) => [c.number, c])));
  const pieces = $derived(
    assignSentences(segmentAnswer(answer, new Set(byNumber.keys()), new Set(ruleReferences)))
  );
  const layout = $derived(layoutAnswer(pieces));

  // The sentence to highlight: the one the clicked marker closes, or the
  // first sentence citing the selected source.
  const marked = $derived.by(() => {
    if (!selection) return null;
    const { number, occurrence } = selection;
    const piece =
      occurrence !== null
        ? pieces[occurrence]
        : pieces.find((p) => p.kind === 'cite' && p.numbers.includes(number));
    return piece ? piece.sentence : null;
  });
  const markRuling = $derived(
    selection ? sourceKind(byNumber.get(selection.number)?.source_type ?? '') === 'ruling' : false
  );

  const isActive = (n: number, index: number) =>
    selection?.number === n && (selection.occurrence === null || selection.occurrence === index);

  // Leading whitespace stays outside the highlight.
  const leadingSpace = (text: string) => text.length - text.trimStart().length;
</script>

<!-- Every piece of model output is rendered as text; nothing uses {@html}.
     The paragraphs are pre-wrap, so the snippet only breaks lines inside tags. -->
{#snippet run(list: Piece[])}{#each list as p, i (i)}{#if p.kind === 'text'}{#if p.sentence === marked && p.text.trim()}{p.text.slice(0, leadingSpace(p.text))}<mark
          class={[
            'rounded-[3px] px-[3px]',
            markRuling ? 'bg-teal-mark text-teal-mark-fg' : 'bg-gold-mark text-gold-hover'
          ]}>{p.text.slice(leadingSpace(p.text))}</mark
        >{:else}{p.text}{/if}{:else if p.kind === 'rule'}<RuleLink
        ruleId={p.ruleId}
        key={String(p.index)}
        {canHover}
      />{:else}{#each p.numbers as n, j (n)}{@const c = byNumber.get(n)}{#if c}{#if j}&nbsp;{/if}<CitationMarker
            citation={c}
            occurrence={p.index}
            active={isActive(n, p.index)}
            {canHover}
            {onselect}
          />{/if}{/each}{/if}{/each}{/snippet}

{#if layout.lead}
  <p
    class="m-0 text-[21px] leading-[1.35] font-medium text-fg sm:text-2xl desk:text-[26px] desk:leading-[1.4]"
  >
    {@render run(layout.lead)}
  </p>
{/if}
{#each layout.paragraphs as paragraph, i (i)}
  <p class="m-0 text-base leading-[1.75] whitespace-pre-wrap text-fg-body sm:text-[17px]">{@render run(paragraph)}</p>
{/each}
