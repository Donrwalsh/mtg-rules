import { expect, test, type Page } from '@playwright/test';

async function ask(page: Page, fixture: string) {
  // No backend: meta and admin status fall back to their defaults.
  await page.route('**/api/v1/**', (route) => route.fulfill({ status: 503, body: '' }));
  await page.goto(`/?mock=${fixture}`);
  const box = page.getByRole('textbox');
  await box.fill('Does trample plus deathtouch only need 1 damage on each blocker?');
  await box.press('Enter');
}

test('evidence and Thinking… show before any answer text', async ({ page }) => {
  await ask(page, 'thinking');
  await expect(page.getByText('Thinking…')).toBeVisible();
  await expect(page.getByRole('tab', { name: /^Retrieved · \d+$/ })).toBeVisible();
  await expect(page.getByText('Yes — each blocker')).toHaveCount(0);
  await expect(page.getByText('Yes — each blocker')).toBeVisible();
  await expect(page.getByText('Thinking…')).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Cited · 5' })).toBeVisible();
});

test('a citation marker works before the answer finishes', async ({ page }) => {
  await ask(page, 'stalled');
  const marker = page.getByRole('link', { name: '1', exact: true });
  await expect(marker).toBeVisible();
  await marker.click();
  await expect(page.locator('#source-1')).toBeVisible();
});

test('a cut-off answer keeps its text and says so', async ({ page }) => {
  await ask(page, 'cutoff');
  await expect(page.getByText(/cut off before it finished/)).toBeVisible();
  await expect(page.getByText('Yes — each blocker')).toBeVisible();
});

test('a stream that fails with no text shows the no-answer notice', async ({ page }) => {
  await ask(page, 'streamerror');
  await expect(page.getByText("Couldn't write an answer this time")).toBeVisible();
});

test('asking again mid-stream replaces the answer instead of mixing them', async ({ page }) => {
  await ask(page, 'thinking');
  await expect(page.getByText('Thinking…')).toBeVisible();
  await page.getByRole('textbox').press('Enter');
  await expect(page.getByRole('tab', { name: 'Cited · 5' })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('Yes — each blocker only needs 1 damage.')).toHaveCount(1);
});
