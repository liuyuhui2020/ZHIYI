#!/usr/bin/env python3
"""Deterministic Spec Kit SDD and design-drift gate.

The checker is intentionally dependency-free so it can run before the product
environment exists. AI-tool hooks are thin adapters around this module; Git and
CI remain the authoritative enforcement points.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

CONFIG_PATH = Path(".specify/governance/design-map.json")
FEATURE_NAME_PATTERN = re.compile(r"^\d{3,}-[a-z0-9][a-z0-9-]*$")
FEATURE_IN_PATH_PATTERN = re.compile(r"^specs/(\d{3,}-[a-z0-9][a-z0-9-]*)/")
FIELD_PATTERN = re.compile(r"^\s*\*{0,2}([A-Za-z][A-Za-z0-9-]*)\*{0,2}\s*:\s*(.*?)\s*$")
TASK_PATTERN = re.compile(r"(?m)^\s*-\s*\[[ xX]\]\s+T\d{3,}\b")
MIN_REASON_LENGTH = 20
MANDATORY_ARTIFACTS = {"spec.md", "plan.md", "tasks.md", "drift-report.md"}
PROTECTED_IMPLEMENTATION_PATTERNS = (
    ".specify/governance/design-map.json",
    ".specify/memory/constitution.md",
    ".specify/templates/drift-report-template.md",
    "scripts/sdd/**",
    ".githooks/**",
    ".claude/settings.json",
    ".github/workflows/sdd-governance.yml",
)
IMPLEMENTATION_SENTINELS = (
    "src/zhiyi/domain/model.py",
    "src/zhiyi/application/service.py",
    "src/zhiyi/runtime/graph.py",
    "src/zhiyi/api/routes.py",
    "tests/unit/test_example.py",
    "frontend/src/app.tsx",
    "migrations/001_example.py",
    "infra/main.tf",
    "pyproject.toml",
)
MANDATORY_ARCHITECTURE_IMPORTS = {
    "ARCH-001": {
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "langchain*",
        "langgraph*",
        "langfuse*",
        "openai",
        "anthropic",
        "zhiyi.application",
        "zhiyi.runtime",
        "zhiyi.adapters",
        "zhiyi.api",
        "zhiyi.infrastructure",
    },
    "ARCH-002": {
        "fastapi",
        "sqlalchemy",
        "langchain*",
        "langgraph*",
        "langfuse*",
        "openai",
        "anthropic",
        "zhiyi.runtime",
        "zhiyi.adapters",
        "zhiyi.api",
        "zhiyi.infrastructure",
    },
    "ARCH-003": {
        "fastapi",
        "sqlalchemy",
        "langfuse*",
        "openai",
        "anthropic",
        "zhiyi.adapters",
        "zhiyi.api",
        "zhiyi.infrastructure",
    },
    "ARCH-004": {
        "sqlalchemy",
        "langchain*",
        "langgraph*",
        "openai",
        "anthropic",
        "zhiyi.adapters",
        "zhiyi.runtime",
        "zhiyi.infrastructure",
    },
}


class GuardOperationalError(RuntimeError):
    """Raised when the checker cannot safely determine repository state."""


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    path: str = ""
    remediation: str = ""
    severity: str = "error"


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    gate: str
    feature: Optional[str]
    changed_files: Tuple[str, ...]
    implementation_files: Tuple[str, ...]
    findings: Tuple[Finding, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "gate": self.gate,
            "feature": self.feature,
            "changed_files": list(self.changed_files),
            "implementation_files": list(self.implementation_files),
            "findings": [asdict(finding) for finding in self.findings],
        }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    candidate = PurePosixPath(normalized)
    if not normalized or candidate.is_absolute() or ".." in candidate.parts:
        raise GuardOperationalError(f"Unsafe repository-relative path: {path!r}")
    return candidate.as_posix()


def path_matches(path: str, pattern: str) -> bool:
    path = normalize_relative_path(path)
    pattern = pattern.replace("\\", "/")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return PurePosixPath(path).match(pattern) or fnmatch.fnmatchcase(path, pattern)


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)


def _validate_policy(policy: Any) -> Dict[str, Any]:
    if not isinstance(policy, dict):
        raise GuardOperationalError("Design map root must be a JSON object")
    if policy.get("schema_version") != 1:
        raise GuardOperationalError("Unsupported design-map schema_version; expected 1")
    required_keys = {
        "implementation_patterns",
        "ignore_patterns",
        "document_patterns",
        "required_artifacts",
        "blocking_placeholders",
        "document_mappings",
        "architecture_import_rules",
    }
    missing = sorted(required_keys.difference(policy))
    if missing:
        raise GuardOperationalError("Design map is missing keys: " + ", ".join(missing))
    list_keys = required_keys
    for key in list_keys:
        if not isinstance(policy[key], list):
            raise GuardOperationalError(f"Design map key {key!r} must be a list")
    for key in (
        "implementation_patterns",
        "ignore_patterns",
        "document_patterns",
        "required_artifacts",
        "blocking_placeholders",
    ):
        if not all(isinstance(value, str) for value in policy[key]):
            raise GuardOperationalError(
                f"Design map key {key!r} must contain only strings"
            )

    missing_artifacts = MANDATORY_ARTIFACTS.difference(policy["required_artifacts"])
    if missing_artifacts:
        raise GuardOperationalError(
            "Design map cannot remove mandatory artifacts: "
            + ", ".join(sorted(missing_artifacts))
        )
    for sentinel in IMPLEMENTATION_SENTINELS:
        if matches_any(sentinel, policy["ignore_patterns"]) or not matches_any(
            sentinel, policy["implementation_patterns"]
        ):
            raise GuardOperationalError(
                f"Design map leaves mandatory implementation path ungoverned: {sentinel}"
            )

    rules_by_code: Dict[str, Dict[str, Any]] = {}
    for rule in policy["architecture_import_rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("code"), str):
            raise GuardOperationalError(
                "Every architecture_import_rules entry requires a string code"
            )
        if not isinstance(rule.get("path_patterns"), list) or not isinstance(
            rule.get("forbidden_imports"), list
        ):
            raise GuardOperationalError(
                f"Architecture rule {rule['code']} has invalid pattern/import lists"
            )
        rules_by_code[rule["code"]] = rule
    for code, mandatory_imports in MANDATORY_ARCHITECTURE_IMPORTS.items():
        rule = rules_by_code.get(code)
        if rule is None:
            raise GuardOperationalError(
                f"Design map cannot remove mandatory architecture rule {code}"
            )
        missing_imports = mandatory_imports.difference(rule["forbidden_imports"])
        if missing_imports:
            raise GuardOperationalError(
                f"Architecture rule {code} cannot remove forbidden imports: "
                + ", ".join(sorted(missing_imports))
            )
    if not matches_any("doc/技术方案.md", policy["document_patterns"]):
        raise GuardOperationalError("Design map must govern project Markdown documents")
    return policy


def load_policy(root: Path, gate: str = "manual") -> Dict[str, Any]:
    config_path = root / CONFIG_PATH
    try:
        if gate == "commit":
            raw_policy = _read_index_text(root, CONFIG_PATH.as_posix())
            if raw_policy is None:
                raise FileNotFoundError(CONFIG_PATH)
        else:
            raw_policy = config_path.read_text(encoding="utf-8")
        policy = json.loads(raw_policy)
    except FileNotFoundError as error:
        raise GuardOperationalError(
            f"Missing design map: {CONFIG_PATH.as_posix()}"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GuardOperationalError(f"Invalid design map: {error}") from error
    return _validate_policy(policy)


def is_implementation_file(path: str, policy: Dict[str, Any]) -> bool:
    if matches_any(path, PROTECTED_IMPLEMENTATION_PATTERNS):
        return True
    if matches_any(path, policy["ignore_patterns"]):
        return False
    return matches_any(path, policy["implementation_patterns"])


def _run_git(
    root: Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise GuardOperationalError(f"Cannot execute Git: {error}") from error
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GuardOperationalError(
            f"Git command failed: git {' '.join(arguments)}: {detail}"
        )
    return completed


def _git_paths(root: Path, arguments: Sequence[str]) -> Set[str]:
    completed = _run_git(root, arguments)
    paths: Set[str] = set()
    for raw_path in completed.stdout.split("\0"):
        if raw_path:
            paths.add(normalize_relative_path(raw_path))
    return paths


def _read_index_text(root: Path, relative_path: str) -> Optional[str]:
    completed = _run_git(
        root,
        ("show", f":{relative_path}"),
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def _worktree_paths(root: Path) -> Set[str]:
    changed = _git_paths(
        root,
        ("diff", "--name-only", "-z", "--diff-filter=ACMRD", "--"),
    )
    changed.update(
        _git_paths(
            root,
            ("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRD", "--"),
        )
    )
    changed.update(
        _git_paths(root, ("ls-files", "--others", "--exclude-standard", "-z"))
    )
    return changed


def collect_changed_files(
    root: Path,
    *,
    scope: str,
    base_ref: Optional[str] = None,
) -> Set[str]:
    root = root.resolve()
    repository_check = _run_git(
        root, ("rev-parse", "--is-inside-work-tree"), check=False
    )
    if repository_check.returncode != 0:
        raise GuardOperationalError(f"{root} is not a Git work tree")

    if scope == "staged":
        return _git_paths(
            root,
            ("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRD", "--"),
        )
    if scope == "worktree":
        return _worktree_paths(root)
    if scope != "base":
        raise GuardOperationalError(f"Unsupported change scope: {scope}")
    if not base_ref:
        raise GuardOperationalError("base-ref scope requires a reference")

    base_check = _run_git(
        root,
        ("rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"),
        check=False,
    )
    head_check = _run_git(
        root,
        ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
        check=False,
    )
    if base_check.returncode == 0 and head_check.returncode == 0:
        merge_base = _run_git(
            root,
            ("merge-base", base_ref, "HEAD"),
        ).stdout.strip()
        if not merge_base:
            raise GuardOperationalError(
                f"Cannot determine merge-base for {base_ref} and HEAD"
            )
        return _git_paths(
            root,
            (
                "diff",
                "--name-only",
                "-z",
                "--diff-filter=ACMRD",
                merge_base,
                "HEAD",
                "--",
            ),
        )

    # A new repository or branch without its configured base has no safe delta.
    # Treat all tracked and working files as candidates rather than silently pass.
    changed = _git_paths(root, ("ls-files", "-z"))
    changed.update(_worktree_paths(root))
    return changed


def collect_commit_evidence_files(
    root: Path,
    staged_files: Set[str],
) -> Set[str]:
    """Return staged plus already-committed files in the current feature delta."""
    base_ref = os.environ.get("SDD_BASE_REF", "origin/main")
    evidence = set(staged_files)
    base_check = _run_git(
        root,
        ("rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"),
        check=False,
    )
    head_check = _run_git(
        root,
        ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
        check=False,
    )
    if base_check.returncode == 0 and head_check.returncode == 0:
        merge_base = _run_git(root, ("merge-base", base_ref, "HEAD")).stdout.strip()
        if not merge_base:
            raise GuardOperationalError(
                f"Cannot determine commit evidence merge-base for {base_ref}"
            )
        evidence.update(
            _git_paths(
                root,
                (
                    "diff",
                    "--name-only",
                    "-z",
                    "--diff-filter=ACMRD",
                    merge_base,
                    "HEAD",
                    "--",
                ),
            )
        )
    else:
        # Before the first push there is no trustworthy base. Every path already
        # in the index is part of the repository's deliverable evidence.
        evidence.update(_git_paths(root, ("ls-files", "-z")))
    return evidence


def _candidate_from_value(root: Path, value: str) -> Optional[Path]:
    raw = value.strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        if candidate.parts and candidate.parts[0] == "specs":
            candidate = root / candidate
        elif len(candidate.parts) == 1:
            candidate = root / "specs" / candidate
        else:
            candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    specs_root = (root / "specs").resolve(strict=False)
    if not _is_within(candidate, specs_root):
        raise GuardOperationalError(f"Feature directory escapes specs/: {value!r}")
    if not FEATURE_NAME_PATTERN.fullmatch(candidate.name):
        raise GuardOperationalError(
            f"Invalid Spec Kit feature name: {candidate.name!r}"
        )
    return candidate


def _feature_from_state(root: Path) -> Optional[Path]:
    state_file = root / ".specify" / "feature.json"
    if not state_file.exists():
        return None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GuardOperationalError(
            f"Invalid .specify/feature.json: {error}"
        ) from error
    value = state.get("feature_directory")
    if not isinstance(value, str) or not value.strip():
        raise GuardOperationalError(
            ".specify/feature.json has no valid feature_directory"
        )
    return _candidate_from_value(root, value)


def _feature_from_branch(root: Path) -> Optional[Path]:
    branch = _run_git(
        root,
        ("branch", "--show-current"),
        check=False,
    ).stdout.strip()
    if not branch:
        return None
    candidate_name = branch.rsplit("/", 1)[-1]
    if FEATURE_NAME_PATTERN.fullmatch(candidate_name):
        return root / "specs" / candidate_name
    return None


def resolve_feature(
    root: Path,
    changed_files: Set[str],
    *,
    explicit_feature: Optional[str] = None,
) -> Tuple[Optional[Path], List[Finding]]:
    findings: List[Finding] = []
    try:
        if explicit_feature:
            return _candidate_from_value(root, explicit_feature), findings

        environment_value = os.environ.get("SPECIFY_FEATURE_DIRECTORY")
        if environment_value:
            return _candidate_from_value(root, environment_value), findings
        environment_value = os.environ.get("SPECIFY_FEATURE")
        if environment_value:
            return _candidate_from_value(root, environment_value), findings

        state_candidate = _feature_from_state(root)
        changed_names = {
            match.group(1)
            for path in changed_files
            for match in [FEATURE_IN_PATH_PATTERN.match(path)]
            if match
        }
        if len(changed_names) > 1:
            findings.append(
                Finding(
                    code="SDD-002",
                    path="specs/",
                    message=(
                        "Implementation change touches multiple Spec Kit features: "
                        + ", ".join(sorted(changed_names))
                    ),
                    remediation="Split the change or pass --feature explicitly.",
                )
            )
            return None, findings

        changed_candidate = (
            root / "specs" / next(iter(changed_names)) if changed_names else None
        )
        branch_candidate = _feature_from_branch(root)
        candidates = [
            candidate
            for candidate in (state_candidate, changed_candidate, branch_candidate)
            if candidate is not None
        ]
        names = {candidate.name for candidate in candidates}
        if len(names) > 1:
            findings.append(
                Finding(
                    code="SDD-002",
                    path=".specify/feature.json",
                    message=(
                        "Conflicting feature context: " + ", ".join(sorted(names))
                    ),
                    remediation=(
                        "Run the intended Spec Kit feature command again or pass "
                        "--feature to select one feature explicitly."
                    ),
                )
            )
            return None, findings
        return (candidates[0] if candidates else None), findings
    except GuardOperationalError as error:
        findings.append(
            Finding(
                code="SDD-002",
                path=".specify/feature.json",
                message=str(error),
                remediation="Repair the Spec Kit feature context and retry.",
            )
        )
        return None, findings


def _read_text(path: Path, code: str, findings: List[Finding], root: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        findings.append(
            Finding(
                code=code,
                path=path.relative_to(root).as_posix(),
                message=f"Cannot read required artifact: {error}",
                remediation="Restore a valid UTF-8 artifact and retry.",
            )
        )
        return ""


def _is_tracked(root: Path, relative_path: str) -> bool:
    completed = _run_git(
        root,
        ("ls-files", "--error-unmatch", "--", relative_path),
        check=False,
    )
    return completed.returncode == 0


def _task_paths(tasks_text: str) -> Set[str]:
    paths: Set[str] = set()
    for quoted in re.findall(r"`([^`\n]+)`", tasks_text):
        if "/" in quoted or "." in quoted:
            try:
                paths.add(normalize_relative_path(quoted))
            except GuardOperationalError:
                continue
    without_quoted = re.sub(r"`[^`\n]*`", " ", tasks_text)
    for token in without_quoted.split():
        candidate = token.strip(",;:()[]{}<>\"'")
        if "/" not in candidate and "." not in candidate:
            continue
        try:
            paths.add(normalize_relative_path(candidate))
        except GuardOperationalError:
            continue
    return paths


def validate_artifacts(
    root: Path,
    feature_dir: Path,
    implementation_files: Sequence[str],
    changed_files: Set[str],
    policy: Dict[str, Any],
    gate: str,
) -> Tuple[List[Finding], Dict[str, str]]:
    findings: List[Finding] = []
    contents: Dict[str, str] = {}

    if not feature_dir.is_dir():
        return [
            Finding(
                code="SDD-003",
                path=feature_dir.relative_to(root).as_posix(),
                message="The active Spec Kit feature directory does not exist.",
                remediation="Run $speckit-specify and complete the feature artifacts.",
            )
        ], contents

    for artifact_name in policy["required_artifacts"]:
        artifact_path = feature_dir / artifact_name
        relative_path = artifact_path.relative_to(root).as_posix()
        if gate == "commit":
            content = _read_index_text(root, relative_path)
            artifact_exists = content is not None
        else:
            artifact_exists = artifact_path.is_file()
            content = (
                _read_text(artifact_path, "SDD-004", findings, root)
                if artifact_exists
                else ""
            )
        if not artifact_exists:
            findings.append(
                Finding(
                    code="SDD-003",
                    path=relative_path,
                    message=f"Missing required Spec Kit artifact: {artifact_name}",
                    remediation=(
                        "Complete spec.md, plan.md, tasks.md and drift-report.md "
                        "before implementation."
                    ),
                )
            )
            continue
        assert content is not None
        contents[artifact_name] = content
        if not content.strip():
            findings.append(
                Finding(
                    code="SDD-004",
                    path=relative_path,
                    message="Required Spec Kit artifact is empty.",
                    remediation="Complete the artifact before implementation.",
                )
            )
        for placeholder in policy["blocking_placeholders"]:
            if placeholder.casefold() in content.casefold():
                findings.append(
                    Finding(
                        code="SDD-005",
                        path=relative_path,
                        message=f"Blocking template placeholder remains: {placeholder}",
                        remediation=(
                            "Resolve clarification/template markers before running "
                            "$speckit-implement."
                        ),
                    )
                )
        if gate in {"push", "ci"} and relative_path not in changed_files:
            findings.append(
                Finding(
                    code="SDD-008",
                    path=relative_path,
                    message="Feature artifact is absent from the branch change set.",
                    remediation=(
                        "Include all active feature artifacts in the pull request "
                        "or select the correct feature."
                    ),
                )
            )

    spec_text = contents.get("spec.md", "")
    if spec_text and (
        "## Requirements" not in spec_text or "## Success Criteria" not in spec_text
    ):
        findings.append(
            Finding(
                code="SDD-004",
                path=(feature_dir / "spec.md").relative_to(root).as_posix(),
                message="spec.md lacks Requirements or Success Criteria.",
                remediation="Run $speckit-specify and complete both mandatory sections.",
            )
        )
    plan_text = contents.get("plan.md", "")
    if plan_text and "## Constitution Check" not in plan_text:
        findings.append(
            Finding(
                code="SDD-004",
                path=(feature_dir / "plan.md").relative_to(root).as_posix(),
                message="plan.md lacks the Constitution Check gate.",
                remediation="Run $speckit-plan and re-check the constitution.",
            )
        )
    tasks_text = contents.get("tasks.md", "")
    if tasks_text and not TASK_PATTERN.search(tasks_text):
        findings.append(
            Finding(
                code="SDD-004",
                path=(feature_dir / "tasks.md").relative_to(root).as_posix(),
                message="tasks.md contains no executable Spec Kit task IDs.",
                remediation="Run $speckit-tasks and create concrete tasks.",
            )
        )
    traced_paths = _task_paths(tasks_text)
    for implementation_path in implementation_files:
        if tasks_text and implementation_path not in traced_paths:
            findings.append(
                Finding(
                    code="SDD-006",
                    path=implementation_path,
                    message="Implementation path is not traceable from tasks.md.",
                    remediation=(
                        f"Add an exact-path task for {implementation_path} before "
                        "changing the file."
                    ),
                )
            )
    return findings, contents


def _parse_report_fields(report: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for line in report.splitlines():
        match = FIELD_PATTERN.match(line)
        if match:
            fields[match.group(1).casefold()] = match.group(2).strip()
    return fields


def _split_document_paths(value: str) -> List[str]:
    if value.strip().upper() in {"", "N/A", "NONE", "-"}:
        return []
    parts = re.split(r"[,;、]", value)
    return [
        normalize_relative_path(part.strip().strip("`"))
        for part in parts
        if part.strip()
    ]


def _is_concrete_reason(reason: str) -> bool:
    normalized = reason.strip().casefold()
    invalid = {
        "",
        "n/a",
        "none",
        "todo",
        "no change",
        "no change.",
        "无",
        "无变化",
        "待补充",
    }
    return len(reason.strip()) >= MIN_REASON_LENGTH and normalized not in invalid


def _mapped_documents(
    implementation_files: Sequence[str],
    policy: Dict[str, Any],
) -> Set[str]:
    mapped: Set[str] = set()
    for mapping in policy["document_mappings"]:
        patterns = mapping.get("implementation_patterns", [])
        if any(matches_any(path, patterns) for path in implementation_files):
            mapped.update(mapping.get("documents", []))
    return mapped


def validate_drift_report(
    root: Path,
    feature_dir: Path,
    report: str,
    implementation_files: Sequence[str],
    changed_files: Set[str],
    policy: Dict[str, Any],
    gate: str,
) -> List[Finding]:
    if not report:
        return []
    findings: List[Finding] = []
    report_path = (feature_dir / "drift-report.md").relative_to(root).as_posix()
    fields = _parse_report_fields(report)
    required_fields = {
        "feature",
        "status",
        "docs-impact",
        "docs-updated",
        "docs-impact-reason",
        "reviewed-by",
    }
    for missing in sorted(required_fields.difference(fields)):
        findings.append(
            Finding(
                code="DRIFT-001",
                path=report_path,
                message=f"Drift report is missing field: {missing}",
                remediation="Complete the drift report from the repository template.",
            )
        )
    if findings:
        return findings

    if fields["feature"].strip("`") != feature_dir.name:
        findings.append(
            Finding(
                code="DRIFT-002",
                path=report_path,
                message=(
                    f"Drift report feature {fields['feature']!r} does not match "
                    f"{feature_dir.name!r}."
                ),
                remediation="Review and update the active feature drift report.",
            )
        )

    status = fields["status"].upper()
    if status not in {"ALIGNED", "BLOCKED"}:
        findings.append(
            Finding(
                code="DRIFT-002",
                path=report_path,
                message="Status must be ALIGNED or BLOCKED.",
                remediation="Use a supported status after reviewing implementation.",
            )
        )
    elif status == "BLOCKED":
        findings.append(
            Finding(
                code="DRIFT-003",
                path=report_path,
                message="The feature drift report is explicitly BLOCKED.",
                remediation=(
                    "Synchronize Spec/Plan/Tasks/documents or revert the "
                    "implementation, then set ALIGNED with evidence."
                ),
            )
        )

    docs_impact = fields["docs-impact"].upper()
    reason = fields["docs-impact-reason"]
    try:
        listed_documents = _split_document_paths(fields["docs-updated"])
    except GuardOperationalError as error:
        findings.append(
            Finding(
                code="DRIFT-005",
                path=report_path,
                message=str(error),
                remediation="Use repository-relative document paths.",
            )
        )
        listed_documents = []

    if not _is_concrete_reason(reason):
        findings.append(
            Finding(
                code="DRIFT-006",
                path=report_path,
                message=(
                    f"Docs-Impact-Reason must contain at least "
                    f"{MIN_REASON_LENGTH} characters of concrete reasoning."
                ),
                remediation="Explain which approved semantics do or do not change.",
            )
        )

    if docs_impact == "UPDATED":
        if not listed_documents:
            findings.append(
                Finding(
                    code="DRIFT-004",
                    path=report_path,
                    message="Docs-Impact is UPDATED but Docs-Updated is empty.",
                    remediation="List every updated repository document.",
                )
            )
        for document in listed_documents:
            if not matches_any(document, policy["document_patterns"]):
                findings.append(
                    Finding(
                        code="DRIFT-005",
                        path=document,
                        message="Docs-Updated contains a path outside governed documents.",
                        remediation="List a governed Markdown document path.",
                    )
                )
                continue
            document_exists = (
                _is_tracked(root, document)
                if gate == "commit"
                else (root / document).is_file()
            )
            if not document_exists:
                findings.append(
                    Finding(
                        code="DRIFT-005",
                        path=document,
                        message="Declared updated document does not exist.",
                        remediation="Create the document or correct Docs-Updated.",
                    )
                )
            if document not in changed_files:
                findings.append(
                    Finding(
                        code="DRIFT-004",
                        path=document,
                        message="Declared updated document is absent from the change set.",
                        remediation="Include the synchronized document in this change.",
                    )
                )
        mapped_documents = _mapped_documents(implementation_files, policy)
        if mapped_documents and not mapped_documents.intersection(listed_documents):
            findings.append(
                Finding(
                    code="DRIFT-005",
                    path=report_path,
                    message=(
                        "Updated documents do not cover the changed implementation "
                        "area. Expected one of: " + ", ".join(sorted(mapped_documents))
                    ),
                    remediation="Update and list an affected design document.",
                )
            )
    elif docs_impact == "NONE":
        if listed_documents:
            findings.append(
                Finding(
                    code="DRIFT-007",
                    path=report_path,
                    message="Docs-Impact is NONE but Docs-Updated lists documents.",
                    remediation="Use UPDATED or clear Docs-Updated and explain why.",
                )
            )
    else:
        findings.append(
            Finding(
                code="DRIFT-002",
                path=report_path,
                message="Docs-Impact must be UPDATED or NONE.",
                remediation="Classify the document impact explicitly.",
            )
        )

    if fields["reviewed-by"].upper() not in {"AI", "HUMAN", "AI+HUMAN", "HUMAN+AI"}:
        findings.append(
            Finding(
                code="DRIFT-002",
                path=report_path,
                message="Reviewed-By must identify AI, HUMAN, or AI+HUMAN.",
                remediation="Record who performed the alignment review.",
            )
        )
    return findings


def _imported_modules(tree: ast.AST, relative_path: str) -> List[Tuple[str, int]]:
    modules: List[Tuple[str, int]] = []
    path_parts = PurePosixPath(relative_path).parts
    package_parts: Tuple[str, ...] = ()
    if "src" in path_parts:
        source_index = len(path_parts) - 1 - path_parts[::-1].index("src")
        package_parts = path_parts[source_index + 1 : -1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                if node.module:
                    modules.append((node.module, node.lineno))
                continue
            parent_levels = node.level - 1
            if parent_levels > len(package_parts):
                base_parts: Tuple[str, ...] = ()
            elif parent_levels:
                base_parts = package_parts[:-parent_levels]
            else:
                base_parts = package_parts
            if node.module:
                module_parts = tuple(node.module.split("."))
                normalized = ".".join((*base_parts, *module_parts))
                if normalized:
                    modules.append((normalized, node.lineno))
            else:
                for alias in node.names:
                    normalized = ".".join((*base_parts, alias.name))
                    if normalized:
                        modules.append((normalized, node.lineno))
    return modules


def _forbidden_import(module: str, forbidden: str) -> bool:
    if forbidden.endswith("*"):
        return module.startswith(forbidden[:-1])
    return module == forbidden or module.startswith(forbidden + ".")


def validate_architecture(
    root: Path,
    implementation_files: Sequence[str],
    policy: Dict[str, Any],
    gate: str,
) -> List[Finding]:
    findings: List[Finding] = []
    rules = policy["architecture_import_rules"]
    for relative_path in implementation_files:
        if not relative_path.endswith(".py"):
            continue
        source_path = root / relative_path
        if gate == "commit":
            source = _read_index_text(root, relative_path)
            if source is None:
                continue
        else:
            if not source_path.is_file():
                continue
            try:
                source = source_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                findings.append(
                    Finding(
                        code="ARCH-000",
                        path=relative_path,
                        message=f"Cannot read Python source for validation: {error}",
                        remediation="Restore a valid UTF-8 source file and retry.",
                    )
                )
                continue
        matching_rules = [
            rule
            for rule in rules
            if matches_any(relative_path, rule.get("path_patterns", []))
        ]
        if not matching_rules:
            continue
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as error:
            line = getattr(error, "lineno", None)
            findings.append(
                Finding(
                    code="ARCH-000",
                    path=f"{relative_path}:{line}" if line else relative_path,
                    message=f"Cannot statically validate Python architecture: {error}",
                    remediation="Fix the file so the architecture gate can parse it.",
                )
            )
            continue
        for module, line in _imported_modules(tree, relative_path):
            for rule in matching_rules:
                forbidden = next(
                    (
                        item
                        for item in rule.get("forbidden_imports", [])
                        if _forbidden_import(module, item)
                    ),
                    None,
                )
                if forbidden:
                    findings.append(
                        Finding(
                            code=rule["code"],
                            path=f"{relative_path}:{line}",
                            message=(
                                f"Forbidden import {module!r}. "
                                f"{rule.get('description', '')}".strip()
                            ),
                            remediation=(
                                "Move the dependency behind an inward-facing port "
                                "or revise the approved architecture before coding."
                            ),
                        )
                    )
    return findings


def evaluate_repository(
    *,
    root: Path,
    changed_files: Set[str],
    gate: str,
    explicit_feature: Optional[str] = None,
) -> GuardResult:
    root = root.resolve()
    if not changed_files:
        return GuardResult(
            passed=True,
            gate=gate,
            feature=None,
            changed_files=(),
            implementation_files=(),
            findings=(),
        )
    normalized_changes: Set[str] = set()
    try:
        normalized_changes = {normalize_relative_path(path) for path in changed_files}
        policy = load_policy(root, gate)
    except GuardOperationalError as error:
        finding = Finding(
            code="CONFIG-001",
            path=CONFIG_PATH.as_posix(),
            message=str(error),
            remediation="Repair governance configuration; checks fail closed.",
        )
        return GuardResult(
            passed=False,
            gate=gate,
            feature=None,
            changed_files=tuple(sorted(normalized_changes)),
            implementation_files=(),
            findings=(finding,),
        )

    implementation_files = tuple(
        sorted(
            path for path in normalized_changes if is_implementation_file(path, policy)
        )
    )
    if not implementation_files:
        return GuardResult(
            passed=True,
            gate=gate,
            feature=None,
            changed_files=tuple(sorted(normalized_changes)),
            implementation_files=(),
            findings=(),
        )

    feature_dir, findings = resolve_feature(
        root,
        normalized_changes,
        explicit_feature=explicit_feature,
    )
    if feature_dir is None and not findings:
        findings.append(
            Finding(
                code="SDD-001",
                path="specs/",
                message="Implementation changes have no active Spec Kit feature.",
                remediation=(
                    "Run $speckit-specify, $speckit-plan and $speckit-tasks "
                    "before editing implementation files."
                ),
            )
        )

    contents: Dict[str, str] = {}
    if feature_dir is not None:
        artifact_findings, contents = validate_artifacts(
            root,
            feature_dir,
            implementation_files,
            normalized_changes,
            policy,
            gate,
        )
        findings.extend(artifact_findings)
        evidence_changes = normalized_changes
        if gate == "commit":
            try:
                evidence_changes = collect_commit_evidence_files(
                    root, normalized_changes
                )
            except GuardOperationalError as error:
                findings.append(
                    Finding(
                        code="GIT-002",
                        message=str(error),
                        remediation=(
                            "Repair the feature branch/base reference before committing."
                        ),
                    )
                )
        findings.extend(
            validate_drift_report(
                root,
                feature_dir,
                contents.get("drift-report.md", ""),
                implementation_files,
                evidence_changes,
                policy,
                gate,
            )
        )

    findings.extend(validate_architecture(root, implementation_files, policy, gate))
    ordered_findings = tuple(
        sorted(
            findings, key=lambda finding: (finding.path, finding.code, finding.message)
        )
    )
    return GuardResult(
        passed=not ordered_findings,
        gate=gate,
        feature=feature_dir.name if feature_dir is not None else None,
        changed_files=tuple(sorted(normalized_changes)),
        implementation_files=implementation_files,
        findings=ordered_findings,
    )


def _print_text(result: GuardResult) -> None:
    if result.passed:
        summary = (
            f"SDD drift gate passed ({result.gate}); "
            f"{len(result.implementation_files)} implementation file(s) checked."
        )
        if result.feature:
            summary += f" Feature: {result.feature}."
        print(summary)
        return

    print(
        f"SDD drift gate failed ({result.gate}) with "
        f"{len(result.findings)} finding(s):",
        file=sys.stderr,
    )
    for finding in result.findings:
        location = f" [{finding.path}]" if finding.path else ""
        print(f"- {finding.code}{location}: {finding.message}", file=sys.stderr)
        if finding.remediation:
            print(f"  Fix: {finding.remediation}", file=sys.stderr)


def _repository_root(value: Optional[str]) -> Path:
    if value:
        return Path(value).resolve()
    git_root = subprocess.run(
        ("git", "rev-parse", "--show-toplevel"),
        check=False,
        capture_output=True,
        text=True,
    )
    if git_root.returncode == 0 and git_root.stdout.strip():
        return Path(git_root.stdout.strip()).resolve()
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Spec Kit completeness and implementation/design drift."
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--staged", action="store_true", help="Check staged changes.")
    scope.add_argument(
        "--worktree",
        action="store_true",
        help="Check staged, unstaged and untracked changes (default).",
    )
    scope.add_argument(
        "--base-ref",
        metavar="REF",
        help="Check committed changes since merge-base with REF.",
    )
    parser.add_argument(
        "--gate",
        choices=("manual", "commit", "push", "ci", "ai-stop"),
        default="manual",
    )
    parser.add_argument("--feature", help="Explicit Spec Kit feature name or path.")
    parser.add_argument("--root", help="Repository root; auto-detected by default.")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
    )
    return parser


def main(arguments: Optional[Sequence[str]] = None) -> int:
    options = build_parser().parse_args(arguments)
    root = _repository_root(options.root)
    if options.staged:
        scope = "staged"
    elif options.base_ref:
        scope = "base"
    else:
        scope = "worktree"

    try:
        changed_files = collect_changed_files(
            root,
            scope=scope,
            base_ref=options.base_ref,
        )
        result = evaluate_repository(
            root=root,
            changed_files=changed_files,
            gate=options.gate,
            explicit_feature=options.feature,
        )
    except GuardOperationalError as error:
        result = GuardResult(
            passed=False,
            gate=options.gate,
            feature=None,
            changed_files=(),
            implementation_files=(),
            findings=(
                Finding(
                    code="GIT-001",
                    message=str(error),
                    remediation="Repair repository state; checks fail closed.",
                ),
            ),
        )

    if options.output_format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        _print_text(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
