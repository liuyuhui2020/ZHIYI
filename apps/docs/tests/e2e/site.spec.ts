import { expect, test, type Page } from '@playwright/test';

type PagefindResult = { data: () => Promise<{ url: string }> };
type PagefindModule = {
  init: () => Promise<void>;
  search: (query: string) => Promise<{ results: PagefindResult[] }>;
};

const DOCUMENT_ROUTES = [
  ['/docs/product-value/', '产品价值'],
  ['/docs/requirements/', '需求文档'],
  ['/docs/features/', '功能文档'],
  ['/docs/architecture/', '技术方案'],
  ['/docs/roadmap/', '项目说明'],
  ['/docs/development/sdd/', 'SDD 开发规范'],
  ['/docs/development/agent-guidelines/', 'Agent 工作规范'],
] as const;

test('homepage states the product, maturity, and credible next actions', async ({ page }) => {
  await page.goto('/');

  await expect(page).toHaveTitle(/ZHIYI/);
  await expect(page.getByRole('heading', { level: 1 })).toContainText('可靠运行');
  await expect(page.getByTestId('project-status')).toContainText('方案基线');
  await expect(page.getByTestId('project-status')).toContainText('Runtime 尚未实现');
  await expect(page.getByRole('link', { name: '阅读技术方案', exact: true })).toHaveAttribute('href', '/docs/architecture/');
  await expect(page.getByRole('link', { name: '查看项目路线图' })).toHaveAttribute('href', '/docs/roadmap/');
  await expect(page.getByRole('link', { name: '进入文档' }).first()).toHaveAttribute('href', '/docs/product-value/');
  await expect(page.getByRole('link', { name: /GitHub/ }).first()).toHaveAttribute('href', 'https://github.com/liuyuhui2020/ZHIYI');
});

test('homepage metadata is complete and no tracking scripts are present', async ({ page }) => {
  await page.goto('/');

  await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', /Agent/);
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', 'http://localhost:4321/');
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', /ZHIYI/);
  await expect(page.locator('meta[property="og:description"]')).toHaveAttribute('content', /Agent/);
  await expect(page.locator('script[src*="analytics"], script[src*="segment"], script[src*="gtag"]')).toHaveCount(0);
});

test('homepage core content and navigation work without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto('/');

  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await expect(page.getByRole('link', { name: '阅读技术方案', exact: true })).toBeVisible();
  await context.close();
});

for (const [route, title] of DOCUMENT_ROUTES) {
  test(`deep documentation route renders and identifies its location: ${route}`, async ({ page }) => {
    await page.goto(route);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(title);
    await expect(page.locator(`a[aria-current="page"][href="${route}"]`)).toBeVisible();
  });
}

test('documentation provides section outline and previous or next reading links', async ({ page }) => {
  await page.goto('/docs/architecture/');

  await expect(page.getByRole('heading', { level: 2, name: '总体架构' })).toBeVisible();
  await expect(page.locator('a[href="#3-总体架构"]:visible')).toBeVisible();
  await expect(page.locator('a[rel="prev"], a[rel="next"]')).not.toHaveCount(0);
});

test('Pagefind indexes every published core document', async ({ page }) => {
  await page.goto('/');
  const cases = [
    ['核心价值支柱', '/docs/product-value/'],
    ['NFR-006', '/docs/requirements/'],
    ['ToolInvocation', '/docs/features/'],
    ['双持久化边界', '/docs/architecture/'],
    ['风险登记', '/docs/roadmap/'],
    ['设计漂移', '/docs/development/sdd/'],
    ['禁止行为', '/docs/development/agent-guidelines/'],
  ] as const;

  for (const [query, expectedPath] of cases) {
    const paths = await page.evaluate(async (searchTerm) => {
      const modulePath = '/pagefind/pagefind.js';
      const pagefind = await import(/* @vite-ignore */ modulePath) as PagefindModule;
      await pagefind.init();
      const search = await pagefind.search(searchTerm);
      const data = await Promise.all(search.results.slice(0, 8).map((result) => result.data()));
      return data.map((result) => new URL(result.url, window.location.href).pathname);
    }, query);
    expect(paths, `${query} should find ${expectedPath}`).toContain(expectedPath);
  }
});

test('search UI supports a Chinese query and a recoverable no-results state', async ({ page }) => {
  await page.goto('/docs/architecture/');

  await openSearch(page);
  const input = page.getByRole('searchbox').or(page.getByRole('textbox', { name: /搜索/ })).first();
  await input.fill('幂等');
  await expect(page.getByRole('link', { name: /幂等|技术方案|需求文档/ }).first()).toBeVisible();

  await input.fill('zhiyi-guaranteed-no-result-7f4a9e');
  await expect(page.getByText(/没有|无.*结果|未找到/).first()).toBeVisible();
  await input.fill('AgentVersion');
  await expect(page.getByRole('link', { name: /AgentVersion|技术方案|功能文档/ }).first()).toBeVisible();
});

test('site does not request runtime assets from external origins', async ({ page }) => {
  const externalRequests = new Set<string>();
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) externalRequests.add(url.origin);
  });

  await page.goto('/docs/architecture/');
  await page.waitForLoadState('networkidle');
  expect([...externalRequests]).toEqual([]);
});

for (const width of [320, 768, 1280, 1920]) {
  test(`homepage and docs do not overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ['/', '/docs/architecture/']) {
      await page.goto(route);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route} overflow at ${width}px`).toBeLessThanOrEqual(1);
    }
  });
}

test('keyboard reaches skip link, navigation, search, and theme controls', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();

  await page.goto('/docs/architecture/');
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+k' : 'Control+k');
  const searchDialog = page.getByRole('dialog', { name: '搜索' });
  await expect(searchDialog).toBeVisible();
  await expect(searchDialog.getByRole('textbox', { name: /搜索/ })).toBeFocused();
});

test('explicit theme selection persists between homepage and documentation', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /切换为浅色主题|主题/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await page.goto('/docs/architecture/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('homepage respects system theme when storage is unavailable', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => {
      throw new Error('storage unavailable');
    };
  });
  await page.goto('/');

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('mobile navigation opens and reaches a documentation destination', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');

  const mobileNavigation = page.locator('.mobile-nav');
  await mobileNavigation.locator('summary[aria-label="打开主导航"]').click();
  await expect(mobileNavigation.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await mobileNavigation.getByRole('link', { name: '架构', exact: true }).click();

  await expect(page).toHaveURL('/docs/architecture/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('技术方案');
});

test('not-found page explains the error and offers working recovery actions', async ({ page }) => {
  const response = await page.goto('/missing-route-for-acceptance');

  expect(response?.status()).toBe(404);
  await expect(page.getByRole('heading', { level: 1 })).toContainText('执行路径不存在');
  await expect(page.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/');
  await page.locator('#main-content').getByRole('link', { name: '进入文档' }).click();
  await expect(page).toHaveURL('/docs/product-value/');
});

test('reduced motion disables decorative animation', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const duration = await page.locator('[data-signal-path]').first().evaluate((element) => getComputedStyle(element).animationDuration);
  expect(duration).toBe('0s');
});

test('Mermaid renders lazily and locally in both themes without overflow or console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.goto('/docs/architecture/');
  const diagram = page.locator('.mermaid').first();
  await diagram.scrollIntoViewIfNeeded();
  await expect(diagram.locator('svg')).toBeVisible();
  await expect(diagram).toHaveAttribute('data-render-theme', 'dark');

  await page.getByRole('combobox', { name: '选择主题' }).selectOption('light');
  await expect(diagram).toHaveAttribute('data-render-theme', 'light');
  await expect(diagram.locator('svg')).toBeVisible();

  const overflow = await diagram.evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
});

async function openSearch(page: Page) {
  const button = page.getByRole('button', { name: /搜索/ }).first();
  await button.click();
  await expect(page.getByRole('dialog')).toBeVisible();
}
