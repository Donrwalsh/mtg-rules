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
