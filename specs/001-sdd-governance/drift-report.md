# Design Drift Report

**Feature**: 001-sdd-governance
**Status**: ALIGNED
**Docs-Impact**: UPDATED
**Docs-Updated**: doc/SDD开发规范.md, doc/AGENTS.md, doc/README.md, doc/PROJECT.md, doc/需求文档.md, doc/技术方案.md
**Docs-Impact-Reason**: 本功能改变了项目的开发事实源、AI 编程流程、提交门禁和进入产品实现阶段的条件，因此同步更新长期治理、需求和技术文档。
**Reviewed-By**: AI

## Alignment Evidence

- FR-001~FR-002：官方 Spec Kit 共享资产、Codex/Claude 集成和项目宪法已经落地。
- FR-003~FR-008：检查器验证四类 feature 工件、任务路径、报告状态和文档影响。
- FR-009~FR-014：worktree/staged/base-ref、Git/Claude/CI 入口、Git index
  快照、相对导入、稳定错误码和 fail-closed 行为由标准库实现并有测试覆盖。
- FR-015：旧 `openspec/` 未被删除，但所有长期文档已明确其历史输入地位。
- 架构检查器、Hook 适配器和文档路径都能从 `tasks.md` 追踪。

## Verification Evidence

- `python3 -m unittest discover -s scripts/sdd/tests -v`
- `python3 -m json.tool .specify/governance/design-map.json`
- `python3 -m json.tool .claude/settings.json`
- `sh -n .githooks/pre-commit .githooks/pre-push scripts/sdd/install_hooks.sh`
- `python3 scripts/sdd/check_design_drift.py --worktree --gate manual`

最终测试集包含 29 个场景，并覆盖 5,000 变更路径小于 2 秒的门禁目标。

## Intentional Differences

- 本地实际 `python3` 为 3.9，因此治理检查器保持 Python 3.9+ 标准库兼容；
  产品 Runtime 的目标版本仍为 Python 3.12。
- 保留旧 `openspec/` 文件，避免在未获删除授权时破坏历史资料；它不参与
  当前事实源和门禁。

## Blocking Findings

None.
