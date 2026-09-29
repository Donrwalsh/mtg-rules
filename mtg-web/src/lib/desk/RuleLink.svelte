<script lang="ts">
  import { previewRule, type RulePreview } from '$lib/rulePreview';
  import Popover from './Popover.svelte';

  let { ruleId, key, canHover }: { ruleId: string; key: string; canHover: boolean } = $props();

  let open = $state(false);
  let preview = $state<RulePreview | null>(null);

  async function show() {
    if (!canHover) return;
    open = true;
    preview ??= await previewRule(ruleId);
  }
</script>

<span class="relative"
  ><a
    href="/rules/{ruleId}"
    class="font-mono text-[0.9em]"
    aria-describedby={canHover && preview ? `rule-pop-${key}` : undefined}
    onmouseenter={show}
    onmouseleave={() => (open = false)}
    onfocus={show}
    onblur={() => (open = false)}
    onkeydown={(e) => e.key === 'Escape' && (open = false)}>{ruleId}</a
  >{#if preview}<Popover
      id="rule-pop-{key}"
      {open}
      title={preview.title}
      text={preview.text}
    />{/if}</span
>
