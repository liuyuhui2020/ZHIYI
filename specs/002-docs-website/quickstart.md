# Quickstart: Official Documentation Website

## Prerequisites

- Node.js 24 LTS recommended; 22.13 or newer supported.
- Corepack enabled.
- Git checkout at the repository root.

## Install

```bash
corepack enable
corepack install --global pnpm@11.22.0
pnpm --dir apps/docs install --frozen-lockfile
pnpm --dir apps/docs exec playwright install chromium
```

The install is local to `apps/docs/`. It does not deploy or publish anything.

## Preview while editing

```bash
pnpm --dir apps/docs dev
```

Open `http://localhost:4321`. The content loader watches the allowlisted
`doc/*.md` sources. Search is only complete in a production build.

## Production preview

```bash
pnpm --dir apps/docs build
pnpm --dir apps/docs preview
```

Open `http://localhost:4321` unless Astro reports another free port.

## Required verification

```bash
pnpm --dir apps/docs check
pnpm --dir apps/docs test
pnpm --dir apps/docs build
pnpm --dir apps/docs test:e2e
pnpm --dir apps/docs quality
python3 -m unittest discover -s scripts/sdd/tests -v
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
```

`SITE_URL` may be set to a real HTTPS origin only in a separately approved
deployment feature. This feature intentionally has no deploy command.

## Add or remove a published document

1. Update the governed source document first.
2. Change the explicit manifest in `apps/docs/src/lib/document-manifest.mjs`.
3. Update the published allowlist contract and affected Spec Kit artifacts.
4. Add route, search, link, and navigation tests.
5. Run the full verification sequence.

Do not copy Markdown into `apps/docs/`, create a symlink, or expose the whole
repository through a glob.
