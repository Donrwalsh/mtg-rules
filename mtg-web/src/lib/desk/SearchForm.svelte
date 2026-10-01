<script lang="ts">
  import { MediaQuery } from 'svelte/reactivity';

  let {
    value = $bindable(''),
    loading = false,
    answering = false,
    retrievalOnly = false,
    variant = 'header',
    maxChars,
    error = '',
    onsubmit
  }: {
    value?: string;
    loading?: boolean;
    // An answer is still streaming: asking again waits for it.
    answering?: boolean;
    retrievalOnly?: boolean;
    variant?: 'header' | 'hero';
    maxChars: number;
    error?: string;
    onsubmit: () => void;
  } = $props();

  const phone = new MediaQuery('max-width: 639px');
  const uid = $props.id();
  const busy = $derived(loading || answering);
  const label = $derived(
    loading ? 'Searching…' : answering ? 'Answering…' : retrievalOnly ? 'Search' : 'Ask'
  );
  const placeholder = $derived(
    variant === 'hero' ? 'e.g. Can I respond to a spell with split second?' : 'Ask a rules question'
  );

  function submit(event: SubmitEvent) {
    event.preventDefault();
    if (!busy && value.trim()) onsubmit();
  }

  // In the phone textarea, Enter asks (Shift+Enter still adds a line).
  function onTextareaKey(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      (event.currentTarget as HTMLTextAreaElement).form?.requestSubmit();
    }
  }
</script>

<form class="flex flex-col gap-1.5" onsubmit={submit}>
  <div
    class={[
      'flex gap-3 rounded-[10px] border bg-field',
      error ? 'border-danger-line' : 'border-line-strong',
      variant === 'hero' && phone.current ? 'flex-col items-stretch p-3' : 'items-center',
      variant === 'hero' && !phone.current && 'py-2 pr-2 pl-5',
      variant === 'header' && 'py-1 pr-1 pl-3 sm:py-1.5 sm:pr-1.5 sm:pl-4'
    ]}
  >
    <label for="q-{uid}" class={phone.current ? 'sr-only' : 'font-mono text-[13px] text-fg-muted'}>
      {phone.current ? 'Rules question' : 'Q'}
    </label>
    {#if variant === 'hero' && phone.current}
      <textarea
        id="q-{uid}"
        rows="3"
        bind:value
        maxlength={maxChars}
        {placeholder}
        aria-describedby={error ? `qe-${uid}` : undefined}
        onkeydown={onTextareaKey}
        class="min-w-0 resize-none border-0 bg-transparent font-sans text-base text-fg outline-none placeholder:text-fg-muted"
      ></textarea>
    {:else}
      <input
        id="q-{uid}"
        type="text"
        bind:value
        maxlength={maxChars}
        {placeholder}
        aria-describedby={error ? `qe-${uid}` : undefined}
        class="min-h-9 min-w-0 flex-1 border-0 bg-transparent font-sans text-base text-fg outline-none placeholder:text-fg-muted"
      />
    {/if}
    {#if !phone.current}
      <kbd
        class="rounded border border-line-strong px-1.5 py-0.5 font-mono text-xs text-fg-muted"
        aria-hidden="true">Enter</kbd
      >
    {/if}
    <button
      type="submit"
      disabled={busy}
      class="min-h-11 min-w-14 cursor-pointer rounded-[7px] border-0 bg-gold px-[18px] font-sans text-[15px] font-semibold text-gold-ink disabled:cursor-default disabled:bg-gold-off disabled:text-gold-off-fg sm:min-h-10 sm:text-sm"
      >{label}</button
    >
  </div>
  {#if error}
    <p id="qe-{uid}" role="alert" class="m-0 text-sm text-danger">{error}</p>
  {/if}
  {#if value.length >= maxChars - 100}
    <p class="m-0 text-right font-mono text-xs text-fg-muted">{value.length} / {maxChars}</p>
  {/if}
</form>
