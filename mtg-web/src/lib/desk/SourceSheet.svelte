<script lang="ts">
  import { parentRule, statLine, type EvidenceItem } from '$lib/evidence';
  import { calendarDate } from '$lib/format';
  import CardArt from './CardArt.svelte';
  import Icon from './Icon.svelte';

  let {
    items,
    index = $bindable(0),
    open,
    onclose,
    onchange
  }: {
    items: EvidenceItem[];
    index?: number;
    open: boolean;
    onclose: () => void;
    onchange?: (item: EvidenceItem) => void;
  } = $props();

  let dialog: HTMLDialogElement;
  let opener: HTMLElement | null = null;
  const item = $derived(items[index]);
  const parent = $derived(item?.ruleId ? parentRule(item.ruleId) : null);
  const external = { target: '_blank', rel: 'noopener noreferrer' };
  const iconButton =
    'flex size-11 cursor-pointer items-center justify-center border-0 bg-transparent text-fg disabled:cursor-default disabled:text-line-muted';
  const outlineButton =
    'flex min-h-12 items-center justify-center rounded-lg border border-line-strong text-sm font-medium no-underline';

  $effect(() => {
    if (open && !dialog.open) {
      opener = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  });

  $effect(() => {
    if (open && item) onchange?.(item);
  });

  // Escape closes the dialog natively while `open` is still true; a close
  // driven by `open` (back, or onclose) arrives with it already false.
  function ondialogclose() {
    if (open) onclose();
    opener?.focus();
  }
</script>

<!-- A click on the backdrop lands on the dialog element itself. Keyboard
     users close it with Escape (native) or the Close button. -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialog}
  onclose={ondialogclose}
  onclick={(e) => e.target === dialog && onclose()}
  aria-label={item ? `Source ${item.number ?? index + 1}: ${item.title}` : 'Source'}
  class="mx-0 mt-auto mb-0 max-h-[85vh] w-full max-w-full overflow-y-auto rounded-t-[18px] border-0 border-t border-line-strong bg-card p-0 text-fg backdrop:bg-scrim"
>
  {#if item}
    <div class="flex min-h-[60vh] flex-col gap-3.5 px-[18px] pt-2 pb-6">
      <div class="h-1 w-10 self-center rounded-sm bg-line-muted" aria-hidden="true"></div>
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1">
          <button
            type="button"
            aria-label="Previous source"
            disabled={index === 0}
            onclick={() => index--}
            class={iconButton}
          >
            <Icon name="chevron-left" size={20} />
          </button>
          <span class="font-mono text-[13px] text-fg-muted"
            >Source {index + 1} of {items.length}</span
          >
          <button
            type="button"
            aria-label="Next source"
            disabled={index === items.length - 1}
            onclick={() => index++}
            class={iconButton}
          >
            <Icon name="chevron-right" size={20} />
          </button>
        </div>
        <button
          type="button"
          aria-label="Close"
          onclick={onclose}
          class="flex size-11 cursor-pointer items-center justify-center rounded-full border-0 bg-chip text-fg"
        >
          <Icon name="close" />
        </button>
      </div>

      {#if item.kind === 'card'}
        <div class="flex gap-4">
          {#if item.card?.image_normal}
            <CardArt
              name={item.card.name}
              thumb={item.card.image_normal}
              normal={item.card.image_normal}
              large={item.card.image_large}
              width={150}
              height={209}
              class="rounded-lg"
            />
          {:else}
            <div
              aria-hidden="true"
              class="flex h-[209px] w-[150px] shrink-0 items-center justify-center rounded-lg border border-line-muted bg-art font-mono text-[11px] text-fg-muted"
            >
              card image
            </div>
          {/if}
          <div class="flex min-w-0 flex-col gap-1.5">
            <div class="font-mono text-xs text-gold">CARD</div>
            <div class="text-[19px] leading-tight font-semibold">
              {item.card?.name ?? item.cardName}
            </div>
            {#if item.card?.mana_cost}
              <div class="font-mono text-xs text-fg-muted">{item.card.mana_cost}</div>
            {/if}
            {#if item.card}<div class="text-[13px] text-fg-muted">{statLine(item.card)}</div>{/if}
          </div>
        </div>
        <p
          class="m-0 rounded-lg bg-panel px-3.5 py-3 text-[15px] leading-[1.6] whitespace-pre-line text-fg-body"
        >
          {item.text}
        </p>
        {#if item.url}
          <div class="mt-auto grid grid-cols-2 gap-2.5">
            <a href="{item.url}#rulings" {...external} class={outlineButton}>Rulings</a>
            <a href={item.url} {...external} class={outlineButton}>Scryfall ↗</a>
          </div>
        {/if}
      {:else if item.kind === 'ruling'}
        <div class="flex flex-col gap-1">
          <div class="flex justify-between font-mono text-xs">
            <span class="text-teal">RULING</span>
            {#if item.publishedAt}
              <span class="text-fg-muted">Published {calendarDate(item.publishedAt)}</span>
            {/if}
          </div>
          <div class="text-[19px] leading-tight font-semibold">Ruling on {item.cardName}</div>
        </div>
        <blockquote
          class="m-0 rounded-lg bg-panel px-4 py-3.5 text-base leading-[1.6] text-fg italic"
        >
          “{item.text}”
        </blockquote>
        {#if item.url}
          <div class="flex items-center gap-3 rounded-[10px] border border-line px-2.5 py-2">
            {#if item.card?.image_normal}
              <CardArt
                name={item.card.name}
                thumb={item.card.image_small ?? item.card.image_normal}
                normal={item.card.image_normal}
                large={item.card.image_large}
                width={40}
                height={56}
                class="rounded"
              />
            {:else}
              <span
                aria-hidden="true"
                class="flex h-14 w-10 shrink-0 items-center justify-center rounded border border-line-muted bg-art font-mono text-[8px] text-fg-muted"
                >art</span
              >
            {/if}
            <a
              href={item.url}
              {...external}
              class="flex min-w-0 flex-1 items-center gap-3 self-stretch text-fg no-underline"
            >
              <span class="flex min-w-0 flex-1 flex-col gap-0.5">
                <span class="text-xs text-fg-muted">From the card</span>
                <span class="text-[15px] font-semibold">{item.cardName}</span>
              </span>
              <Icon name="chevron-right" class="text-fg-muted" />
            </a>
          </div>
          <div class="mt-auto grid grid-cols-2 gap-2.5">
            <a href="{item.url}#rulings" {...external} class={outlineButton}>All rulings (card)</a>
            <a href={item.url} {...external} class={outlineButton}>Scryfall ↗</a>
          </div>
        {/if}
      {:else}
        <div class="flex flex-col gap-1">
          <div class="font-mono text-xs text-fg-muted">RULE {item.ruleId}</div>
          {#if item.heading}
            <div class="text-[19px] leading-tight font-semibold">{item.heading}</div>
          {/if}
        </div>
        <p
          class="m-0 rounded-lg bg-panel px-3.5 py-3 text-[15px] leading-[1.6] whitespace-pre-line text-fg-body"
        >
          {item.text}
        </p>
        <div class="mt-auto grid grid-cols-2 gap-2.5">
          <a href="/rules/{item.ruleId}" class={outlineButton}>Open rule {item.ruleId}</a>
          {#if parent}
            <a href="/rules/{parent}" class={outlineButton}>Parent rule</a>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</dialog>
