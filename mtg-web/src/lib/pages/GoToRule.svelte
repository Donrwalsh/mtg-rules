<script lang="ts">
  import { goto } from '$app/navigation';
  import { normalizeRuleId } from '$lib/rules';

  let { class: cls = '' }: { class?: string } = $props();

  let value = $state('');
  const uid = $props.id();

  function onsubmit(event: SubmitEvent) {
    event.preventDefault();
    const id = normalizeRuleId(value);
    if (id) goto(`/rules/${encodeURIComponent(id)}`);
  }
</script>

<form
  {onsubmit}
  class={[
    'flex items-center gap-2 rounded-[10px] border border-line-strong bg-field py-1 pr-1 pl-3.5',
    cls
  ]}
>
  <label for="goto-{uid}" class="font-mono text-xs whitespace-nowrap text-fg-muted">Go to</label>
  <input
    id="goto-{uid}"
    type="text"
    bind:value
    placeholder="e.g. 702.19b"
    autocomplete="off"
    spellcheck="false"
    class="min-h-9 min-w-0 flex-1 border-0 bg-transparent font-mono text-sm text-fg outline-none placeholder:text-fg-muted"
  />
  <button
    type="submit"
    class="min-h-10 cursor-pointer rounded-[7px] border-0 bg-chip px-3.5 font-sans text-sm font-medium text-fg"
    >Go</button
  >
</form>
