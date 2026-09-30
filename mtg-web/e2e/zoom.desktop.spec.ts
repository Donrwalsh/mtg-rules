import { expect, test, type Page } from '@playwright/test';
import { askFixture } from './helpers';

test.beforeEach(async ({ page }) => {
  await askFixture(page);
});

const art = (page: Page) => page.getByRole('button', { name: 'Enlarge Colossal Dreadmaw' });
const zoom = (page: Page) => page.getByRole('dialog', { name: 'Colossal Dreadmaw', exact: true });

test('hovering art shows a preview inside the viewport', async ({ page }) => {
  await art(page).hover();
  const preview = page.getByTestId('card-preview');
  await expect(preview).toBeVisible();
  await expect(preview).toBeInViewport({ ratio: 1 });
  await page.mouse.move(5, 5);
  await expect(preview).toBeHidden();
});

test('clicking art zooms, hides the preview, then swaps in the large image', async ({ page }) => {
  await art(page).hover();
  await expect(page.getByTestId('card-preview')).toBeVisible();
  await art(page).click();
  await expect(zoom(page)).toBeVisible();
  await expect(page.getByTestId('card-preview')).toBeHidden();
  await expect(zoom(page).getByRole('img')).toHaveAttribute('src', /\/large\//);
});

test('Escape closes the zoom and returns focus to the art', async ({ page }) => {
  await art(page).click();
  await expect(zoom(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(zoom(page)).toBeHidden();
  await expect(art(page)).toBeFocused();
});

test('keyboard opens the zoom', async ({ page }) => {
  await art(page).focus();
  await page.keyboard.press('Enter');
  await expect(zoom(page)).toBeVisible();
  await expect(page.getByTestId('card-preview')).toHaveCount(0);
});

test('a click anywhere closes the zoom', async ({ page }) => {
  await art(page).click();
  await expect(zoom(page)).toBeVisible();
  await page.mouse.click(10, 10);
  await expect(zoom(page)).toBeHidden();
});

test('back closes the zoom and stays on the page', async ({ page }) => {
  await art(page).click();
  await expect(zoom(page)).toBeVisible();
  await page.goBack();
  await expect(zoom(page)).toBeHidden();
  await expect(art(page)).toBeVisible();
  await expect(page).toHaveURL(/mock=images/);
});

test('a card without art has no zoom', async ({ page }) => {
  await expect(page.locator('#source-3')).toContainText('Basilisk Collar');
  await expect(page.getByRole('button', { name: 'Enlarge Basilisk Collar' })).toHaveCount(0);
});
