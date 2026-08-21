from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import check_design_drift as drift  # noqa: E402


class DriftGuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        config_source = REPOSITORY_ROOT / ".specify" / "governance" / "design-map.json"
        config_target = self.root / ".specify" / "governance" / "design-map.json"
        config_target.parent.mkdir(parents=True)
        config_target.write_text(
            config_source.read_text(encoding="utf-8"), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, relative_path: str, content: str) -> None:
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def create_complete_feature(
        self,
        *,
        implementation_paths: tuple[str, ...] = ("src/zhiyi/domain/model.py",),
        docs_impact: str = "NONE",
        docs_updated: str = "N/A",
        reason: str = "The implementation follows the approved design without semantic change.",
    ) -> Path:
        feature = self.root / "specs" / "001-test-feature"
        feature.mkdir(parents=True)
        self.write(
            "specs/001-test-feature/spec.md",
            "# Feature Specification: Test\n\n"
            "## Requirements\n\n- **FR-001**: The system MUST be testable.\n\n"
            "## Success Criteria\n\n- **SC-001**: The check is deterministic.\n",
        )
        self.write(
            "specs/001-test-feature/plan.md",
            "# Implementation Plan: Test\n\n"
            "## Constitution Check\n\nAll gates pass.\n\n"
            "## Design\n\nUse deterministic repository checks.\n",
        )
        tasks = "\n".join(
            f"- [ ] T{index:03d} [US1] Implement {path}"
            for index, path in enumerate(implementation_paths, start=1)
        )
        self.write(
            "specs/001-test-feature/tasks.md",
            f"# Tasks: Test\n\n{tasks}\n",
        )
        self.write(
            "specs/001-test-feature/drift-report.md",
            "# Design Drift Report\n\n"
            "**Feature**: 001-test-feature\n"
            "**Status**: ALIGNED\n"
            f"**Docs-Impact**: {docs_impact}\n"
            f"**Docs-Updated**: {docs_updated}\n"
            f"**Docs-Impact-Reason**: {reason}\n"
            "**Reviewed-By**: AI\n",
        )
        self.write(
            ".specify/feature.json",
            json.dumps({"feature_directory": "specs/001-test-feature"}),
        )
        return feature

    def run_guard(self, changed_files: set[str]) -> drift.GuardResult:
        return drift.evaluate_repository(
            root=self.root,
            changed_files=changed_files,
            gate="manual",
        )

    def test_docs_only_change_does_not_require_active_feature(self) -> None:
        self.write("doc/README.md", "# Documentation\n")

        result = self.run_guard({"doc/README.md"})

        self.assertTrue(result.passed, result.findings)

    def test_implementation_without_feature_is_blocked(self) -> None:
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")

        result = self.run_guard({"src/zhiyi/domain/model.py"})

        self.assertFinding(result, "SDD-001")

    def test_missing_required_artifact_is_blocked(self) -> None:
        feature = self.create_complete_feature()
        (feature / "plan.md").unlink()
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")

        result = self.run_guard({"src/zhiyi/domain/model.py"})

        self.assertFinding(result, "SDD-003")

    def test_untraced_implementation_path_is_blocked(self) -> None:
        self.create_complete_feature(
            implementation_paths=("src/zhiyi/domain/model.py",)
        )
        self.write("src/zhiyi/domain/other.py", "class Tool:\n    pass\n")

        result = self.run_guard({"src/zhiyi/domain/other.py"})

        self.assertFinding(result, "SDD-006")

    def test_similarly_prefixed_task_path_does_not_satisfy_traceability(self) -> None:
        path = "src/zhiyi/domain/model.py"
        self.create_complete_feature(implementation_paths=(path + ".bak",))
        self.write(path, "class Run:\n    pass\n")

        result = self.run_guard({path})

        self.assertFinding(result, "SDD-006")

    def test_aligned_implementation_with_none_reason_passes(self) -> None:
        self.create_complete_feature()
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")

        result = self.run_guard({"src/zhiyi/domain/model.py"})

        self.assertTrue(result.passed, result.findings)

    def test_docs_updated_requires_document_in_change_set(self) -> None:
        self.create_complete_feature(
            docs_impact="UPDATED",
            docs_updated="doc/技术方案.md",
            reason="The domain contract changes the approved technical design.",
        )
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")
        self.write("doc/技术方案.md", "# Updated design\n")

        result = self.run_guard({"src/zhiyi/domain/model.py"})

        self.assertFinding(result, "DRIFT-004")

    def test_docs_updated_passes_when_mapped_document_changed(self) -> None:
        self.create_complete_feature(
            docs_impact="UPDATED",
            docs_updated="doc/技术方案.md",
            reason="The domain contract changes the approved technical design.",
        )
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")
        self.write("doc/技术方案.md", "# Updated design\n")

        result = self.run_guard({"src/zhiyi/domain/model.py", "doc/技术方案.md"})

        self.assertTrue(result.passed, result.findings)

    def test_none_impact_requires_concrete_reason(self) -> None:
        self.create_complete_feature(reason="No change.")
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")

        result = self.run_guard({"src/zhiyi/domain/model.py"})

        self.assertFinding(result, "DRIFT-006")

    def test_forbidden_domain_import_is_hard_blocked(self) -> None:
        path = "src/zhiyi/domain/model.py"
        self.create_complete_feature(implementation_paths=(path,))
        self.write(path, "from langchain_core.messages import AIMessage\n")

        result = self.run_guard({path})

        self.assertFinding(result, "ARCH-001")

    def test_syntax_error_is_fail_closed(self) -> None:
        path = "src/zhiyi/domain/model.py"
        self.create_complete_feature(implementation_paths=(path,))
        self.write(path, "def broken(:\n")

        result = self.run_guard({path})

        self.assertFinding(result, "ARCH-000")

    def test_relative_outer_layer_import_is_hard_blocked(self) -> None:
        path = "src/zhiyi/domain/model.py"
        self.create_complete_feature(implementation_paths=(path,))
        self.write(path, "from ..runtime import execute\n")

        result = self.run_guard({path})

        self.assertFinding(result, "ARCH-001")

    def test_all_configured_layers_enforce_forbidden_imports(self) -> None:
        cases = {
            "src/zhiyi/application/service.py": (
                "import sqlalchemy\n",
                "ARCH-002",
            ),
            "src/zhiyi/runtime/graph.py": (
                "from openai import AsyncOpenAI\n",
                "ARCH-003",
            ),
            "src/zhiyi/api/routes.py": (
                "from zhiyi.adapters.persistence import repository\n",
                "ARCH-004",
            ),
        }
        self.create_complete_feature(implementation_paths=tuple(cases))
        for path, (source, _) in cases.items():
            self.write(path, source)

        result = self.run_guard(set(cases))
        finding_codes = {finding.code for finding in result.findings}

        self.assertFalse(result.passed)
        for _, expected_code in cases.values():
            self.assertIn(expected_code, finding_codes)

    def test_conflicting_feature_context_is_blocked(self) -> None:
        self.create_complete_feature()
        self.write(
            "specs/002-other-feature/spec.md",
            "# Feature Specification: Other\n",
        )
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")

        result = self.run_guard(
            {
                "src/zhiyi/domain/model.py",
                "specs/002-other-feature/spec.md",
            }
        )

        self.assertFinding(result, "SDD-002")

    def test_malformed_policy_is_blocked_fail_closed(self) -> None:
        self.write(".specify/governance/design-map.json", "{not-json")

        result = self.run_guard({"doc/README.md"})

        self.assertFinding(result, "CONFIG-001")

    def test_policy_cannot_remove_mandatory_governance(self) -> None:
        policy_path = self.root / ".specify" / "governance" / "design-map.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["required_artifacts"].remove("drift-report.md")
        policy_path.write_text(json.dumps(policy), encoding="utf-8")

        result = self.run_guard({"doc/README.md"})

        self.assertFinding(result, "CONFIG-001")

    def test_five_thousand_path_guard_completes_under_two_seconds(self) -> None:
        paths = tuple(f"src/generated/file_{index:04d}.py" for index in range(5_000))
        self.create_complete_feature(implementation_paths=paths)

        started = time.perf_counter()
        result = self.run_guard(set(paths))
        elapsed = time.perf_counter() - started

        self.assertTrue(result.passed, result.findings)
        self.assertLess(elapsed, 2.0, f"5,000-path guard took {elapsed:.3f}s")

    def assertFinding(self, result: drift.GuardResult, code: str) -> None:
        self.assertFalse(result.passed)
        self.assertIn(code, {finding.code for finding in result.findings})


class GitScopeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.run_git("init")
        self.run_git("config", "user.email", "sdd-test@example.invalid")
        self.run_git("config", "user.name", "SDD Test")
        self.write("README.md", "baseline\n")
        self.run_git("add", "README.md")
        self.run_git("commit", "-m", "baseline")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, relative_path: str, content: str) -> None:
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def run_git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("git", *arguments),
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )

    def commit_sdd_fixture(self) -> None:
        config_source = REPOSITORY_ROOT / ".specify" / "governance" / "design-map.json"
        self.write(
            ".specify/governance/design-map.json",
            config_source.read_text(encoding="utf-8"),
        )
        self.write(
            "specs/001-test-feature/spec.md",
            "# Feature Specification: Test\n\n"
            "## Requirements\n\n- **FR-001**: The system MUST validate staged code.\n\n"
            "## Success Criteria\n\n- **SC-001**: Staged drift is blocked.\n",
        )
        self.write(
            "specs/001-test-feature/plan.md",
            "# Implementation Plan: Test\n\n## Constitution Check\n\nAll gates pass.\n",
        )
        self.write(
            "specs/001-test-feature/tasks.md",
            "# Tasks: Test\n\n- [ ] T001 [US1] Implement src/zhiyi/domain/model.py\n",
        )
        self.write(
            "specs/001-test-feature/drift-report.md",
            "# Design Drift Report\n\n"
            "**Feature**: 001-test-feature\n"
            "**Status**: ALIGNED\n"
            "**Docs-Impact**: NONE\n"
            "**Docs-Updated**: N/A\n"
            "**Docs-Impact-Reason**: The implementation follows the approved design exactly.\n"
            "**Reviewed-By**: AI\n",
        )
        self.write("src/zhiyi/domain/model.py", "class Run:\n    pass\n")
        self.run_git("add", ".")
        self.run_git("commit", "-m", "add sdd fixture")

    def test_worktree_scope_includes_untracked_files(self) -> None:
        self.write("src/new.py", "value = 1\n")

        changed = drift.collect_changed_files(self.root, scope="worktree")

        self.assertIn("src/new.py", changed)

    def test_staged_scope_excludes_unstaged_files(self) -> None:
        self.write("staged.py", "value = 1\n")
        self.write("unstaged.py", "value = 2\n")
        self.run_git("add", "staged.py")

        changed = drift.collect_changed_files(self.root, scope="staged")

        self.assertIn("staged.py", changed)
        self.assertNotIn("unstaged.py", changed)

    def test_missing_base_ref_falls_back_without_error(self) -> None:
        self.write("new.py", "value = 1\n")
        self.run_git("add", "new.py")

        changed = drift.collect_changed_files(
            self.root,
            scope="base",
            base_ref="origin/main",
        )

        self.assertIn("new.py", changed)

    def test_base_scope_works_before_initial_commit(self) -> None:
        with tempfile.TemporaryDirectory() as empty_repository:
            root = Path(empty_repository)
            subprocess.run(
                ("git", "init"),
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            (root / "first.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(
                ("git", "add", "first.py"),
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            changed = drift.collect_changed_files(
                root,
                scope="base",
                base_ref="origin/main",
            )

        self.assertIn("first.py", changed)

    def test_prefixed_feature_branch_is_resolved(self) -> None:
        self.run_git("checkout", "-b", "codex/001-test-feature")

        feature, findings = drift.resolve_feature(
            self.root,
            {"src/zhiyi/domain/model.py"},
        )

        self.assertEqual([], findings)
        self.assertIsNotNone(feature)
        self.assertEqual("001-test-feature", feature.name)

    def test_non_git_scope_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as non_repository:
            with self.assertRaises(drift.GuardOperationalError):
                drift.collect_changed_files(
                    Path(non_repository),
                    scope="worktree",
                )

    def test_commit_gate_reads_staged_source_and_policy(self) -> None:
        self.commit_sdd_fixture()
        path = "src/zhiyi/domain/model.py"
        self.write(path, "from langchain_core.messages import AIMessage\n")
        self.run_git("add", path)
        self.write(path, "class Run:\n    pass\n")
        self.write(".specify/governance/design-map.json", "{not-json")

        changed = drift.collect_changed_files(self.root, scope="staged")
        result = drift.evaluate_repository(
            root=self.root,
            changed_files=changed,
            gate="commit",
            explicit_feature="001-test-feature",
        )

        self.assertFalse(result.passed)
        self.assertIn("ARCH-001", {finding.code for finding in result.findings})
        self.assertNotIn("CONFIG-001", {finding.code for finding in result.findings})

    def test_commit_gate_reads_staged_drift_report(self) -> None:
        self.commit_sdd_fixture()
        report_path = "specs/001-test-feature/drift-report.md"
        blocked_report = (
            "# Design Drift Report\n\n"
            "**Feature**: 001-test-feature\n"
            "**Status**: BLOCKED\n"
            "**Docs-Impact**: NONE\n"
            "**Docs-Updated**: N/A\n"
            "**Docs-Impact-Reason**: The implementation currently contradicts the approved design.\n"
            "**Reviewed-By**: AI\n"
        )
        self.write(report_path, blocked_report)
        self.write("src/zhiyi/domain/model.py", "class Run:\n    version = 2\n")
        self.run_git("add", report_path, "src/zhiyi/domain/model.py")
        self.write(
            report_path,
            blocked_report.replace("**Status**: BLOCKED", "**Status**: ALIGNED"),
        )

        changed = drift.collect_changed_files(self.root, scope="staged")
        result = drift.evaluate_repository(
            root=self.root,
            changed_files=changed,
            gate="commit",
            explicit_feature="001-test-feature",
        )

        self.assertFalse(result.passed)
        self.assertIn("DRIFT-003", {finding.code for finding in result.findings})

    def test_commit_gate_accepts_document_from_feature_branch_delta(self) -> None:
        self.commit_sdd_fixture()
        self.run_git("tag", "feature-base")
        self.write("doc/技术方案.md", "# Updated technical design\n")
        self.write(
            "specs/001-test-feature/drift-report.md",
            "# Design Drift Report\n\n"
            "**Feature**: 001-test-feature\n"
            "**Status**: ALIGNED\n"
            "**Docs-Impact**: UPDATED\n"
            "**Docs-Updated**: doc/技术方案.md\n"
            "**Docs-Impact-Reason**: The feature intentionally updates the approved domain design.\n"
            "**Reviewed-By**: AI\n",
        )
        self.run_git(
            "add",
            "doc/技术方案.md",
            "specs/001-test-feature/drift-report.md",
        )
        self.run_git("commit", "-m", "synchronize design")
        self.write("src/zhiyi/domain/model.py", "class Run:\n    version = 2\n")
        self.run_git("add", "src/zhiyi/domain/model.py")

        changed = drift.collect_changed_files(self.root, scope="staged")
        with mock.patch.dict(os.environ, {"SDD_BASE_REF": "feature-base"}):
            result = drift.evaluate_repository(
                root=self.root,
                changed_files=changed,
                gate="commit",
                explicit_feature="001-test-feature",
            )

        self.assertTrue(result.passed, result.findings)


class ClaudeStopAdapterTestCase(unittest.TestCase):
    def test_recursive_stop_hook_is_allowed_to_terminate(self) -> None:
        command = subprocess.run(
            (sys.executable, str(SCRIPT_DIR / "claude_stop_guard.py")),
            input=json.dumps({"stop_hook_active": True}),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPOSITORY_ROOT)},
        )

        self.assertEqual(0, command.returncode)
        self.assertEqual("", command.stdout)

    def test_checker_failure_blocks_stop_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as empty_project:
            command = subprocess.run(
                (sys.executable, str(SCRIPT_DIR / "claude_stop_guard.py")),
                input=json.dumps({"stop_hook_active": False}),
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "CLAUDE_PROJECT_DIR": empty_project},
            )

        response = json.loads(command.stdout)
        self.assertEqual(0, command.returncode)
        self.assertEqual("block", response["decision"])
        self.assertIn("fail-closed", response["reason"])

    def test_real_design_drift_is_returned_to_claude(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            root = Path(project)
            (root / "scripts" / "sdd").mkdir(parents=True)
            (root / ".specify" / "governance").mkdir(parents=True)
            (root / "src" / "zhiyi" / "domain").mkdir(parents=True)
            shutil.copy2(
                SCRIPT_DIR / "check_design_drift.py",
                root / "scripts" / "sdd" / "check_design_drift.py",
            )
            shutil.copy2(
                REPOSITORY_ROOT / ".specify" / "governance" / "design-map.json",
                root / ".specify" / "governance" / "design-map.json",
            )
            (root / "src" / "zhiyi" / "domain" / "bad.py").write_text(
                "from langchain_core.messages import AIMessage\n",
                encoding="utf-8",
            )
            subprocess.run(
                ("git", "init"),
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            command = subprocess.run(
                (sys.executable, str(SCRIPT_DIR / "claude_stop_guard.py")),
                input=json.dumps({"stop_hook_active": False}),
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)},
            )

        response = json.loads(command.stdout)
        self.assertEqual(0, command.returncode)
        self.assertEqual("block", response["decision"])
        self.assertIn("ARCH-001", response["reason"])


if __name__ == "__main__":
    unittest.main()
