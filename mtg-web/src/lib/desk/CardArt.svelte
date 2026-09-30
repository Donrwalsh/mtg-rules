<script lang="ts">
  import { MediaQuery } from 'svelte/reactivity';
  import { PREVIEW_SIZE, placePreview } from '$lib/cardPreview';
  import { openZoom } from '$lib/overlays';

  // Card art that zooms when clicked and, with a mouse, previews larger on
  // hover. `thumb` is what shows at `width`×`height`; `normal` feeds the
  // preview and the zoom, which swaps in `large` when there is one.
  let {
    name,
    thumb,
    normal,
    large = null,
    width,
    height,
    class: className = ''
  }: {
    name: string;
    thumb: string;
    normal: string;
    large?: string | null;
    width: number;
    height: number;
    class?: string;
  } = $props();

  const HOVER_DELAY_MS = 150;
  const finePointer = new MediaQuery('(hover: hover) and (pointer: fine)');

  let button: HTMLButtonElement;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let spot = $state<{ left: number; top: number } | null>(null);

  function show() {
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    spot = placePreview(button.getBoundingClientRect(), viewport, PREVIEW_SIZE, 12);
  }

  function hide() {
    clearTimeout(timer);
    spot = null;
  }

  function onpointerenter(event: PointerEvent) {
    if (!finePointer.current || event.pointerType !== 'mouse') return;
    clearTimeout(timer);
    timer = setTimeout(show, HOVER_DELAY_MS);
  }

  function onclick(event: MouseEvent) {
    // Inside the sheet's clickable rows, the art zooms instead.
    event.stopPropagation();
    hide();
    openZoom({ name, normal, large });
  }

  // Any scroll, including the evidence panel's own, moves the art away
  // from a fixed preview.
  $effect(() => {
    if (!spot) return;
    window.addEventListener('scroll', hide, { capture: true, passive: true });
    return () => window.removeEventListener('scroll', hide, { capture: true });
  });

  $effect(() => () => clearTimeout(timer));
</script>

<button
  bind:this={button}
  type="button"
  aria-label="Enlarge {name}"
  {onclick}
  {onpointerenter}
  onpointerleave={hide}
  class={['shrink-0 cursor-zoom-in border-0 bg-transparent p-0', className]}
  style:width="{width}px"
  style:height="{height}px"
>
  <img
    src={thumb}
    alt=""
    {width}
    {height}
    loading="lazy"
    decoding="async"
    class="block size-full rounded-[inherit]"
  />
</button>
{#if spot}
  <img
    data-testid="card-preview"
    src={normal}
    alt=""
    class="pointer-events-none fixed z-30 rounded-[4.75%/3.5%] shadow-[0_8px_24px_rgb(0_0_0/0.4)]"
    style:left="{spot.left}px"
    style:top="{spot.top}px"
    style:width="{PREVIEW_SIZE.width}px"
    style:height="{PREVIEW_SIZE.height}px"
  />
{/if}
