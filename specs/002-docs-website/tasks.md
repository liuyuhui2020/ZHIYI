# Tasks: Official Documentation Website

**Input**: Design documents from `/specs/002-docs-website/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/publishing-contract.md`

**Testing rule**: Behavioral tests are written and observed failing before their
corresponding implementation. Browser tests run against the production build.

## Phase 1: Project Setup

- [x] T001 Create the isolated Astro project, exact dependency/tool scripts, and audited build allowlist in `apps/docs/package.json`, `apps/docs/tsconfig.json`, `apps/docs/pnpm-workspace.yaml`, and `apps/docs/pnpm-lock.yaml`
- [x] T002 [P] Configure static Astro, Starlight, Chinese root locale, Pagefind, Mermaid strict mode, manual sidebar, and metadata defaults in `apps/docs/astro.config.mjs` and `apps/docs/src/content.config.mjs`
- [x] T003 [P] Configure production-preview browser and Lighthouse runners in `apps/docs/playwright.config.ts` and `apps/docs/lighthouserc.json`
- [x] T004 [P] Exclude website build/test outputs without excluding source or lockfiles in `.gitignore`

## Phase 2: Single-Source Publishing Foundation (User Story 4, Priority P2)

**Goal**: Publish only the approved repository documents without maintaining a second body copy.

**Independent Test**: A source edit appears after rebuild; a missing/escaping
link, duplicate route, or missing heading fails with the original source path.

### Tests first

- [x] T005 [US4] Add failing manifest, path-containment, heading extraction, link-rewrite, duplicate, missing-target, and source-change tests in `apps/docs/tests/unit/content-pipeline.test.mjs`

### Implementation

- [x] T006 [US4] Define the seven-source allowlist, stable routes, descriptions, groups, order, and maturity status in `apps/docs/src/lib/document-manifest.mjs`
- [x] T007 [US4] Implement safe repository path resolution and Markdown link validation/rewriting in `apps/docs/src/lib/repository-links.mjs`
- [x] T008 [US4] Implement the read-only Astro loader with H1 metadata derivation, Starlight schema data, deterministic digests, exact edit URLs, and source watching in `apps/docs/src/lib/repository-docs-loader.mjs`
- [x] T009 [US4] Connect the custom loader, repository-link transformer, and viewport-lazy strict Mermaid adapter in `apps/docs/src/content.config.mjs`, `apps/docs/astro.config.mjs`, `apps/docs/src/lib/mermaid-plugin.mjs`, and `apps/docs/src/scripts/mermaid.ts`
- [x] T010 [US4] Run `pnpm --dir apps/docs test:unit` and prove all content-pipeline tests pass after the expected red state

## Phase 3: Project Homepage (User Story 1, Priority P1)

**Goal**: Let a first-time visitor understand ZHIYI, its design-stage maturity, and next actions within 30 seconds.

**Independent Test**: The homepage contains the product definition, explicit
design-baseline status, and working architecture, roadmap, docs, and GitHub links.

### Tests first

- [x] T011 [US1] Add failing homepage positioning, maturity-truthfulness, navigation, metadata, and JavaScript-disabled acceptance tests in `apps/docs/tests/e2e/site.spec.ts`

### Implementation

- [x] T012 [P] [US1] Create accessible brand primitives and a local SVG identity in `apps/docs/src/components/BrandMark.astro`, `apps/docs/src/assets/brand-mark.svg`, and `apps/docs/public/favicon.svg`
- [x] T013 [P] [US1] Create the semantic global header/footer with working project, document, and GitHub links in `apps/docs/src/components/SiteHeader.astro` and `apps/docs/src/components/SiteFooter.astro`
- [x] T014 [P] [US1] Create the topology-based architecture hero visual with non-script fallback in `apps/docs/src/components/SignalMap.astro`
- [x] T015 [US1] Build the shared canonical/OG/no-tracking layout and social preview asset in `apps/docs/src/layouts/SiteLayout.astro` and `apps/docs/public/social-card.svg`
- [x] T016 [US1] Build the technical-observatory homepage with honest status rail, value narrative, architecture sequence, roadmap, and documentation actions in `apps/docs/src/pages/index.astro`
- [x] T017 [US1] Implement the homepage visual tokens, layout, typography, focus treatment, and progressive motion in `apps/docs/src/styles/global.css`

## Phase 4: Documentation Navigation and Search (User Story 2, Priority P1)

**Goal**: Make every core document directly browsable and searchable with clear current location and reading order.

**Independent Test**: Deep-link all seven routes, move between any core pages
within two actions, and find `AgentVersion`, `幂等`, and `设计漂移` in production search.

### Tests first

- [x] T018 [US2] Extend `apps/docs/tests/e2e/site.spec.ts` with failing route, active-navigation, previous/next, heading-outline, seven-document search matrix, Chinese/English query, no-external-request, and no-results tests
- [x] T019 [US2] Add failing built HTML route, href, target-file, anchor, canonical, sitemap, and Pagefind-index validation in `apps/docs/scripts/validate-built-links.mjs`

### Implementation

- [x] T020 [US2] Finalize manual sidebar groups, source-specific edit URLs, previous/next ordering, title/description metadata, and Pagefind settings in `apps/docs/astro.config.mjs`
- [x] T021 [US2] Add documentation-specific table, code, Mermaid, long-token, status-badge, search, sidebar, and narrow-screen styles in `apps/docs/src/styles/global.css`
- [x] T022 [US2] Make `apps/docs/scripts/validate-built-links.mjs` pass against a clean `pnpm --dir apps/docs build`, including Pagefind and all internal anchors

## Phase 5: Responsive and Accessible Experience (User Story 3, Priority P2)

**Goal**: Preserve the complete homepage-to-search flow across required viewports, input modes, themes, and motion preferences.

**Independent Test**: Playwright and axe complete the flow at 320, 768, 1280,
and 1920 pixels with keyboard-only input, both themes, reduced motion, and no JS.

### Tests first

- [x] T023 [US3] Add failing axe landmark/name/focus tests for homepage and architecture page in `apps/docs/tests/e2e/accessibility.spec.ts`
- [x] T024 [US3] Extend `apps/docs/tests/e2e/site.spec.ts` with failing viewport-overflow, keyboard-search, theme-persistence, reduced-motion, Mermaid-theme, and console-error tests

### Implementation

- [x] T025 [US3] Add skip navigation, semantic main landmarks, responsive menu behavior, visible focus, contrast, reduced-motion overrides, and non-JavaScript fallbacks in `apps/docs/src/layouts/SiteLayout.astro`, `apps/docs/src/components/SiteHeader.astro`, and `apps/docs/src/styles/global.css`
- [x] T026 [US3] Add an honest, navigable not-found page with recovery actions in `apps/docs/src/pages/404.astro`
- [x] T027 [US3] Run `pnpm --dir apps/docs test:e2e` against the production preview and resolve all functional and axe findings

## Phase 6: Quality, Documentation, and CI

- [x] T028 [P] Update root project navigation and local website commands without claiming deployment in `README.md`, and add its editable visual source plus responsive hero and architecture assets in `assets/banners/zhiyi-readme/source.html`, `assets/banners/zhiyi-readme/resumable-orbit-1440x420.png`, `assets/banners/zhiyi-readme/resumable-orbit-mobile-720x360.png`, and `assets/banners/zhiyi-readme/runtime-architecture-1200x520.png`
- [x] T029 [P] Record the website source boundary, current completion state, risks, and no-deployment decision in `doc/PROJECT.md`
- [x] T030 Add a non-deploying Node 24 CI job with exact install, unit, Astro, build, link, and browser checks in `.github/workflows/docs-website.yml`
- [x] T031 Run `pnpm --dir apps/docs quality` for `/` and `/docs/architecture/`, preserve median scores >=0.90 in all four Lighthouse categories, and tune `apps/docs/lighthouserc.json` or implementation without lowering thresholds
- [x] T032 Validate the documented clean-install/preview/build workflow in `specs/002-docs-website/quickstart.md` within ten minutes
- [x] T033 Conduct and record a 30-second first-visit comprehension check for project positioning, maturity, and two next actions in `specs/002-docs-website/drift-report.md`
- [x] T034 Run `python3 -m unittest discover -s scripts/sdd/tests -v` and `python3 scripts/sdd/check_design_drift.py --worktree --gate manual`
- [x] T035 Re-run `$speckit-analyze`, resolve all critical/high findings, and confirm each changed implementation/test/document path is named in this file
- [x] T036 Run `$speckit-converge`, append and complete any missing tasks, then update evidence and set `Status: ALIGNED` in `specs/002-docs-website/drift-report.md`

## Phase 7: README Architecture Asset Refinement

**Goal**: Make the README architecture visual a square, top-down five-layer map without changing the approved platform boundaries or implying Runtime delivery.

**Independent Test**: The generated image is exactly 1200×1200 pixels, presents 客户端 → 渠道 → 服务端 → Agent 核心 → 基础设施层 in that order, and the README references the square asset with matching alternative text.

- [x] T037 [US1] Redesign and verify the README architecture visual in `assets/banners/zhiyi-readme/source.html`, replace `assets/banners/zhiyi-readme/runtime-architecture-1200x520.png` with `assets/banners/zhiyi-readme/runtime-architecture-1200x1200.png`, and update `README.md`

## Phase 8: README Brand Identity Refinement

**Goal**: Replace the README promotional header with one distinctive, theme-aware ZHIYI logo lockup that expresses recoverable signal flow without changing product claims.

**Independent Test**: The README begins with only the centered logo; light and dark variants share identical geometry, contain no font or external-resource dependency, parse as valid SVG, and remain legible when rendered at 16, 24, 32, and 128 pixels for the mark and at the README display width for the lockup.

- [x] T038 [US1] Design and verify the theme-aware ZHIYI logo lockup in `assets/brand/zhiyi-logo-light.svg` and `assets/brand/zhiyi-logo-dark.svg`, replace the promotional header at the top of `README.md`, and record alignment evidence in `specs/002-docs-website/drift-report.md`

## Phase 9: README Architecture Layout Simplification

**Goal**: Remove the left-side layer introductions from the square README architecture visual while preserving the top-down five-group structure and approved component boundaries.

**Independent Test**: The generated image remains exactly 1200×1200 pixels, contains no left-side layer label column, keeps all component cards readable, and preserves an unobstructed downward flow between the five groups.

- [x] T039 [US1] Remove the left-side layer introductions and re-render the architecture asset in `assets/banners/zhiyi-readme/source.html` and `assets/banners/zhiyi-readme/runtime-architecture-1200x1200.png`

## Phase 10: README Architecture Copy Removal

**Goal**: Remove all visible header copy from the square README architecture visual and use the released space for the five component groups.

**Independent Test**: The generated image remains exactly 1200×1200 pixels, contains no visible architecture title, layer-sequence subtitle, or design-baseline badge, and keeps all five groups fully visible without clipping.

- [x] T040 [US1] Remove the top architecture copy, move the component groups upward, and re-render `assets/banners/zhiyi-readme/source.html` and `assets/banners/zhiyi-readme/runtime-architecture-1200x1200.png`

## Phase 11: README Architecture Contrast Refinement

**Goal**: Replace the overly dark architecture palette with a high-contrast light technical palette while preserving the square layout and component hierarchy.

**Independent Test**: The generated image remains exactly 1200×1200 pixels, uses a light background with dark readable labels, keeps the five accent colors distinguishable, and has no clipped or overlapping cards or connectors.

- [x] T041 [US1] Apply a light high-contrast palette and re-render `assets/banners/zhiyi-readme/source.html` and `assets/banners/zhiyi-readme/runtime-architecture-1200x1200.png`

## Phase 12: README Header Information Hierarchy

**Goal**: Supersede T038's logo-only presentation by reducing the logo footprint and restoring a compact project header with technology/status badges and working documentation and section navigation.

**Independent Test**: The README header renders a theme-aware logo no wider than 240 pixels, exposes roadmap/architecture/requirements/development-guide links, labels planned technologies as the target stack, restores all major section anchors, and keeps the design-baseline maturity explicit without reinstating the previous long promotional block.

- [x] T042 [US1] Replace the T038 logo-only header with a smaller logo, target-stack badges, documentation links, and section navigation at the top of `README.md`, and record verification evidence in `specs/002-docs-website/drift-report.md`

## Phase 13: Documentation CI Bootstrap Repair

**Goal**: Keep the non-deploying documentation workflow reproducible on a clean GitHub-hosted runner where pnpm is unavailable before the pinned Corepack bootstrap step.

**Independent Test**: A `push` workflow run reaches dependency installation with Node 24 and pinned pnpm 11.22.0, then completes the existing check, build, browser, accessibility, and Lighthouse gates.

- [x] T043 Reproduce the failed post-merge clean-runner pnpm lookup, align `specs/002-docs-website/spec.md` and `specs/002-docs-website/plan.md`, remove the premature pnpm cache lookup from `.github/workflows/docs-website.yml`, rerun the local documentation and governance gates, and record the repair evidence in `specs/002-docs-website/drift-report.md`

## Dependencies and Execution Order

- Phase 1 establishes the toolchain and blocks all later phases.
- Phase 2 is foundational for document routes and blocks US2, but US1 visual work
  may begin after Phase 1.
- Within every story, test tasks must reach the expected failure before
  implementation tasks begin.
- US3 integrates US1 and US2 and therefore follows both.
- Phase 6 is the completion gate. Deployment remains explicitly out of scope.

## Requirement and Success-Criterion Coverage

| Coverage | Tasks |
|---|---|
| FR-001, FR-003; SC-001, SC-009 | T011-T017, T033, T037-T042 |
| FR-002, FR-004, FR-006 | T013, T016, T018, T020 |
| FR-005; SC-002, SC-003 | T018, T020-T022 |
| FR-007, FR-008, FR-009, FR-010; SC-004, SC-005, SC-006 | T023-T027, T031 |
| FR-011, FR-012; SC-007, SC-008, SC-009 | T005-T010, T019, T022, T034-T036 |
| FR-013 | T011, T015, T019-T020 |
| FR-014, FR-015 | T006, T009, T020 |
| FR-016, FR-018 | T001-T003, T011, T015, T030 |
| FR-017; SC-010 | T001-T004, T028-T032, T043 |

## Completion Definition

All tasks are checked, production build and Pagefind output are reproducible,
browser/axe/Lighthouse and governance gates pass, the drift report is aligned,
and no deploy workflow, public URL, analytics, content copy, placeholder, or
untracked implementation path exists.
