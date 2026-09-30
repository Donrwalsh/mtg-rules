import { defineConfig, devices } from '@playwright/test';

// Runs against `vite dev` and the `images` fixture (/?mock=images), so no
// backend is needed. Port 5174 so a normal `npm run dev` can stay up.
const PORT = 5174;

export default defineConfig({
  testDir: 'e2e',
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: { baseURL: `http://localhost:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI
  },
  projects: [
    {
      name: 'desktop',
      testMatch: /\.desktop\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } }
    },
    { name: 'phone', testMatch: /\.phone\.spec\.ts$/, use: { ...devices['Pixel 7'] } }
  ]
});
