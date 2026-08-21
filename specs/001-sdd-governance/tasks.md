# Tasks: SDD Governance and Design Drift Gates

**Input**: Design documents from `/specs/001-sdd-governance/`

**Prerequisites**: `spec.md`, `plan.md`

## Phase 1: Spec Kit Foundation

- [x] T001 [US1] Initialize official Spec Kit shared assets in `.specify/`
- [x] T002 [US3] Install Codex skills in `.agents/skills/speckit-*/`
- [x] T003 [US3] Install Claude Code skills in `.claude/skills/speckit-*/`
- [x] T030 [US3] Exclude machine-local AI state while retaining shared skills in `.gitignore`
- [x] T004 [US1] Ratify project principles in `.specify/memory/constitution.md`
- [x] T005 [US1] Create feature spec and plan in `specs/001-sdd-governance/spec.md` and `specs/001-sdd-governance/plan.md`

## Phase 2: Tests First

- [x] T006 [P] [US1] Add incomplete-artifact and traceability tests in `scripts/sdd/tests/test_design_drift.py`
- [x] T007 [P] [US2] Add architecture and document-impact tests in `scripts/sdd/tests/test_design_drift.py`
- [x] T008 [P] [US3] Add Git scope and Claude adapter tests in `scripts/sdd/tests/test_design_drift.py`

## Phase 3: Shared Drift Engine

- [x] T009 [US2] Define versioned implementation/document/architecture mapping in `.specify/governance/design-map.json`
- [x] T010 [US1] Implement Git change collection and feature resolution in `scripts/sdd/check_design_drift.py`
- [x] T011 [US1] Implement artifact completeness and task path traceability in `scripts/sdd/check_design_drift.py`
- [x] T012 [US2] Implement drift report and document-impact validation in `scripts/sdd/check_design_drift.py`
- [x] T013 [US2] Implement AST import boundary enforcement in `scripts/sdd/check_design_drift.py`
- [x] T014 [US2] Add stable text/JSON findings and fail-closed errors in `scripts/sdd/check_design_drift.py`
- [x] T015 [US2] Add the review evidence template in `.specify/templates/drift-report-template.md`

## Phase 4: Enforcement Adapters

- [x] T016 [US3] Add Git adapters in `.githooks/pre-commit` and `.githooks/pre-push`
- [x] T017 [US3] Add idempotent local installer in `scripts/sdd/install_hooks.sh`
- [x] T018 [US3] Add Claude Stop adapter in `scripts/sdd/claude_stop_guard.py` and `.claude/settings.json`
- [x] T019 [US3] Add CI enforcement in `.github/workflows/sdd-governance.yml`
- [x] T020 [US3] Add AI entrypoint rules in `AGENTS.md` and `CLAUDE.md`

## Phase 5: Documentation Migration

- [x] T021 [P] [US1] Write operating procedure and failure resolution in `doc/SDD开发规范.md`
- [x] T022 [P] [US1] Replace OpenSpec workflow references in `doc/AGENTS.md` and `doc/README.md`
- [x] T023 [P] [US1] Align governance references in `doc/PROJECT.md`, `doc/需求文档.md`, and `doc/技术方案.md`
- [x] T024 [US1] Record design alignment in `specs/001-sdd-governance/drift-report.md`

## Phase 6: Verification

- [x] T025 [US1] Run `python3 -m unittest discover -s scripts/sdd/tests -v`
- [x] T026 [US3] Run shell syntax and JSON validation for all adapters/configuration
- [x] T027 [US2] Exercise passing, document-drift, and architecture-hard-block fixtures
- [x] T028 [US3] Install local Git hooks and verify `core.hooksPath=.githooks`
- [x] T029 [US1] Run Spec Kit artifact consistency review and final drift check

## Dependencies & Execution Order

- Phase 1 blocks all implementation.
- Phase 2 tests precede Phase 3 production logic.
- Phase 3 provides the single policy engine used by every Phase 4 adapter.
- Phase 5 can proceed after the policy vocabulary is stable.
- Phase 6 is the release gate; failures append tasks instead of being waived.

## Phase 7: Convergence

- [x] T031 [US2] Add Application, Runtime, and API forbidden-import coverage for SC-002 in `scripts/sdd/tests/test_design_drift.py` (partial)
- [x] T032 [US3] Add no-initial-commit, prefixed-feature-branch, and conflicting-feature-context coverage for SC-004 in `scripts/sdd/tests/test_design_drift.py` (partial)
- [x] T033 [US1] Add a 5,000-path execution benchmark for SC-007 in `scripts/sdd/tests/test_design_drift.py` (missing)
- [x] T034 [US3] Add malformed-config, non-Git, and real Claude drift-feedback tests for FR-014 and US3/AC1 in `scripts/sdd/tests/test_design_drift.py` (partial)
- [x] T035 [US1] Make the commit gate validate policy, artifacts, reports, documents, and Python source from the Git index snapshot in `scripts/sdd/check_design_drift.py` and `scripts/sdd/tests/test_design_drift.py` (contradicts)
- [x] T036 [US1] Let the commit gate accept synchronized documents already present in the current feature branch while still rejecting unstaged-only evidence in `scripts/sdd/check_design_drift.py` and `scripts/sdd/tests/test_design_drift.py` (partial)
- [x] T037 [US2] Normalize Python relative imports before applying architecture boundaries in `scripts/sdd/check_design_drift.py` and `scripts/sdd/tests/test_design_drift.py` (partial)
- [x] T038 [US1] Enforce exact task-path boundaries so similarly prefixed files cannot satisfy traceability in `scripts/sdd/check_design_drift.py` and `scripts/sdd/tests/test_design_drift.py` (partial)

## Phase 8: GitHub CI Performance Correction

- [x] T039 [US1] Normalize each path once per pattern set and skip filesystem reads for Python paths outside configured architecture rules in `scripts/sdd/check_design_drift.py`
- [ ] T040 [US1] Re-run the 29-test suite, SC-007 benchmark, Git Hooks, and GitHub Actions workflow for `scripts/sdd/check_design_drift.py` and `scripts/sdd/tests/test_design_drift.py`
- [x] T041 [US1] Add allocation-free matching for literal and terminal `/**` policy rules in `scripts/sdd/check_design_drift.py`, preserving generic glob behavior for complex rules
