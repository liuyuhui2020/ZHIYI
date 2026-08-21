#!/usr/bin/env python3
"""Claude Code Stop Hook adapter for the shared SDD drift checker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

MAX_HOOK_INPUT_BYTES = 1_000_000
MAX_FINDINGS_IN_REASON = 8


def _block(reason: str) -> None:
    print(
        json.dumps(
            {"decision": "block", "reason": reason},
            ensure_ascii=False,
        )
    )


def _read_payload() -> Dict[str, Any]:
    payload = sys.stdin.read(MAX_HOOK_INPUT_BYTES + 1)
    if len(payload) > MAX_HOOK_INPUT_BYTES:
        raise ValueError("Hook input exceeds the 1 MB safety limit")
    if not payload.strip():
        return {}
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Hook input must be a JSON object")
    return parsed


def _project_root(payload: Dict[str, Any]) -> Path:
    candidate = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if isinstance(candidate, str) and candidate.strip():
        return Path(candidate).resolve()
    return Path(__file__).resolve().parents[2]


def _reason_from_result(result: Dict[str, Any]) -> str:
    findings = result.get("findings")
    if not isinstance(findings, list):
        return "SDD 漂移检查失败且未返回结构化结果。请手动运行设计漂移检查器。"
    lines: List[str] = [
        "检测到实现与 Spec Kit/设计约束不一致，请继续工作，不要结束当前任务："
    ]
    for finding in findings[:MAX_FINDINGS_IN_REASON]:
        if not isinstance(finding, dict):
            continue
        code = finding.get("code", "UNKNOWN")
        path = finding.get("path", "")
        message = finding.get("message", "")
        remediation = finding.get("remediation", "")
        location = f" [{path}]" if path else ""
        lines.append(f"- {code}{location}: {message}")
        if remediation:
            lines.append(f"  修复：{remediation}")
    if len(findings) > MAX_FINDINGS_IN_REASON:
        lines.append(
            f"- 另有 {len(findings) - MAX_FINDINGS_IN_REASON} 项；请运行完整检查命令。"
        )
    lines.append(
        "处理方式只有两种：先同步 Spec/Plan/Tasks/受影响文档并重新检查，"
        "或撤销漂移实现。架构硬违规不得通过修改报告豁免。"
    )
    return "\n".join(lines)


def main() -> int:
    try:
        payload = _read_payload()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _block(f"无法解析 Claude Hook 输入，按 fail-closed 处理：{error}")
        return 0

    # Claude re-enters Stop hooks after a block. Allow the recursive attempt to
    # terminate to avoid an infinite loop; Git and CI remain authoritative.
    if payload.get("stop_hook_active") is True:
        return 0

    root = _project_root(payload)
    checker = root / "scripts" / "sdd" / "check_design_drift.py"
    try:
        completed = subprocess.run(
            (
                sys.executable,
                str(checker),
                "--worktree",
                "--gate",
                "ai-stop",
                "--format",
                "json",
                "--root",
                str(root),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _block(f"SDD 漂移检查器无法运行，按 fail-closed 处理：{error}")
        return 0

    if completed.returncode == 0:
        return 0
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        detail = completed.stderr.strip() or completed.stdout.strip() or "无输出"
        _block(f"SDD 漂移检查器异常，按 fail-closed 处理：{detail[:2000]}")
        return 0
    _block(_reason_from_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
