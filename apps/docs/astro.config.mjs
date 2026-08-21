import { defineConfig } from 'astro/config';
import { satteri } from '@astrojs/markdown-satteri';
import starlight from '@astrojs/starlight';
import { fileURLToPath } from 'node:url';

import { createSidebar } from './src/lib/document-manifest.mjs';
import { createMermaidSatteriPlugin } from './src/lib/mermaid-plugin.mjs';
import { repositoryLinksSatteri } from './src/lib/repository-links.mjs';

const site = process.env.SITE_URL ?? 'http://localhost:4321';
const mermaidRuntimePath = fileURLToPath(new URL('./src/scripts/mermaid.ts', import.meta.url));
const preserveSearchFocus = `
  window.addEventListener('DOMContentLoaded', () => {
    const search = document.querySelector('site-search');
    const dialog = search?.querySelector('dialog');
    if (!search || !dialog) return;

    const focusInputWhenReady = () => {
      if (!dialog.open || document.activeElement instanceof HTMLInputElement) return;
      const active = document.activeElement;
      const closeButton = search.querySelector('[data-close-modal]');
      if (!dialog.contains(active) || active === dialog || active === closeButton) {
        search.querySelector('input')?.focus();
      }
    };

    new MutationObserver(focusInputWhenReady).observe(dialog, { childList: true, subtree: true });
    dialog.addEventListener('toggle', focusInputWhenReady);
  });
`;

export default defineConfig({
  site,
  output: 'static',
  markdown: {
    processor: satteri({
      mdastPlugins: [repositoryLinksSatteri(), createMermaidSatteriPlugin()],
    }),
  },
  integrations: [
    mermaidRuntime(),
    starlight({
      title: 'ZHIYI',
      description: '面向生产级 AI Agent 的可恢复、可治理、可观测 Runtime 设计基线。',
      logo: {
        src: './src/assets/brand-mark.svg',
        alt: 'ZHIYI 标志',
      },
      favicon: '/favicon.svg',
      disable404Route: true,
      locales: {
        root: {
          label: '简体中文',
          lang: 'zh-CN',
        },
      },
      sidebar: createSidebar(),
      social: [
        {
          icon: 'github',
          label: 'ZHIYI GitHub 仓库',
          href: 'https://github.com/liuyuhui2020/ZHIYI',
        },
      ],
      customCss: ['./src/styles/global.css'],
      pagefind: true,
      pagination: true,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#071012' } },
        { tag: 'meta', attrs: { property: 'og:image', content: new URL('/social-card.svg', site).href } },
        { tag: 'meta', attrs: { name: 'twitter:card', content: 'summary_large_image' } },
        { tag: 'meta', attrs: { name: 'twitter:image', content: new URL('/social-card.svg', site).href } },
        { tag: 'script', content: preserveSearchFocus },
      ],
      credits: false,
    }),
  ],
});

function mermaidRuntime() {
  return {
    name: 'zhiyi-mermaid-runtime',
    hooks: {
      'astro:config:setup': ({ injectScript }) => {
        injectScript('page', `import ${JSON.stringify(mermaidRuntimePath)};`);
      },
    },
  };
}
