# Frontend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `mtg-web` to Svelte 5 (runes), Tailwind v4 with the Judge's Desk tokens, and self-hosted fonts, with a shared header and every existing page restyled. No behaviour changes.

**Architecture:** First the toolchain is upgraded and test/typecheck runners are added while the old components still run (Svelte 5 still accepts the old syntax). Then Tailwind and the tokens land in `src/app.css`. Last, each component is rewritten in runes and restyled with token utilities, one group at a time, keeping every page working after each task.

**Tech Stack:** SvelteKit 2 + Svelte 5, adapter-static, Vite, Tailwind CSS v4 (`@tailwindcss/vite`), `@fontsource/ibm-plex-sans`, `@fontsource/jetbrains-mono`, Vitest, svelte-check, TypeScript.

**Spec:** [docs/superpowers/specs/2026-09-29-frontend-foundation-design.md](../specs/2026-09-29-frontend-foundation-design.md)

## Global Constraints

- Dark only. All colours come from the `@theme` tokens in `src/app.css`; Tailwind's default palette is cleared with `--color-*: initial;`. No hex colours in `.svelte` files.
- Tailwind v4 is configured in CSS only: no `tailwind.config.js`, no PostCSS config.
- Fonts are self-hosted via `@fontsource` (IBM Plex Sans 400/500/600/400-italic, JetBrains Mono 400/500). No Google Fonts requests.
- Svelte 5 runes everywhere. By the end: no `export let`, `$:`, `on:` directives, `<slot>`, `createEventDispatcher` or `svelte/store` in `src/`.
- Breakpoints: Tailwind `sm` = 640px; custom `desk` = 1100px.
- Keep behaviour identical: same routes, API calls, admin gating, citation popovers, SPA fallback.
- Model output is never rendered with `{@html}`.
- Node 22 (Dockerfile `node:22-slim` and CI `node-version: 22`).
- Commits: conventional prefixes (`feat:`, `fix:`, `chore:`, `test:`, `docs:`, `style:`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Branch: `feature/frontend-foundation`, cut from `main`.

## File Structure

- `mtg-web/package.json`: upgraded deps, new devDeps, `check` and `test` scripts.
- `mtg-web/tsconfig.json` (new): extends SvelteKit's generated config, for `svelte-check`.
- `mtg-web/vite.config.ts` (replaces `vite.config.js`): Tailwind plugin and Vitest config.
- `mtg-web/src/app.css` (new): Tailwind import, `@theme` tokens, fonts, base styles.
- `mtg-web/src/app.html`: `class="dark"` color scheme meta.
- `mtg-web/src/lib/admin.svelte.ts` (replaces `admin.ts`): rune-based admin state.
- `mtg-web/src/lib/AppHeader.svelte` (new): wordmark, optional `center` snippet, nav.
- `mtg-web/src/lib/segments.test.ts` (new): first Vitest suite.
- `mtg-web/src/lib/CitedAnswer.svelte`, `SourcesList.svelte`: runes and restyle.
- `mtg-web/src/routes/+layout.svelte`, `+page.svelte`, `rules/[id]/+page.svelte`, `history/+page.svelte`, `login/+page.svelte`, `admin/usage/+page.svelte`: runes, header and restyle.
- `.github/workflows/frontend-tests.yml`: run `check` and `test`.
- `README.md`: frontend commands.

---

### Task 1: Toolchain upgrade, typecheck and test runner

**Files:**
- Modify: `mtg-web/package.json`, `mtg-web/package-lock.json`
- Create: `mtg-web/tsconfig.json`, `mtg-web/vite.config.ts`, `mtg-web/src/lib/segments.test.ts`
- Delete: `mtg-web/vite.config.js`
- Modify: `.github/workflows/frontend-tests.yml`

**Interfaces:**
- Produces: `npm run check` (svelte-check), `npm test` (vitest run). Test files live next to their module as `src/**/*.test.ts`.

- [ ] **Step 1: Upgrade and add packages**

Run (in `mtg-web/`):

```bash
npm install -D svelte@5 @sveltejs/kit@latest @sveltejs/vite-plugin-svelte@latest @sveltejs/adapter-static@latest vite@latest
npm install -D svelte-check@latest typescript@latest vitest@latest
```

If npm reports a peer conflict between `vite` and `@sveltejs/vite-plugin-svelte`, install the `vite` major that the plugin's `peerDependencies` names (`npm view @sveltejs/vite-plugin-svelte peerDependencies`). Record the resolved versions in the commit message body.

- [ ] **Step 2: Add scripts** (in `package.json` `"scripts"`)

```json
"dev": "vite dev",
"build": "vite build",
"preview": "vite preview",
"check": "svelte-kit sync && svelte-check --tsconfig ./tsconfig.json",
"test": "vitest run"
```

- [ ] **Step 3: Create `tsconfig.json`**

```json
{
  "extends": "./.svelte-kit/tsconfig.json",
  "compilerOptions": {
    "allowJs": true,
    "checkJs": false,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "skipLibCheck": true,
    "sourceMap": true,
    "strict": true,
    "moduleResolution": "bundler"
  }
}
```

- [ ] **Step 4: Replace `vite.config.js` with `vite.config.ts`**

```ts
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    // Same-origin API calls in `npm run dev`, as nginx does in Docker.
    proxy: { '/api': 'http://localhost:8000' }
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node'
  }
});
```

Run: `git rm mtg-web/vite.config.js`

- [ ] **Step 5: Write the first test** (`src/lib/segments.test.ts`)

Covers the existing `segmentAnswer` so the runner is proven on real code:

```ts
import { describe, expect, it } from 'vitest';
import { segmentAnswer } from './segments';

describe('segmentAnswer', () => {
  it('splits text, citation markers and rule references', () => {
    const segs = segmentAnswer('Lethal [1, 2]. See 702.2c.', new Set([1, 2]), new Set(['702.2c']));
    expect(segs).toEqual([
      { kind: 'text', text: 'Lethal ' },
      { kind: 'cite', numbers: [1, 2] },
      { kind: 'text', text: '. See ' },
      { kind: 'rule', ruleId: '702.2c' },
      { kind: 'text', text: '.' }
    ]);
  });

  it('leaves unknown citation numbers and unknown rules as text', () => {
    const segs = segmentAnswer('Odd [9] and 999.1a.', new Set([1]), new Set());
    expect(segs).toEqual([{ kind: 'text', text: 'Odd [9] and 999.1a.' }]);
  });

  it('keeps only the valid numbers of a mixed marker', () => {
    const segs = segmentAnswer('X [1, 9]', new Set([1]), new Set());
    expect(segs).toEqual([
      { kind: 'text', text: 'X ' },
      { kind: 'cite', numbers: [1] }
    ]);
  });

  it('does not treat prices or versions as rule numbers', () => {
    const segs = segmentAnswer('$100.50 or v100.5.2', new Set(), new Set(['100.5']));
    expect(segs).toEqual([{ kind: 'text', text: '$100.50 or v100.5.2' }]);
  });
});
```

- [ ] **Step 6: Run everything**

Run (in `mtg-web/`): `npm test && npm run check && npm run build`
Expected: 4 tests PASS; `svelte-check` reports 0 errors (warnings about legacy syntax are expected and fine at this point; if svelte-check reports **errors** in existing files, fix only what's needed to type-check, e.g. add missing types); build succeeds.

Run `npm run dev` and click through `/`, a rule page, `/login`. Everything should work unchanged.

- [ ] **Step 7: CI** (in `.github/workflows/frontend-tests.yml`, after "Install dependencies")

```yaml
      - name: Type-check
        run: npm run check

      - name: Unit tests
        run: npm test
```

- [ ] **Step 8: Commit**

```bash
git add mtg-web/package.json mtg-web/package-lock.json mtg-web/tsconfig.json mtg-web/vite.config.ts mtg-web/src/lib/segments.test.ts .github/workflows/frontend-tests.yml
git commit -m "chore: upgrade to Svelte 5, add svelte-check and Vitest"
```

---

### Task 2: Tailwind v4, Judge's Desk tokens and self-hosted fonts

**Files:**
- Modify: `mtg-web/package.json`, `mtg-web/package-lock.json`, `mtg-web/vite.config.ts`, `mtg-web/src/app.html`, `mtg-web/src/routes/+layout.svelte`
- Create: `mtg-web/src/app.css`

**Interfaces:**
- Produces: utilities `bg-*`, `text-*`, `border-*` for every token in the spec's table (e.g. `bg-page`, `bg-card`, `text-fg-muted`, `border-line-strong`, `bg-gold`, `text-gold-ink`, `bg-teal-chip`, `bg-scrim`); `font-sans`, `font-mono`; variant `desk:` (≥1100px).

- [ ] **Step 1: Install**

Run (in `mtg-web/`): `npm install -D tailwindcss@4 @tailwindcss/vite@4 @fontsource/ibm-plex-sans @fontsource/jetbrains-mono`

- [ ] **Step 2: Add the plugin** (in `vite.config.ts`)

```ts
import tailwindcss from '@tailwindcss/vite';
// ...
  plugins: [tailwindcss(), sveltekit()],
```

- [ ] **Step 3: Create `src/app.css`**

```css
@import '@fontsource/ibm-plex-sans/400.css';
@import '@fontsource/ibm-plex-sans/400-italic.css';
@import '@fontsource/ibm-plex-sans/500.css';
@import '@fontsource/ibm-plex-sans/600.css';
@import '@fontsource/jetbrains-mono/400.css';
@import '@fontsource/jetbrains-mono/500.css';
@import 'tailwindcss';

/* Judge's Desk design tokens: values from the design canvas
   (docs/superpowers/specs/2026-09-29-frontend-foundation-design.md). */
@theme {
  --color-*: initial;

  --color-page: #121418;
  --color-panel: #16191e;
  --color-well: #181b20;
  --color-card: #1c1f25;
  --color-field: #1b1e24;
  --color-chip: #23262d;
  --color-skeleton: #1d2026;
  --color-art: #2c2c30;

  --color-line: #2a2e36;
  --color-line-strong: #353a44;
  --color-line-muted: #4a505c;

  --color-fg: #e7e5df;
  --color-fg-body: #cfccc4;
  --color-fg-soft: #a9aeb7;
  --color-fg-muted: #8d929c;
  --color-fg-disabled: #5f6570;

  --color-gold: #e7c170;
  --color-gold-hover: #f3d796;
  --color-gold-ink: #16181c;
  --color-gold-wash: #211e16;
  --color-gold-mark: #3a3320;
  --color-gold-off: #4a4332;
  --color-gold-off-fg: #cbbd97;

  --color-notice: #2a2416;
  --color-notice-line: #5c4a1f;
  --color-notice-fg: #d9cfb6;

  --color-teal: #9fd3c7;
  --color-teal-wash: #182224;
  --color-teal-line: #2f4a47;
  --color-teal-chip: #24323a;
  --color-teal-mark: #1f3331;
  --color-teal-mark-fg: #bfe6dc;

  --color-paused: #b9c9e6;
  --color-paused-bg: #1b2230;
  --color-paused-line: #34425c;
  --color-paused-fg: #d6e1f5;

  --color-caution: #f0c28a;
  --color-caution-bg: #221c14;
  --color-caution-line: #4d3a22;
  --color-caution-chip: #3a2a18;
  --color-caution-fg: #e3cfae;

  --color-danger: #f0a3a3;
  --color-danger-line: #8a3a3a;

  --color-scrim: rgb(6 7 9 / 0.62);
  --color-transparent: transparent;
  --color-current: currentColor;

  --font-sans: 'IBM Plex Sans', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;

  --breakpoint-desk: 1100px;
}

@layer base {
  :root {
    color-scheme: dark;
  }
  body {
    @apply bg-page font-sans text-fg antialiased;
  }
  a {
    @apply text-gold hover:text-gold-hover;
  }
  :focus-visible {
    outline: 2px solid var(--color-gold);
    outline-offset: 2px;
  }
}
```

- [ ] **Step 4: Load it and set the colour scheme**

In `src/routes/+layout.svelte`, first line of `<script>`: `import '../app.css';`

In `src/app.html` `<head>`, add: `<meta name="color-scheme" content="dark" />` and `<meta name="theme-color" content="#121418" />`.

- [ ] **Step 5: Verify**

Run: `npm run build && npm run check`
Expected: PASS. Then `npm run dev`: every page now has the dark background, light text and Plex Sans. DevTools → Network → Font shows `.woff2` from `localhost`, none from Google. Check that `bg-page` really came from the token (inspect `body`: `background-color: var(--color-page)`).

- [ ] **Step 6: Commit**

```bash
git add mtg-web/package.json mtg-web/package-lock.json mtg-web/vite.config.ts mtg-web/src/app.css mtg-web/src/app.html mtg-web/src/routes/+layout.svelte
git commit -m "feat: Tailwind v4 with Judge's Desk tokens and self-hosted fonts"
```

---

### Task 3: Rune-based admin state and the app header

**Files:**
- Create: `mtg-web/src/lib/admin.svelte.ts`, `mtg-web/src/lib/AppHeader.svelte`
- Delete: `mtg-web/src/lib/admin.ts`
- Modify: `mtg-web/src/routes/+layout.svelte` and every page that imports `isAdmin` (`+page.svelte` only today)

**Interfaces:**
- Produces: `admin` (a `$state` object `{ isAdmin: boolean }`) and `refreshAdmin(): Promise<void>` from `$lib/admin.svelte`; `<AppHeader center={snippet?} />` from `$lib/AppHeader.svelte`.

- [ ] **Step 1: `src/lib/admin.svelte.ts`**

```ts
import { fetchAdminStatus } from './api';

// Whether this browser holds an admin session. Admin links and controls
// render only when true; the backend enforces access either way.
export const admin = $state({ isAdmin: false });

export async function refreshAdmin(): Promise<void> {
  admin.isAdmin = await fetchAdminStatus();
}
```

Run: `git rm mtg-web/src/lib/admin.ts`

- [ ] **Step 2: `src/lib/AppHeader.svelte`**

```svelte
<script lang="ts">
  import type { Snippet } from 'svelte';
  import { goto } from '$app/navigation';
  import { admin, refreshAdmin } from '$lib/admin.svelte';
  import { logout } from '$lib/api';

  // `center` sits between the wordmark and the nav (the search form, on /).
  let { center }: { center?: Snippet } = $props();

  async function onLogout() {
    await logout();
    await refreshAdmin();
    goto('/');
  }
</script>

<header
  class="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-line bg-page px-4 py-3 sm:px-8 sm:py-[18px]"
>
  <a href="/" class="font-mono text-sm font-medium whitespace-nowrap text-gold no-underline">
    mtg/rules
  </a>
  {#if center}
    <div class="order-last w-full desk:order-none desk:w-auto desk:flex-1">
      {@render center()}
    </div>
  {/if}
  <nav class="ml-auto flex items-center gap-5 text-sm" aria-label="Main">
    <!-- /rules arrives with the secondary-pages PR; until then, the first rule. -->
    <a href="/rules/100">Rules</a>
    {#if admin.isAdmin}
      <a href="/history">History</a>
      <a href="/admin/usage">Usage</a>
      <button
        type="button"
        class="cursor-pointer border-0 bg-transparent p-0 text-sm text-gold hover:text-gold-hover"
        onclick={onLogout}>Log out</button
      >
    {/if}
  </nav>
</header>
```

- [ ] **Step 3: `src/routes/+layout.svelte`**

```svelte
<script lang="ts">
  import '../app.css';
  import type { Snippet } from 'svelte';
  import { onMount } from 'svelte';
  import { refreshAdmin } from '$lib/admin.svelte';

  let { children }: { children: Snippet } = $props();

  onMount(refreshAdmin);
</script>

{@render children()}
```

- [ ] **Step 4: Update the admin import on `/`**

In `src/routes/+page.svelte`: replace `import { isAdmin } from '$lib/admin';` with `import { admin } from '$lib/admin.svelte';` and `$isAdmin` with `admin.isAdmin`. (The rest of that file is rewritten in Task 4; this keeps it compiling now.) Add `<AppHeader />` (import it) as the first element of the page's markup, and likewise at the top of `rules/[id]`, `history`, `login` and `admin/usage` pages.

- [ ] **Step 5: Verify**

Run: `npm run check && npm run build`
Expected: PASS. In `npm run dev`: every page shows the header; logged out, the nav shows only "Rules"; after `/login` with the dev admin password (`MTG_API_ADMIN_PASSWORD` in `.env`), History, Usage and Log out appear; Log out hides them and lands on `/`. Resize to 390px: nav stays on the wordmark's row (wraps if needed), no horizontal scroll.

- [ ] **Step 6: Commit**

```bash
git add -A mtg-web/src
git commit -m "feat: shared app header and rune-based admin state"
```

---

### Task 4: Search page and citation components in runes, restyled

**Files:**
- Modify: `mtg-web/src/lib/CitedAnswer.svelte`, `mtg-web/src/lib/SourcesList.svelte`, `mtg-web/src/routes/+page.svelte`

**Interfaces:**
- Consumes: `admin` (Task 3), tokens (Task 2).
- Produces: `CitedAnswer` props `{ answer: string; citations?: Citation[]; ruleReferences?: string[]; idPrefix?: string }`; `SourcesList` props `{ citations?: Citation[]; results?: QueryResult[]; idPrefix?: string; expanded?: boolean }` (unchanged names, so `/history` keeps working).

- [ ] **Step 1: Rewrite `CitedAnswer.svelte`**

Keep the markup on one line inside `<p>` (whitespace matters with `white-space: pre-wrap`):

```svelte
<script lang="ts">
  import { isExternalUrl, type Citation } from '$lib/api';
  import { segmentAnswer } from '$lib/segments';

  let {
    answer,
    citations = [],
    ruleReferences = [],
    // Keeps popover ids unique when several answers share a page (history).
    idPrefix = 'answer'
  }: { answer: string; citations?: Citation[]; ruleReferences?: string[]; idPrefix?: string } =
    $props();

  const byNumber = $derived(new Map(citations.map((c) => [c.number, c])));
  const segments = $derived(
    segmentAnswer(answer, new Set(byNumber.keys()), new Set(ruleReferences))
  );

  // Popovers are keyed per marker occurrence ("<segment>-<number>"), not per
  // source number: the same source can be cited several times in one answer.
  let open = $state<string | null>(null);

  function hide(key: string) {
    if (open === key) open = null;
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') open = null;
  }

  function href(c: Citation): string {
    return c.url ?? `#${idPrefix}-source-${c.number}`;
  }
</script>

<!-- Every piece of model output is rendered as text; nothing uses {@html}. -->
<p class="leading-relaxed whitespace-pre-wrap text-fg-body">{#each segments as seg, i}{#if seg.kind === 'text'}{seg.text}{:else if seg.kind === 'rule'}<a class="font-mono" href="/rules/{seg.ruleId}">{seg.ruleId}</a>{:else}{#each seg.numbers as n}{@const c = byNumber.get(n)}{@const key = `${i}-${n}`}{#if c}<span class="relative"><sup><a
            class="rounded border border-line-muted px-1 font-mono text-xs text-fg-body no-underline"
            href={href(c)}
            target={isExternalUrl(c.url) ? '_blank' : undefined}
            rel={isExternalUrl(c.url) ? 'noopener noreferrer' : undefined}
            aria-describedby="{idPrefix}-pop-{key}"
            onmouseenter={() => (open = key)}
            onmouseleave={() => hide(key)}
            onfocus={() => (open = key)}
            onblur={() => hide(key)}
            onkeydown={onKeydown}>{n}</a></sup><span
          role="tooltip"
          id="{idPrefix}-pop-{key}"
          class="absolute bottom-[1.8em] left-0 z-10 w-[min(28rem,80vw)] rounded-lg border border-line-strong bg-card px-3 py-2 text-sm leading-snug whitespace-normal text-fg shadow-[0_8px_24px_rgb(0_0_0/0.4)]"
          hidden={open !== key}><strong class="font-mono text-xs text-fg-muted">{c.title}</strong><span class="mt-1 block whitespace-pre-wrap text-fg-body">{c.text}</span></span></span>{/if}{/each}{/if}{/each}</p>
```

The shadow uses an arbitrary value because the default palette (including `black`) is cleared.

- [ ] **Step 2: Rewrite `SourcesList.svelte`**

```svelte
<script lang="ts">
  import { isExternalUrl, type Citation, type QueryResult } from '$lib/api';

  let {
    citations = [],
    results = [],
    idPrefix = 'answer',
    // Open "Also retrieved" by default, e.g. when there is no answer to cite from.
    expanded = false
  }: {
    citations?: Citation[];
    results?: QueryResult[];
    idPrefix?: string;
    expanded?: boolean;
  } = $props();

  const uncited = $derived(results.filter((r) => !r.cited));
</script>

{#if citations.length}
  <h3 class="mt-6 mb-2 font-mono text-xs tracking-widest text-fg-muted uppercase">Sources</h3>
  <ol class="flex list-none flex-col gap-3 p-0">
    {#each citations as c (c.number)}
      <li
        value={c.number}
        id="{idPrefix}-source-{c.number}"
        class="rounded-[10px] border border-line bg-card px-4 py-3"
      >
        <span class="font-mono text-xs text-fg-muted">{c.number} · </span>
        {#if c.url}
          <a
            href={c.url}
            target={isExternalUrl(c.url) ? '_blank' : undefined}
            rel={isExternalUrl(c.url) ? 'noopener noreferrer' : undefined}>{c.title}</a
          >
        {:else}
          {c.title}
        {/if}
        <p class="mt-1 mb-0 text-sm leading-normal whitespace-pre-wrap text-fg-body">{c.text}</p>
      </li>
    {/each}
  </ol>
{/if}

{#if uncited.length}
  <details open={expanded} class="mt-6">
    <summary class="cursor-pointer text-sm text-fg-muted">Also retrieved ({uncited.length})</summary>
    <ul class="mt-3 flex list-none flex-col gap-3 p-0">
      {#each uncited as result}
        <li class="rounded-[10px] border border-line bg-card px-4 py-3">
          <strong>{result.title}</strong>
          <span class="font-mono text-xs text-fg-muted">({result.source}, score {result.score})</span>
          <p class="mt-1 mb-0 text-sm leading-normal whitespace-pre-wrap text-fg-body">
            {result.text}
          </p>
        </li>
      {/each}
    </ul>
  </details>
{/if}
```

- [ ] **Step 3: Rewrite `src/routes/+page.svelte`** (same behaviour as today)

```svelte
<script lang="ts">
  import { MAX_QUERY_CHARS, RateLimitedError, submitQuery, type QueryResponse } from '$lib/api';
  import { admin } from '$lib/admin.svelte';
  import AppHeader from '$lib/AppHeader.svelte';
  import CitedAnswer from '$lib/CitedAnswer.svelte';
  import SourcesList from '$lib/SourcesList.svelte';

  let query = $state('');
  let response = $state<QueryResponse | null>(null);
  let error = $state('');
  let loading = $state(false);

  async function ask(fresh = false) {
    // A fresh request must regenerate the question whose cached answer is
    // on screen, not whatever is currently sitting in the input box.
    const q = fresh && response ? response.query : query;
    error = '';
    loading = true;
    try {
      response = await submitQuery(q, { fresh });
    } catch (e) {
      error = e instanceof RateLimitedError ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function onSubmit(event: SubmitEvent) {
    event.preventDefault();
    ask();
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  // Quotas and the budget reset at UTC midnight; show it in local time.
  function resetTime(): string {
    const now = new Date();
    const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
    return next.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
</script>

<AppHeader />

<main class="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-8 sm:px-8">
  <h1 class="m-0 text-2xl font-medium">Ask a rules question</h1>
  <form
    class="flex items-center gap-3 rounded-[10px] border border-line-strong bg-field py-1.5 pr-1.5 pl-4"
    onsubmit={onSubmit}
  >
    <label for="q" class="font-mono text-[13px] text-fg-muted">Q</label>
    <input
      id="q"
      type="text"
      class="min-h-8 min-w-0 flex-1 border-0 bg-transparent text-base text-fg outline-none"
      bind:value={query}
      maxlength={MAX_QUERY_CHARS}
      placeholder="Ask a rules question"
    />
    <button
      type="submit"
      class="min-h-10 cursor-pointer rounded-[7px] border-0 bg-gold px-[18px] text-sm font-semibold text-gold-ink disabled:cursor-default disabled:bg-gold-off disabled:text-gold-off-fg"
      disabled={loading}>{loading ? 'Searching…' : 'Search'}</button
    >
  </form>
  {#if query.length > MAX_QUERY_CHARS - 100}
    <p class="m-0 text-right font-mono text-xs text-fg-muted">{query.length} / {MAX_QUERY_CHARS}</p>
  {/if}

  {#if error}
    <p role="alert" class="m-0 text-sm text-danger">{error}</p>
  {/if}

  {#if response}
    {#if response.degraded}
      <p role="status" class="m-0 rounded-[10px] border border-notice-line bg-notice px-4 py-3 text-sm text-notice-fg">
        {#if response.degraded === 'global_budget'}
          AI answers are paused for today. They resume at {resetTime()}. Here are the matching
          rules, rulings and cards.
        {:else if response.answers_remaining}
          You're asking quickly, so AI answers pause for a few minutes. Here are the matching
          rules, rulings and cards.
        {:else}
          You've used today's AI answers. They reset at {resetTime()}. Here are the matching
          rules, rulings and cards.
        {/if}
      </p>
    {/if}

    {#if response.answer}
      <section class="flex flex-col gap-2">
        {#if response.cached_at}
          <p class="m-0 flex items-center gap-3 font-mono text-xs text-fg-muted">
            <span class="rounded bg-chip px-2 py-0.5">cached · first generated {formatDate(response.cached_at)}</span>
            {#if admin.isAdmin}
              <button
                type="button"
                class="min-h-9 cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-3 font-sans text-sm text-gold"
                onclick={() => ask(true)}
                disabled={loading}>Get a fresh answer</button
              >
            {/if}
          </p>
        {/if}
        <CitedAnswer
          answer={response.answer}
          citations={response.citations}
          ruleReferences={response.rule_references}
        />
        {#if response.citation_stats.uncited_answer}
          <p class="m-0 text-sm text-caution">No sources cited</p>
        {/if}
      </section>
    {/if}

    {#if response.answers_remaining !== null && !response.degraded}
      <p class="m-0 font-mono text-xs text-fg-muted">
        {response.answers_remaining} AI answer{response.answers_remaining === 1 ? '' : 's'} left today
      </p>
    {/if}

    <SourcesList
      citations={response.citations}
      results={response.results}
      expanded={!response.answer}
    />
  {/if}
</main>
```

- [ ] **Step 4: Verify**

Run: `npm run check && npm test && npm run build`
Expected: PASS, no svelte-check warnings for these three files.

With the dev stack up (`docker compose up -d`, then `npm run dev`): ask "Does trample plus deathtouch only need 1 damage on each blocker?". Check that the answer renders with outlined numeric markers; hovering or focusing a marker shows the dark popover; Escape closes it; rule numbers link to `/rules/…`; Sources and "Also retrieved" render as dark cards; a 429 (hit Search rapidly) shows the red alert.

- [ ] **Step 5: Commit**

```bash
git add mtg-web/src/lib/CitedAnswer.svelte mtg-web/src/lib/SourcesList.svelte mtg-web/src/routes/+page.svelte
git commit -m "feat: search page and citations in Svelte 5 runes with the new theme"
```

---

### Task 5: Rule, history, login and usage pages in runes, restyled

**Files:**
- Modify: `mtg-web/src/routes/rules/[id]/+page.svelte`, `mtg-web/src/routes/history/+page.svelte`, `mtg-web/src/routes/login/+page.svelte`, `mtg-web/src/routes/admin/usage/+page.svelte`

**Interfaces:**
- Consumes: `AppHeader`, `CitedAnswer`, `SourcesList` (Tasks 3–4), `page` from `$app/state`.

Shared page shell for all four: `<AppHeader />` then `<main class="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-8 sm:px-8">`. Shared styles: `h1` → `class="m-0 text-2xl font-medium"`; `h2` → `class="mt-4 mb-0 text-lg font-medium"`; tables → `class="w-full border-collapse text-sm"`, `th` → `class="border-b border-line-strong px-2 py-1.5 text-left font-mono text-xs font-normal text-fg-muted"`, `td` → `class="border-b border-line px-2 py-1.5 align-top"`; errors → `<p role="alert" class="m-0 text-danger">`; muted notes → `class="text-sm text-fg-muted"`; secondary buttons → `class="min-h-10 cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-4 text-sm text-fg disabled:text-fg-disabled"`.

- [ ] **Step 1: `rules/[id]/+page.svelte`**

Script (behaviour identical: stale responses ignored):

```svelte
<script lang="ts">
  import { page } from '$app/state';
  import AppHeader from '$lib/AppHeader.svelte';
  import { fetchRule, NotFoundError, type RuleDetail } from '$lib/api';

  let rule = $state<RuleDetail | null>(null);
  let notFound = $state(false);
  let error = $state('');
  let loading = $state(false);

  const id = $derived(page.params.id ?? '');

  $effect(() => {
    load(id);
  });

  async function load(ruleId: string) {
    loading = true;
    rule = null;
    notFound = false;
    error = '';
    try {
      const fetched = await fetchRule(ruleId);
      // Ignore a slow response for a rule the user already navigated away from.
      if (ruleId === id) rule = fetched;
    } catch (e) {
      if (ruleId !== id) return;
      if (e instanceof NotFoundError) notFound = true;
      else error = String(e);
    } finally {
      if (ruleId === id) loading = false;
    }
  }

  // Best guess at an unknown rule's parent: 702.99z -> 702.99, 702.99 -> 702.
  function parentGuess(ruleId: string): string | null {
    const sub = ruleId.match(/^(\d{3}\.\d+)[a-z]$/);
    if (sub) return sub[1];
    const top = ruleId.match(/^(\d{3})\.\d+$/);
    return top ? top[1] : null;
  }

  const parent = $derived(parentGuess(id));
</script>
```

Markup: keep the existing structure (`<svelte:head>` title, back link, loading, not found with parent suggestion, error, breadcrumbs `<nav aria-label="Rule hierarchy">`, `<h1>Rule {id}</h1>`, text, subrules list, ingest note), with these classes:
- breadcrumbs `<ol class="m-0 flex list-none flex-wrap gap-1.5 p-0 font-mono text-[13px]">`, each non-last `<li>` followed by `<span aria-hidden="true" class="text-fg-muted">›</span>` (replacing the `::after` CSS);
- `<h1 class="m-0 font-mono text-2xl font-medium">Rule {rule.rule_id}</h1>`;
- rule text `<p class="m-0 text-[17px] leading-7 whitespace-pre-wrap text-fg-body">`;
- subrules `<ul class="m-0 flex list-none flex-col gap-2 p-0">`, each `<li class="rounded-[10px] border border-line bg-card px-4 py-3 text-sm leading-normal text-fg-body"><a class="mr-2 font-mono" href=…>{id}</a>{text}</li>`;
- ingest note `class="text-[13px] text-fg-muted"`.

Delete the `<style>` block.

- [ ] **Step 2: `history/+page.svelte`**

Runes: `let rows = $state<QueryHistoryRow[]>([])`, `let offset = $state(0)`, `let error = $state('')`, `let expandedId = $state<number | null>(null)`; `load()` called once at the top level of the script as today. Replace `on:click` with `onclick`. Rows: `<tr class="cursor-pointer hover:bg-card" onclick={() => toggle(row.id)}>`; the cached marker becomes `<span class="ml-1 rounded bg-chip px-1.5 font-mono text-[11px] text-fg-muted">cached</span>`; expanded cell `<td colspan="6" class="bg-panel px-4 py-4">`; raw results `<pre class="overflow-x-auto rounded-lg bg-well p-3 font-mono text-xs whitespace-pre-wrap break-words">`. Prev/Next use the secondary-button class. Delete the `<style>` block. `CitedAnswer`/`SourcesList` usage and props stay as they are.

- [ ] **Step 3: `login/+page.svelte`**

```svelte
<script lang="ts">
  import { goto } from '$app/navigation';
  import AppHeader from '$lib/AppHeader.svelte';
  import { login, RateLimitedError } from '$lib/api';
  import { refreshAdmin } from '$lib/admin.svelte';

  let password = $state('');
  let error = $state('');
  let busy = $state(false);

  async function onSubmit(event: SubmitEvent) {
    event.preventDefault();
    error = '';
    busy = true;
    try {
      if (await login(password)) {
        await refreshAdmin();
        goto('/');
      } else {
        error = 'Incorrect password.';
      }
    } catch (e) {
      error = e instanceof RateLimitedError ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<AppHeader />

<main class="mx-auto flex max-w-sm flex-col gap-4 px-4 py-16">
  <h1 class="m-0 text-2xl font-medium">Log in</h1>
  <form class="flex flex-col gap-3" onsubmit={onSubmit}>
    <label for="pw" class="text-sm text-fg-muted">Admin password</label>
    <input
      id="pw"
      type="password"
      class="min-h-11 rounded-[10px] border border-line-strong bg-field px-4 text-base text-fg outline-none focus:border-gold"
      bind:value={password}
      autocomplete="current-password"
      aria-describedby={error ? 'login-error' : undefined}
    />
    <button
      type="submit"
      class="min-h-11 cursor-pointer rounded-[7px] border-0 bg-gold text-sm font-semibold text-gold-ink disabled:bg-gold-off disabled:text-gold-off-fg"
      disabled={busy}>Log in</button
    >
  </form>
  {#if error}
    <p id="login-error" role="alert" class="m-0 text-sm text-danger">{error}</p>
  {/if}
</main>
```

- [ ] **Step 4: `admin/usage/+page.svelte`**

Runes: `let usage = $state<UsageSummary | null>(null)`, `let error = $state('')`, the existing `fetchUsage().then(...)` at top level, `const today = $derived(usage ? usage.days[usage.days.length - 1] : null)`. Same markup with the shared table classes; the "Today" paragraph becomes a `rounded-[10px] border border-line bg-card px-4 py-3` box with the spend in `font-mono`. Delete the `<style>` block.

- [ ] **Step 5: Confirm the migration is complete**

Run (in `mtg-web/`):

```bash
grep -rnE "export let|\\$:|on:[a-z]+=|<slot|svelte/store|createEventDispatcher|<style" src || echo "clean"
```

Expected: `clean`.

Run: `npm run check && npm test && npm run build`
Expected: PASS with 0 errors and 0 warnings.

- [ ] **Step 6: Visual check**

With the dev stack and `npm run dev`, at 1280px and 390px wide:
- `/rules/702.2c` shows the breadcrumbs, text and ingest note; `/rules/702.99z` shows "No rule" and suggests `702.99`; navigating from 702.2 to a subrule updates the page.
- `/login` shows the wrong-password error and then logs in.
- `/history` rows expand and show the answer and sources; paging works.
- `/admin/usage` shows today's spend and both tables.
- No page scrolls horizontally at 390px (the history table may scroll inside its own container: wrap it in `<div class="overflow-x-auto">`).

- [ ] **Step 7: Commit**

```bash
git add mtg-web/src/routes
git commit -m "feat: rule, history, login and usage pages in runes with the new theme"
```

---

### Task 6: README and the final check

**Files:**
- Modify: `README.md` (Frontend commands around line 265; `mtg-web` row in the components table around line 42)

- [ ] **Step 1: Update the README**

In the components table, change the `mtg-web/` description to start with: "SvelteKit (Svelte 5) SPA styled with Tailwind v4 (tokens in `src/app.css`)."

Under "Frontend:", after `npm run build`, add:

```bash
npm run check      # svelte-check (types, Svelte diagnostics)
npm test           # Vitest unit tests (src/**/*.test.ts)
```

- [ ] **Step 2: Full check**

Run (in `mtg-web/`): `npm ci && npm run check && npm test && npm run build && docker build .`
Expected: all PASS.

- [ ] **Step 3: Commit and open the PR**

```bash
git add README.md
git commit -m "docs: frontend check and test commands"
git push -u origin feature/frontend-foundation
gh pr create --title "Frontend foundation: Svelte 5, Tailwind v4, Judge's Desk tokens" --body "$(cat <<'EOF'
Part 1 of 4 of the Judge's Desk redesign. Spec: docs/superpowers/specs/2026-09-29-frontend-foundation-design.md

- Svelte 5 with every component in runes; current SvelteKit and Vite
- Tailwind v4 set up in CSS, with the design's colour and type tokens; self-hosted IBM Plex Sans and JetBrains Mono
- Shared AppHeader; all existing pages restyled dark, with no behaviour changes
- svelte-check and Vitest, both run in CI

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
