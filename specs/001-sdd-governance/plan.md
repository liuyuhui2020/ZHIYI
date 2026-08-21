# Implementation Plan: SDD Governance and Design Drift Gates

**Branch**: `001-sdd-governance` | **Date**: 2026-08-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-sdd-governance/spec.md`

## Summary

Initialize GitHub Spec Kit as the repository SDD backbone, then add a
dependency-free Python drift checker shared by native Git hooks, Claude Code and
GitHub Actions. The checker validates feature artifacts and task traceability,
enforces architectural import boundaries, and verifies explicit document-impact
decisions. Root AI instructions and project documents establish Spec Kit as the
only workflow for new implementation.

## Technical Context

**Language/Version**: Python 3.9+ for governance tooling; Python 3.12 remains the application target

**Primary Dependencies**: Python standard library, Git, GitHub Spec Kit CLI 0.16.5-compatible repository assets

**Storage**: Repository files only

**Testing**: `unittest`, subprocess-based temporary Git repositories, shell syntax checks

**Target Platform**: macOS/Linux developer machines and Linux CI

**Project Type**: Repository governance tooling

**Performance Goals**: staged check under 2 seconds for 5,000 changed paths; no network I/O

**Constraints**: no application dependencies; fail closed on malformed config; work before first commit and without a remote; one shared policy engine

**Scale/Scope**: one monorepo, multiple AI coding tools, feature directories under `specs/`

## Constitution Check

- **Spec before implementation**: PASS. This spec and plan precede governance code.
- **Framework ownership**: PASS. Governance tooling has no product-framework imports.
- **Test first and traceability**: PASS. Tests and exact paths are tasks before implementation tasks.
- **Recoverability/idempotency**: PASS. Hooks are read-only and deterministic.
- **Tool/context safety**: PASS. Hook input is parsed as untrusted JSON and never executed.
- **Tenant/privacy**: N/A. The checker reads only repository paths and source text.
- **Observability/no hidden reasoning**: PASS. Findings are structured evidence, not chain-of-thought.
- **Simplicity/versioning**: PASS. Standard library and versioned JSON configuration; no pre-commit framework dependency.

Re-check after design: PASS. The detailed policy keeps AI-specific adapters thin
and maintains a single deterministic checker.

## Design

### Enforcement layers

1. **Behavioral guidance**: root `AGENTS.md`, `CLAUDE.md`, constitution and SDD guide.
2. **Fast local gate**: pre-commit checks staged changes; pre-push checks the branch delta.
3. **AI feedback**: Claude Stop Hook converts checker findings into a blocking continuation request.
4. **Non-bypassable repository gate**: CI runs the same checker against the pull-request base.
5. **Semantic review**: `$speckit-analyze` before implementation and `$speckit-converge` before completion.

### Drift decision model

- Static architecture violation: always fail.
- Missing/incomplete Spec Kit artifacts or task traceability: fail until corrected.
- `Docs-Impact: UPDATED`: listed mapped documents must exist and be in the change set.
- `Docs-Impact: NONE`: a concrete reason is required; reviewer decides whether it is credible.
- `Status: BLOCKED`: fail intentionally until design and implementation are reconciled.

### Feature resolution order

1. `--feature` CLI argument.
2. `SPECIFY_FEATURE_DIRECTORY` or `SPECIFY_FEATURE`.
3. `.specify/feature.json` maintained by Spec Kit.
4. Exactly one changed `specs/NNN-name/` directory.
5. `NNN-name` suffix of the current Git branch.

Ambiguous or absent context blocks implementation changes. Pure docs/spec changes
do not require an active implementation feature.

### Failure behavior

Findings use stable IDs (`SDD-*`, `DRIFT-*`, `ARCH-*`, `CONFIG-*`) with a path,
message and remediation. Operational/configuration failures fail closed. The
Claude adapter returns a documented Stop Hook `decision: block` response and
uses `stop_hook_active` to prevent infinite self-trigger loops; Git/CI remain the
hard fallback if a repeated Stop is allowed.

## Project Structure

### Documentation (this feature)

```text
specs/001-sdd-governance/
├── spec.md
├── plan.md
├── tasks.md
└── drift-report.md
```

### Source Code (repository root)

```text
.agents/skills/speckit-*/       # Official Codex Spec Kit integration
.claude/skills/speckit-*/      # Official Claude Code integration
.claude/settings.json          # Claude Stop Hook
.github/workflows/
└── sdd-governance.yml         # CI gate
.githooks/
├── pre-commit
└── pre-push
.specify/
├── governance/design-map.json # Versioned policy configuration
├── memory/constitution.md
├── scripts/
├── templates/
└── workflows/
scripts/sdd/
├── check_design_drift.py      # Shared policy engine
├── claude_stop_guard.py       # Thin JSON adapter
├── install_hooks.sh
└── tests/test_design_drift.py
AGENTS.md
CLAUDE.md
doc/SDD开发规范.md
```

**Structure Decision**: Keep official Spec Kit managed assets untouched and put
project extensions in separate governance/script paths. This preserves official
upgrade compatibility and makes every tool call the same checker.

## Complexity Tracking

No constitution exceptions. Native Git hooks are used instead of a hook framework
because the policy engine already provides cross-platform Python behavior and
the repository does not yet have an application dependency environment.
