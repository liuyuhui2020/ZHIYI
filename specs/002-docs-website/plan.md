# Implementation Plan: Official Documentation Website

**Branch**: `codex/002-docs-website` | **Date**: 2026-08-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-docs-website/spec.md`

## Summary

Build a static Chinese official website and documentation portal with Astro
7.2.4 and Starlight 0.41.7. A custom, read-only Astro content loader publishes
an explicit allowlist of the repository's `doc/*.md` files, derives required
metadata from their existing headings, and keeps those files as the only
long-lived documentation source. The site adds a distinctive custom homepage,
manual documentation navigation, Pagefind search, responsive light/dark themes,
Mermaid rendering, and automated content, link, accessibility, browser, and
Lighthouse checks. This feature does not deploy the site or create a domain.

## Technical Context

**Language/Version**: Node.js 24 LTS recommended; Node.js 22.13+ supported; TypeScript/Astro components and ECMAScript modules

**Primary Dependencies**: Astro 7.2.4, Starlight 0.41.7, `@astrojs/markdown-satteri` 0.3.7, Pagefind bundled by Starlight, Mermaid 11.17.0

**Storage**: Repository Markdown and generated static files only; no database, browser analytics, cookies, or server state

**Testing**: Node test runner, Astro Check, Playwright 1.62.1, axe-core 4.13.0, Lighthouse CI 0.15.1, repository SDD checks

**Target Platform**: Static HTML/CSS/JS on modern evergreen browsers; macOS/Linux development and Linux CI

**Project Type**: Static website and documentation portal under `apps/docs/`

**Performance Goals**: Lighthouse performance, accessibility, best-practices, and SEO scores at least 90 on the homepage and representative documentation page; no layout overflow at required viewports

**Constraints**: no deployment, SSR, runtime backend, remote search service, remote font, analytics, authentication, or committed documentation copy; core content must survive disabled enhancement JavaScript

**Scale/Scope**: one custom homepage, seven allowlisted source documents, three navigation groups, Chinese root locale, future locale/version boundaries without empty selectors

## Constitution Check

- **Specification before implementation**: PASS. `spec.md`, this plan, design artifacts, and tasks precede website code.
- **Product semantics own the framework**: PASS. Starlight is a presentation adapter; project status and product semantics remain in governed repository documents.
- **Test first and traceability**: PASS. Loader/link tests and browser acceptance tests are scheduled before their corresponding implementations, with exact paths in `tasks.md`.
- **Recoverable/idempotent execution**: N/A for the product Runtime. The documentation build is deterministic and idempotent and writes only generated output.
- **Tools and context untrusted by default**: PASS. The loader reads an explicit allowlist, rejects path escape and duplicate routes, sanitizes Mermaid with strict security mode, and never executes Markdown as arbitrary code.
- **Tenant/privacy/least privilege**: PASS. The static site has no accounts, tenant data, secrets, analytics, or visitor data collection.
- **Observable without hidden reasoning**: PASS. Build and validation failures identify source path, route, and rule; no hidden reasoning is exposed.
- **Simple, versioned, reversible change**: PASS. Every direct dependency has
  an explicit purpose, active upstream owner, accepted license, exact version,
  and lockfile entry recorded in `research.md`; no SSR adapter or remote service
  is introduced. Removing `apps/docs/` and its CI workflow fully rolls back the
  feature without changing source documents.

Post-design re-check: PASS. Contracts preserve a single documentation source,
manual navigation avoids dependence on Starlight's internal filesystem layout,
and all community integration risks have explicit fallback behavior and tests.

## Architecture and Data Flow

```text
doc/*.md (governed source, read-only)
        |
        v
explicit publishing manifest
        |
        +--> source/path/title/slug/link validation
        |
        v
Astro content loader + Starlight docs schema
        |
        +--> repository-relative link rewriting
        +--> Mermaid strict-mode transformation
        |
        v
static HTML/CSS/JS + Pagefind index
        |
        +--> built-link validation
        +--> Playwright/axe acceptance
        +--> Lighthouse thresholds
```

### Content adapter

The `docs` collection uses a project-owned loader instead of Starlight's fixed
`src/content/docs/` loader. The loader:

1. Reads only paths listed in the publishing manifest.
2. Confirms each path is inside the repository and is a readable Markdown file.
3. Derives the page title from the first level-one heading and removes that
   heading from the rendered body to avoid duplicate page titles.
4. Injects stable ASCII IDs, descriptions, navigation metadata, content status,
   and a source-specific GitHub edit URL before validating with `docsSchema()`.
5. Rewrites published relative Markdown links to stable site routes and valid
   unpublished repository links to their GitHub source URL.
6. Fails on missing targets, duplicate IDs, duplicate sources, missing titles,
   or unknown allowlisted files and watches source files during local preview.

No generated Markdown is committed. The source manifest contains routing and
presentation metadata only, never a duplicate body.

### Navigation and locale boundary

The homepage is `/`. Documentation routes use stable unversioned paths under
`/docs/`. Starlight receives an explicit sidebar so routing does not depend on
Chinese filenames or external source paths. The current location is conveyed by
the page title, active sidebar entry (`aria-current`), and on-page table of
contents. Chinese is configured as the root locale (`zh-CN`); no language or
version selector is shown until content exists.

### Search and JavaScript fallback

Starlight's built-in Pagefind indexes the production static output and remains
entirely local to the site. Search is tested against Chinese and English terms
after `astro build`, not against the development server. Documentation text and
all standard navigation are server-rendered HTML. If JavaScript is disabled,
search, theme switching, homepage motion, and Mermaid SVG enhancement may be
unavailable, but source text and navigation remain usable.

Mermaid fences are transformed by a project-owned Sätteri plugin into escaped,
readable `<pre>` fallbacks. A small local client adapter imports Mermaid only
when a diagram intersects the viewport, renders with `securityLevel: 'strict'`,
and re-renders visible diagrams after a theme change. This keeps the full
Mermaid parser off the initial critical path of long documentation pages.

### Visual system

The design direction is a **technical observatory** rather than a generic SaaS
dashboard: ink-black and warm-white surfaces, one electric-cyan signal color,
editorial type scale, thin topology lines, and strong whitespace. The homepage
uses a full-width signal-map hero, an evidence/status rail, an architecture
sequence, and direct documentation calls to action. Motion is limited to signal
routing, staged section reveal, and navigation feedback, all disabled by
`prefers-reduced-motion`. No remote fonts, gradient-heavy surfaces, fake product
screenshots, customer logos, or capability claims are used.

### SEO and exposure boundary

All pages provide unique titles, descriptions, canonical metadata based on
`SITE_URL`, Open Graph summaries, a favicon, and sitemap-compatible routes.
Without a deployment-specific `SITE_URL`, local canonical URLs use
`http://localhost:4321`. This feature does not add a deployment workflow or
public domain, and does not claim that the private design-stage repository is a
released product.

## Failure Handling and Rollback

- Content errors fail the build with the original source path and invalid link,
  route, or heading.
- A Mermaid rendering failure leaves readable source code and fails browser
  acceptance if it causes console errors or overflow.
- Pagefind is validated only in production preview; missing index output blocks
  completion.
- Lighthouse uses multiple samples and median assertions to reduce false
  failures while keeping all four categories at 90 or higher.
- A Starlight 0.x upgrade is a separate dependency change with full regression
  checks; versions are exact in `package.json` and `pnpm-lock.yaml`.
- Rollback deletes `apps/docs/` and `.github/workflows/docs-website.yml` and
  reverts only the README/PROJECT website instructions. Governed source document
  bodies remain untouched.

## Project Structure

### Documentation (this feature)

```text
specs/002-docs-website/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/publishing-contract.md
├── checklists/requirements.md
├── tasks.md
└── drift-report.md
```

### Source Code (repository root)

```text
apps/docs/
├── package.json
├── pnpm-lock.yaml
├── astro.config.mjs
├── tsconfig.json
├── playwright.config.ts
├── lighthouserc.json
├── public/{favicon.svg,social-card.svg}
├── src/
│   ├── content.config.mjs
│   ├── assets/brand-mark.svg
│   ├── components/{BrandMark,SignalMap,SiteFooter,SiteHeader}.astro
│   ├── layouts/SiteLayout.astro
│   ├── lib/{document-manifest,mermaid-plugin,repository-docs-loader,repository-links}.mjs
│   ├── pages/{404,index}.astro
│   ├── scripts/mermaid.ts
│   └── styles/global.css
├── scripts/validate-built-links.mjs
└── tests/
    ├── unit/content-pipeline.test.mjs
    └── e2e/{accessibility,site}.spec.ts
.github/workflows/docs-website.yml
README.md
assets/banners/zhiyi-readme/{source.html,resumable-orbit-1440x420.png,resumable-orbit-mobile-720x360.png,runtime-architecture-1200x520.png}
doc/PROJECT.md
```

**Structure Decision**: Isolate the static site and its dependency graph under
the already governed `apps/docs/` implementation boundary; preserve `doc/` as
the content boundary; keep project-owned adapter
logic small and testable; use Starlight's default accessible documentation
components instead of replacing its complete layout.

## Complexity Tracking

No constitution exception is required. The custom loader is justified because
Starlight's official loader is fixed to `src/content/docs/` and requires title
frontmatter, while copying or symlinking governed documentation would weaken
single-source guarantees. Manual sidebar configuration also avoids relying on
Starlight's private assumption that file paths live inside its default content
directory.
