# ZHIYI Repository Agent Instructions

These rules apply to every AI coding tool and human contributor in this
repository.

## Mandatory reading

Before changing the repository, read:

1. `.specify/memory/constitution.md`
2. `doc/AGENTS.md`
3. `doc/PROJECT.md`
4. The active `specs/NNN-feature-name/{spec.md,plan.md,tasks.md,drift-report.md}`

If these sources conflict, stop implementation and resolve the conflict using
the precedence in the constitution. Existing code is never the highest source
of truth.

## Spec Kit SDD is mandatory

No product implementation may start from a chat request alone. Use the
repository Spec Kit skills in this order:

1. `$speckit-constitution` when principles change.
2. `$speckit-specify`.
3. `$speckit-clarify` when material ambiguity remains.
4. `$speckit-plan`.
5. `$speckit-checklist` for high-risk work.
6. `$speckit-tasks`, with exact implementation and test paths.
7. `$speckit-analyze`; resolve critical findings.
8. `$speckit-implement`, following tasks and test-first order.
9. Run repository tests and the drift checker.
10. `$speckit-converge`; finish appended tasks before claiming completion.

The project remains in product-design phase. Governance scaffolding is approved;
M0 product code still requires explicit approval recorded in the active spec.

## Design drift rule

- Constitution or architecture violations are hard blocks. A document edit or
  drift report cannot waive them.
- Intentional changes to behavior, API, data, permissions, state machines,
  security, side effects, or architecture require Spec/Plan/Tasks and affected
  product documents to be updated before implementation continues.
- Implementation details within an approved plan may use
  `Docs-Impact: NONE`, but the drift report must give a concrete reason and
  every implementation file must appear in `tasks.md`.
- On a drift failure, either synchronize the design artifacts and rerun
  `$speckit-analyze`, or revert the implementation. Do not bypass the check.

Run locally:

```bash
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
python3 -m unittest discover -s scripts/sdd/tests -v
```

Git hooks and CI are authoritative even when an AI tool has no native lifecycle
Hook. Do not use `--no-verify`, change `core.hooksPath`, weaken CI, edit the
design map, or mark a report `ALIGNED` merely to suppress a failure unless the
user explicitly authorizes a governance change through Spec Kit.

Detailed engineering, architecture, testing, security, and completion rules are
in `doc/AGENTS.md`. The operating procedure is in `doc/SDD开发规范.md`.
