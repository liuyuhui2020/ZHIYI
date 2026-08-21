import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

for (const route of ['/', '/docs/architecture/']) {
  for (const theme of ['dark', 'light'] as const) {
    test(`has no critical or serious automated accessibility violations: ${route} (${theme})`, async ({ page }) => {
      await page.addInitScript((selectedTheme) => {
        localStorage.setItem('zhiyi-theme', selectedTheme);
        localStorage.setItem('starlight-theme', selectedTheme);
      }, theme);
      await page.goto(route);
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      const blocking = results.violations.filter(({ impact }) => impact === 'critical' || impact === 'serious');
      expect(blocking).toEqual([]);
    });
  }
}

test('homepage has labelled landmarks and a single primary heading', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('banner')).toBeVisible();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await expect(page.getByRole('main')).toHaveCount(1);
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  await expect(page.getByRole('contentinfo')).toBeVisible();
});

test('focus is visible for interactive homepage controls', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  const focused = page.locator(':focus-visible');
  await expect(focused).toBeVisible();
  const outline = await focused.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outline).not.toBe('none');
});
