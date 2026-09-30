import { expect, test, type Page } from '@playwright/test';
import { askFixture } from './helpers';

test.beforeEach(async ({ page }) => {
  await askFixture(page);
});

const sheet = (page: Page, n: number) =>
  page.getByRole('dialog', { name: new RegExp(`^Source ${n}:`) });
const zoom = (page: Page, name: string) => page.getByRole('dialog', { name, exact: true });

test('a compact row opens the sheet, not a zoom', async ({ page }) => {
  await page.locator('#source-4').tap();
  await expect(sheet(page, 4)).toBeVisible();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeHidden();
});

test('sheet art zooms above the sheet; back closes zoom, then sheet', async ({ page }) => {
  await page.locator('#source-4').tap();
  await sheet(page, 4).getByRole('button', { name: 'Enlarge Colossal Dreadmaw' }).tap();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeVisible();
  await expect(page.getByTestId('card-preview')).toHaveCount(0);

  await page.goBack();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeHidden();
  await expect(sheet(page, 4)).toBeVisible();

  await page.goBack();
  await expect(sheet(page, 4)).toBeHidden();
  await expect(page.locator('#source-4')).toBeVisible();
  await expect(page).toHaveURL(/mock=images/);
});

test('tapping the zoom returns to the sheet', async ({ page }) => {
  await page.locator('#source-4').tap();
  await sheet(page, 4).getByRole('button', { name: 'Enlarge Colossal Dreadmaw' }).tap();
  await zoom(page, 'Colossal Dreadmaw').tap();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeHidden();
  await expect(sheet(page, 4)).toBeVisible();
});

test('the Close button closes the sheet and leaves no history entry', async ({ page }) => {
  await page.locator('#source-4').tap();
  await sheet(page, 4).getByRole('button', { name: 'Close' }).tap();
  await expect(sheet(page, 4)).toBeHidden();
  await expect(page).toHaveURL(/mock=images/);
  // Asking doesn't navigate, so with the sheet's entry gone, back leaves
  // the app entirely (to the blank page before goto).
  await page.goBack();
  await expect(page).not.toHaveURL(/mock=images/);
});

test('ruling art zooms; the rest of the row links to Scryfall', async ({ page }) => {
  await page.locator('#source-5').tap();
  const ruling = sheet(page, 5);
  await ruling.getByRole('button', { name: 'Enlarge Windswift Slice' }).tap();
  await expect(zoom(page, 'Windswift Slice')).toBeVisible();
  await zoom(page, 'Windswift Slice').tap();
  await expect(ruling).toBeVisible();
  await expect(ruling.getByRole('link', { name: /From the card/ })).toHaveAttribute(
    'href',
    /scryfall\.com/
  );
});
