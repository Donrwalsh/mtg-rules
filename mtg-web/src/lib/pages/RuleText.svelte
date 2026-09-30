<script lang="ts">
  import { splitRuleText } from '$lib/ruleText';

  let { text }: { text: string } = $props();

  const parts = $derived(splitRuleText(text));
</script>

<!-- Rule text is rendered as text parts; references become links. -->
{#each parts as part, i (i)}{#if part.kind === 'text'}{part.text}{:else}<a
      href="/rules/{part.ruleId}"
      class="font-mono text-[0.9em]">{part.text}</a
    >{/if}{/each}
