<script lang="ts">
  import type { ClassValue } from 'svelte/elements';

  type Name =
    | 'clock'
    | 'pause'
    | 'warning'
    | 'info'
    | 'chevron-left'
    | 'chevron-right'
    | 'close'
    | 'spinner';

  let { name, size = 18, class: cls = '' }: { name: Name; size?: number; class?: ClassValue } =
    $props();
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
  class={cls}
>
  {#if name === 'clock'}<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />
  {:else if name === 'pause'}<circle cx="12" cy="12" r="9" /><path d="M10 9v6M14 9v6" />
  {:else if name === 'warning'}<path d="M12 3l9.5 17h-19z" /><path d="M12 10v4M12 17.5v.01" />
  {:else if name === 'info'}<circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" />
  {:else if name === 'chevron-left'}<path d="M15 6l-6 6 6 6" />
  {:else if name === 'chevron-right'}<path d="M9 6l6 6-6 6" />
  {:else if name === 'close'}<path d="M6 6l12 12M18 6L6 18" />
  {:else if name === 'spinner'}<path d="M12 3a9 9 0 1 0 9 9" />
  {/if}
</svg>
