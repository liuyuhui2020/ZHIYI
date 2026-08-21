# Contract: Documentation Publishing and Site Routes

## Published source allowlist

| Source | Route | Group | Status |
|---|---|---|---|
| `doc/产品价值.md` | `/docs/product-value/` | 产品与范围 | design-target |
| `doc/需求文档.md` | `/docs/requirements/` | 产品与范围 | design-target |
| `doc/功能文档.md` | `/docs/features/` | 产品与范围 | design-target |
| `doc/技术方案.md` | `/docs/architecture/` | 架构与计划 | design-target |
| `doc/PROJECT.md` | `/docs/roadmap/` | 架构与计划 | established |
| `doc/SDD开发规范.md` | `/docs/development/sdd/` | 开发与治理 | established |
| `doc/AGENTS.md` | `/docs/development/agent-guidelines/` | 开发与治理 | established |

`/` is a website-specific homepage. It may summarize governed content but must
label ZHIYI as a design baseline and must not claim planned Runtime features are
implemented.

## Loader contract

For every manifest entry, the loader MUST:

1. Resolve `sourcePath` against the repository root and reject path escape.
2. Read UTF-8 Markdown without modifying the source file.
3. Require exactly one usable first level-one heading and use it as page title.
4. Remove only that first heading from the rendered body.
5. Inject route ID, description, sidebar label/order, status, and exact GitHub
   edit URL before Starlight schema validation.
6. Include the source path and rule in all validation errors.
7. Produce the same output for the same source and manifest inputs.

Manifest entries MUST be unique by source and route. Files not in the allowlist
MUST NOT become website routes.

## Repository link contract

- `https:`, `http:`, `mailto:`, `tel:`, hash-only, and protocol-relative links
  retain their meaning.
- A relative `.md` link to another published source becomes that source's site
  route and retains query/fragment data.
- A relative link to an existing repository file that is not published becomes
  an HTTPS GitHub `blob/main` URL.
- A relative link to a missing path fails validation with source and target.
- Decoded paths must remain within the repository; traversal outside the root
  fails validation.

## Route and navigation contract

- All internal routes use trailing-slash canonical form.
- The global homepage navigation exposes: 产品价值, 架构, 路线图, 文档, GitHub.
- Every document page shows an active sidebar entry, an on-page section outline
  when headings exist, and previous/next links according to manifest order.
- All seven published documents are at most two navigation actions from every
  documentation page.
- Chinese is the root locale; no language or version selector is rendered.

## Search contract

After a production build:

- `/pagefind/pagefind.js` and its index assets exist.
- `AgentVersion` returns architecture or feature content.
- `幂等` returns requirements, features, or architecture content.
- `设计漂移` returns SDD or Agent guideline content.
- A guaranteed-missing query displays an explicit no-results state and permits
  editing or clearing the query.
- No search query is sent to an external service or analytics endpoint.

## Accessibility and responsive contract

- A skip link reaches main content.
- Navigation, search, theme control, results, and document links are keyboard
  operable with visible focus.
- Semantic landmarks and accessible names are present.
- No page-level horizontal overflow occurs at 320, 768, 1280, or 1920 CSS pixels.
- Motion is disabled under `prefers-reduced-motion`.
- Without JavaScript, the homepage message, global links, sidebar/document text,
  and Mermaid source fallback remain readable.
- axe reports no critical or serious violations on the homepage and architecture
  document page.

## Build and quality contract

The following commands are deterministic and non-deploying:

```bash
pnpm --dir apps/docs install --frozen-lockfile
pnpm --dir apps/docs check
pnpm --dir apps/docs test
pnpm --dir apps/docs build
pnpm --dir apps/docs test:e2e
pnpm --dir apps/docs quality
```

`check` validates content and templates. `build` creates static output and the
Pagefind index. `test:e2e` runs against production preview. `quality` asserts
median Lighthouse scores >=0.90 for performance, accessibility, best practices,
and SEO on `/` and `/docs/architecture/`.

No command in this feature publishes a site, creates a cloud resource, or
requires a production secret.
