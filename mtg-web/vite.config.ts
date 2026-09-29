import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    // Same-origin API calls in `npm run dev`, as nginx does in Docker.
    proxy: { '/api': 'http://localhost:8000' }
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node'
  }
});
