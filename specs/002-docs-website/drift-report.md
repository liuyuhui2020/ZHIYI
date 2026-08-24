# Design Drift Report

**Feature**: 002-docs-website
**Status**: ALIGNED
**Docs-Impact**: NONE
**Docs-Updated**: N/A
**Docs-Impact-Reason**: T043 只修正文档站点 CI 中 pnpm 的启动顺序，不改变产品行为、站点内容、本地命令或对外文档；既有 README 与 PROJECT 描述继续有效。
**Reviewed-By**: AI

## Alignment Evidence

- Requirements and success criteria checked: all 18 functional requirements,
  10 success criteria, and 16 acceptance scenarios are covered by the 43 tasks
  and their tests or explicit verification steps.
- Plan sections checked: static architecture, single-source loader, local
  Pagefind, Chinese locale boundary, responsive/accessibility behavior,
  no-deployment boundary, rollback, and all direct dependencies are aligned.
  The dependency review records purpose, upstream maintenance source, license,
  exact version, and lockfile coverage.
- Tasks and implementation paths checked: every changed `apps/docs/` source,
  test, configuration, lockfile, `.github/workflows/docs-website.yml`, and
  README visual asset path is named by a task. Final `$speckit-analyze` found
  no Critical or High issue.
- README architecture refinement: T037 replaces the wide execution pipeline
  with a 1200×1200 top-down map ordered 客户端 → 渠道 → 服务端 → Agent 核心 →
  基础设施层. The editable HTML source, generated PNG, README reference, and
  alternative text remain synchronized; the README text and image alternative
  text retain the explicit design-baseline/not-implemented context.
- README architecture simplification: T039 removes the five left-side layer
  introduction columns, expands the component-card grids to the full available
  width, and centers the downward connectors without changing component order
  or responsibility boundaries.
- README architecture copy removal: T040 removes the visible title, layer
  sequence, and design-baseline badge from the top of the image and moves all
  five component groups upward without changing the accessible description or
  the README maturity disclosure.
- README architecture contrast refinement: T041 replaces the dark canvas and
  cards with a light blue-gray technical grid, white group surfaces, pale node
  cards, and dark labels while preserving the five accent colors, component
  order, connectors, and accessible description.
- README brand refinement: T038 replaces the promotional header, badges, and
  local anchor rail with one centered ZHIYI logo lockup. Its light/dark SVGs
  use identical path geometry, contain no font or external-resource dependency,
  pass XML validation, select correctly through `prefers-color-scheme`, and
  remain legible in 16/24/32/128-pixel mark renders and at the 440-pixel README
  lockup width. Product maturity remains explicit at the start of Overview.
- README header hierarchy refinement: T042 supersedes T038's logo-only
  presentation, reduces the lockup from 440 to 220 pixels, and restores four
  core-document links, seven maturity/target-stack badges, and eight working
  section anchors. Browser checks at 1000 pixels in both themes and at 390
  pixels confirm correct logo selection, successful image loading, natural
  badge/navigation wrapping, and no horizontal overflow. Planned technologies
  are consistently labeled `target`, while the design-baseline state and the
  unimplemented Product Runtime remain explicit in the header.
- Convergence result: all 18 FRs, 10 SCs, 16 acceptance scenarios, plan
  decisions, and eight constitution principles were checked; no missing,
  partial, contradictory, or unrequested product work was found. The T043
  CI-bootstrap re-check confirms the workflow repair is fully traced and does
  not require another convergence task.
- Static and content evidence: Astro reports 0 errors, 0 warnings, and 0 hints;
  17 content-pipeline unit tests pass; the production build generates 9 pages;
  the validator passes all 8 required routes, internal targets, anchors,
  canonical URLs, sitemap, and Pagefind output.
- Browser evidence: all 31 Playwright/axe scenarios pass, including seven deep
  routes, the seven-document search matrix, Chinese and English search,
  no-results recovery, no external runtime requests, 320/768/1280/1920 px
  layouts, keyboard flow, mobile navigation, system-theme fallback when storage
  is unavailable, the 404 recovery path, both themes, reduced motion,
  JavaScript-disabled content, and local lazy Mermaid rendering.
- Quality evidence: the latest three Lighthouse runs for `/` and three for
  `/docs/architecture/` each score 1.00 for performance, accessibility, best
  practices, and SEO, above the required 0.90 medians.
- Reproducibility evidence: a clean frozen install reused the lockfile and
  completed in 6.8 seconds; dependency preparation, production build, browser
  suite, and quality workflow completed within the 10-minute target. The CI
  workflow is non-deploying and uses Node 24.
- CI bootstrap repair: the failed post-merge Documentation Website run
  reproduced a clean-runner failure in `actions/setup-node` because its pnpm
  cache lookup ran before the workflow's pinned Corepack bootstrap. T043
  removes only that premature cache lookup; dependency installation remains
  frozen and pinned to pnpm 11.22.0, and all existing documentation quality
  gates remain unchanged.
- Governance evidence: all 29 SDD checker unit tests pass; the final manual
  worktree drift gate passes. T037 through T042 reported zero product
  implementation files; the T043 re-check reports the single governed CI
  workflow file under feature `002-docs-website`.
- 30-second first-visit comprehension check (2026-08-21): PASS. In a
  first-viewport, read-only review, the reviewer identified the positioning as
  a reliable production-oriented Agent Runtime design baseline; the maturity
  as SDD governance established with Runtime unimplemented and M0 awaiting
  start; and the next actions as reading the technical architecture and project
  roadmap. The first viewport also exposes the documentation and GitHub paths.

## Intentional Differences

- `astro-mermaid` was replaced before completion by a project-owned lazy
  adapter after the representative documentation page measured a 0.87 median
  Lighthouse performance score. The Plan, Research, Tasks, dependency graph,
  fallback behavior, and tests are synchronized before implementation.

## Blocking Findings

- None.

> `ALIGNED` records the completed evidence above; it does not waive any
> constitution, architecture, content, or quality failure.
