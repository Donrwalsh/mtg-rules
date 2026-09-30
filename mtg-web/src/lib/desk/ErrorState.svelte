<script lang="ts">
  import Icon from './Icon.svelte';

  let {
    message,
    onretry,
    title = "Search didn't go through",
    hint = "The server didn't answer. Your question is still in the box, so you can try again in a moment."
  }: { message: string; onretry: () => void; title?: string; hint?: string } = $props();
</script>

<main class="flex justify-center px-4 py-16 sm:py-24">
  <div role="alert" class="flex w-full max-w-[560px] flex-col items-center gap-3.5 text-center">
    <Icon name="warning" size={40} class="text-danger" />
    <h1 class="m-0 text-2xl font-medium">{title}</h1>
    <p class="m-0 text-base leading-normal text-fg-soft">{hint}</p>
    <button
      type="button"
      onclick={onretry}
      class="min-h-11 cursor-pointer rounded-[7px] border-0 bg-gold px-5 font-sans text-sm font-semibold text-gold-ink"
      >Try again</button
    >
    <details class="text-sm text-fg-muted">
      <summary class="cursor-pointer">Details</summary>
      <code class="font-mono text-xs">{message}</code>
    </details>
  </div>
</main>
