import { expect, test } from '@playwright/test';
import { askFixture } from './helpers';

test('the images fixture renders the cited cards', async ({ page }) => {
  await askFixture(page);
  // By selector, not role: once the art is inside a button (Task 5), ARIA
  // makes the image presentational.
  await expect(page.locator('#source-4 img')).toBeVisible();
});
