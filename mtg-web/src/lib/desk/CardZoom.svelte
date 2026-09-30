<script lang="ts">
  import { page } from '$app/state';
  import { closeZoom } from '$lib/overlays';

  let dialog: HTMLDialogElement;
  let opener: HTMLElement | null = null;
  let src = $state('');
  const zoom = $derived(page.state.zoom);

  $effect(() => {
    if (zoom && !dialog.open) {
      opener = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!zoom && dialog.open) {
      dialog.close();
    }
  });

  // `normal` first (usually cached by the hover preview), `large` once it
  // has loaded. A failed `large` leaves `normal` up.
  $effect(() => {
    if (!zoom) return;
    src = zoom.normal;
    const large = zoom.large;
    if (!large) return;
    let live = true;
    const img = new Image();
    img.onload = () => live && (src = large);
    img.src = large;
    return () => {
      live = false;
    };
  });

  // Escape closes the dialog natively; going back drops the history entry.
  // After back has already cleared page.state, closeZoom does nothing.
  function onclose() {
    closeZoom();
    opener?.focus();
  }
</script>

<!-- Any click closes it; keyboard users have Escape (native). -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialog}
  {onclose}
  onclick={closeZoom}
  aria-label={zoom?.name ?? 'Card'}
  class="m-auto max-h-none max-w-none cursor-zoom-out border-0 bg-transparent p-0 backdrop:bg-scrim"
>
  {#if zoom}
    <img
      {src}
      alt={zoom.name}
      class="block h-auto rounded-[4.75%/3.5%]"
      style:width="min(92vw, calc(90dvh * 488 / 680))"
      style:aspect-ratio="488 / 680"
    />
  {/if}
</dialog>
