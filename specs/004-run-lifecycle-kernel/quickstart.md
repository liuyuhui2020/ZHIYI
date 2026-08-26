# Quickstart: Validate Run Lifecycle Kernel

## Prerequisites

- Python 3.12
- `uv`
- 无需数据库、网络、Provider 凭据或 LangGraph

## Install Frozen Environment

```bash
uv sync --all-groups --frozen --python 3.12
```

## Focused Validation

```bash
uv run pytest tests/unit/domain/runs -q
uv run pytest tests/unit/application/commands/test_run_lifecycle_commands.py -q
uv run pytest tests/unit/application/services/test_run_lifecycle.py -q
uv run pytest tests/contract/persistence/test_run_repository_contract.py -q
uv run pytest tests/unit/adapters/persistence/test_memory_run_repository.py -q
uv run pytest tests/performance/test_run_lifecycle_overhead.py -q
```

Expected outcomes:

- 合法/非法状态矩阵、终态不可逆和固定 AgentVersion 全部通过。
- 相同命令/charge 重放不增加版本、用量或事件；不同意图复用稳定冲突。
- 至少 1,000 组并发命令每组只有一个不同意图获胜。
- deadline、等值上限、超限、取消和时钟倒退行为符合 spec。
- 事件 sequence 连续，四类终态各有且只有一个 RunResult。
- 敏感 sentinel 不进入异常、receipt、事件、结果或 repr。
- 10,000 次纯领域转换 p95 小于 1 ms。

## Full Python Gate

```bash
uv run pytest -m "not online"
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

## Governance Gate

```bash
python3 -m unittest discover -s scripts/sdd/tests -v
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
```

## Manual Contract Walkthrough

1. 使用固定时钟、确定性标识生成器和内存仓储创建 Run。
2. 观察 `queued`、version=1、`run.created` sequence=1 和固定 AgentVersion。
3. start 后提交一个恰好达到硬上限的 BudgetCharge，确认仍为 running。
4. 重放相同 charge command，确认 receipt 标记 replay 且 usage/event 不增加。
5. 提交会超过上限的新 charge，确认直接进入 limit_exceeded，超限 charge 不计入 usage。
6. 查询事件，确认 sequence 连续且终态结果中的 usage 与 Run 一致。
7. 对同一 expected_version 并发提交两个不同命令，确认一个成功、一个 version_conflict。

## Scope Guard

验证期间不应启动数据库、HTTP Server、Worker、LangGraph 或真实模型。任何需要这些组件
才能通过的测试都表示本 Feature 越界，应先回到 Spec/Plan 处理。
