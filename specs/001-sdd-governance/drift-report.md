# Design Drift Report

**Feature**: 001-sdd-governance
**Status**: ALIGNED
**Docs-Impact**: NONE
**Docs-Updated**: N/A
**Docs-Impact-Reason**: 本次修正仅优化既有路径匹配和架构检查执行顺序，不改变已批准的门禁语义、产品设计或长期开发流程。
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

GitHub Actions 首次运行暴露了 2.372 秒的性能回归。修正后，本地连续五次
5,000 路径检查耗时为 0.444–0.554 秒；29 项测试和静态质量检查均通过。
第二次 GitHub Actions 仍耗时 2.376 秒，说明 Runner 的主要瓶颈在通用 glob
对象分配。T041 增加快速路径后，本地连续十次耗时为 0.217–0.249 秒，
并保持全部 29 项测试通过；GitHub Actions 复验继续由 T040 跟踪。

## Intentional Differences

- 本地实际 `python3` 为 3.9，因此治理检查器保持 Python 3.9+ 标准库兼容；
  产品 Runtime 的目标版本仍为 Python 3.12。
- 保留旧 `openspec/` 文件，避免在未获删除授权时破坏历史资料；它不参与
  当前事实源和门禁。

## Blocking Findings

None. GitHub Actions 复验由 T040 跟踪，若失败则重新设为 BLOCKED。
