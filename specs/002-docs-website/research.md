# Research: Official Documentation Website

**Date**: 2026-08-21

## Decision

Use Astro 7.2.4 with Starlight 0.41.7 in a static `apps/docs/` project. Use
Starlight's built-in Pagefind search, explicit navigation, theme support, and
Chinese root locale. Read `doc/*.md` through a custom allowlisted Astro content
loader; do not copy or symlink the source documents. Use Mermaid 11.17.0 through
a project-owned Sätteri transform and viewport-lazy client adapter. Use Node 24 LTS in
CI while retaining a Node 22.13+ floor compatible with both Astro and pnpm.

## Framework comparison

| Option | Strengths | Rejected as primary because |
|---|---|---|
| Astro + Starlight | Static-first HTML, built-in Pagefind, Chinese search, theme/i18n/navigation defaults, low framework JS, custom Astro homepage | Selected; 0.x version and external document loading require pinned versions and a small adapter |
| Docusaurus 3.10.2 | Most mature native docs versioning/i18n, direct external docs path, strong React customization | Local search is community-provided, React/MDX/Webpack dependency surface is larger, and versioning has no current product version to serve |
| VitePress 1.6.4 | Simple, built-in local search, Vue customization | Stable release is older, VitePress 2 remains alpha, no native versioning, and Chinese search plus mixed homepage/docs source layout needs more bespoke work |

Docusaurus becomes the migration candidate only if native multi-version
documentation is later made a hard requirement. Versioning is intentionally
deferred until ZHIYI makes its first compatibility promise.

## Verified versions and compatibility

| Component | Version | Compatibility decision |
|---|---:|---|
| Node.js | 24 LTS recommended | Astro 7 requires Node >=22.12 and pnpm 11.22 requires >=22.13; current local 22.23.1 is supported |
| pnpm | 11.22.0 | Pin with `packageManager`; lock all dependencies |
| Astro | 7.2.4 | Static output, Node >=22.12 |
| Starlight | 0.41.7 | Peer dependency Astro ^7.0.2; pin exact 0.x version |
| Mermaid | 11.17.0 | Local bundle only, strict security mode |
| Playwright | 1.62.1 | Production-preview browser acceptance |
| axe-core Playwright | 4.13.0 | Automated critical/serious accessibility checks |
| Lighthouse CI | 0.15.1 | Four-category score gate |

## Direct dependency and license review

All direct dependencies are exact-pinned in `apps/docs/package.json` and
resolved by `apps/docs/pnpm-lock.yaml`. Their package metadata and upstream
repositories were reviewed before the final build.

| Dependency | Purpose | Maintenance source | License | Decision |
|---|---|---|---|---|
| `astro` 7.2.4 | Static site compiler and content collections | withastro/astro | MIT | Accept; active primary framework, exact pin |
| `@astrojs/starlight` 0.41.7 | Accessible documentation shell, navigation, theme, Pagefind | withastro/starlight | MIT | Accept; exact-pin 0.x and regression-test upgrades |
| `@astrojs/markdown-satteri` 0.3.7 | Official Markdown plugin API used by repository-link and Mermaid transforms | withastro/astro monorepo | MIT | Accept; exact-pin 0.x, lock native transitive packages, verify Linux CI build |
| `mermaid` 11.17.0 | Local architecture-diagram enhancement | mermaid-js/mermaid | MIT | Accept; strict mode, lazy import, readable fallback |
| `@astrojs/check` 0.9.10 | Astro/TypeScript static validation | withastro/astro | MIT | Accept as build-time validation only |
| `@playwright/test` 1.62.1 | Production-preview browser acceptance | microsoft/playwright | Apache-2.0 | Accept as test-only dependency; Chromium is CI-installed |
| `@axe-core/playwright` 4.13.0 | Automated accessibility assertions | dequelabs/axe-core-npm | MPL-2.0 | Accept as test-only dependency; no code is redistributed into site output |
| `@lhci/cli` 0.15.1 | Median Lighthouse quality gate | GoogleChrome/lighthouse-ci | Apache-2.0 | Accept as test-only dependency |
| `typescript` 6.0.3 | Type checking for Astro and browser scripts | microsoft/TypeScript | Apache-2.0 | Accept as build-time compiler dependency |

No direct dependency introduces a runtime service, visitor tracking, remote
font, deployment adapter, or production secret. Package-manager build scripts
are denied by default; `apps/docs/pnpm-workspace.yaml` allows only `esbuild`,
which is required by the Astro toolchain. Dependency upgrades remain separate,
reviewed changes with the same build, browser, and quality gates.

## Starlight capability findings

- Pagefind is enabled by default for prerendered builds and provides local,
  low-bandwidth full-text search. Pagefind Extended supports Chinese, Japanese,
  and Korean tokenization.
- Manual or generated sidebars, active-page state, on-page table of contents,
  previous/next navigation, theme switching, edit links, and static SEO metadata
  are built in.
- Root-locale i18n keeps Chinese at stable unprefixed paths and allows a future
  `/en/` tree without publishing an empty language selector today.
- Starlight's `docsLoader()` is fixed to `src/content/docs/`, and its documents
  require a title. Astro's Content Loader API can load files from anywhere, so a
  small project loader can inject metadata while keeping source Markdown intact.
- Starlight does not provide first-class breadcrumbs or generic header menus.
  Current-location requirements can be met by page title, active sidebar state,
  and section TOC without replacing the accessible header.

## Single-source content decision

The loader uses an explicit manifest, not a directory-wide glob. This prevents
future internal documents from becoming public accidentally and provides stable
ASCII URLs independent of Chinese filenames. It derives titles from source H1s,
injects schema metadata, removes the duplicate rendered H1, and maps repository
relative links to published routes or GitHub source URLs.

Alternatives rejected:

- **Committed copies** create two long-lived bodies and guaranteed drift.
- **Git symlinks** are fragile on Windows, editors, hosting systems, and archive
  downloads.
- **Adding site-specific links to source documents** breaks their repository
  reading experience.
- **Unrestricted repository globbing** can expose specs or internal governance
  material without an explicit publication decision.

If Astro 7 external-file watching proves unreliable, the approved fallback is a
clean, deterministic prebuild copy inside `apps/docs/` into an ignored generated directory. It must
never be committed and must retain the same manifest/link tests.

## Mermaid decision

Starlight has no built-in Mermaid renderer. Initial implementation with
`astro-mermaid` 2.1.0 was functionally correct and made no external request, but
its eager import and immediate rendering of all three architecture diagrams
produced 350-420 ms of blocking time and a Lighthouse median performance score
of 0.87 on the representative documentation page. The homepage scored 1.00.

The selected adapter keeps Mermaid 11.17.0 local, escapes fence content into a
readable no-script `<pre>` fallback, imports the renderer only when a diagram
intersects the viewport, uses `securityLevel: 'strict'`, and re-renders visible
diagrams when the theme changes. This is smaller than maintaining a fork of the
community integration and preserves the quality gate instead of weakening it.
Playwright must scroll a diagram into view, prove local SVG rendering in both
themes, and fail on console errors or overflow.

## Quality strategy

- Node unit tests validate the manifest, path containment, H1 extraction,
  duplicate detection, route rewriting, and broken-link reporting.
- Astro Check validates templates and collection configuration.
- A production build proves content rendering and creates the Pagefind index.
- A post-build validator checks every local HTML `href`, target file, and anchor.
- Playwright validates deep links, search terms, no-results recovery, keyboard
  navigation, theme persistence, reduced motion, no-JavaScript content, viewport
  overflow, Mermaid rendering, and metadata.
- axe scans the homepage and a representative documentation page. Manual keyboard
  coverage remains necessary because automated accessibility checks are partial.
- Lighthouse CI takes multiple runs and asserts median scores >=0.90 for
  performance, accessibility, best practices, and SEO.

## Primary sources

- [Astro content collections](https://docs.astro.build/en/guides/content-collections/)
- [Astro testing](https://docs.astro.build/en/guides/testing/)
- [Starlight configuration and content loaders](https://starlight.astro.build/reference/configuration/)
- [Starlight site search](https://starlight.astro.build/guides/site-search/)
- [Starlight i18n](https://starlight.astro.build/guides/i18n/)
- [Starlight sidebar](https://starlight.astro.build/guides/sidebar/)
- [Starlight frontmatter](https://starlight.astro.build/reference/frontmatter/)
- [Pagefind multilingual search](https://pagefind.app/docs/multilingual/)
- [astro-mermaid repository](https://github.com/joesaby/astro-mermaid)
- [Playwright accessibility testing](https://playwright.dev/docs/accessibility-testing)
- [Lighthouse CI](https://github.com/GoogleChrome/lighthouse-ci/blob/main/docs/getting-started.md)
- [Docusaurus versioning](https://docusaurus.io/docs/versioning)
- [VitePress local search](https://vitepress.dev/reference/default-theme-search)
