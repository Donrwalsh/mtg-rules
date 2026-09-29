<script lang="ts">
  import { goto } from '$app/navigation';
  import { login, RateLimitedError } from '$lib/api';
  import { refreshAdmin } from '$lib/admin';

  let password = '';
  let error = '';
  let busy = false;

  async function onSubmit() {
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

<main>
  <h1>Log in</h1>
  <form on:submit|preventDefault={onSubmit}>
    <input type="password" bind:value={password} autocomplete="current-password" />
    <button type="submit" disabled={busy}>Log in</button>
  </form>
  {#if error}
    <p style="color: red">{error}</p>
  {/if}
</main>
