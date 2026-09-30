import { expect, type Page } from '@playwright/test';

// 1×1 PNG standing in for every Scryfall image, so no test needs the network.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64'
);

export async function askFixture(page: Page): Promise<void> {
  // No backend: meta and admin status fall back to their defaults.
  await page.route('**/api/v1/**', (route) => route.fulfill({ status: 503, body: '' }));
  await page.route('https://cards.scryfall.io/**', async (route) => {
    // Delay `large` so the zoom visibly starts on `normal`.
    if (route.request().url().includes('/large/')) await new Promise((r) => setTimeout(r, 300));
    await route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
  });
  await page.goto('/?mock=images');
  const box = page.getByRole('textbox');
  await box.fill('Does trample plus deathtouch only need 1 damage on each blocker?');
  await box.press('Enter');
  await expect(page.locator('#source-4')).toBeVisible();
}
