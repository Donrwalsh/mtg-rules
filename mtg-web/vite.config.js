import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    // Same-origin API calls in `npm run dev`, as nginx does in Docker.
    proxy: { '/api': 'http://localhost:8000' }
  }
});
