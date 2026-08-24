# Implementation Plan: Run Lifecycle Kernel

**Branch**: `codex/004-run-lifecycle-kernel` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-run-lifecycle-kernel/spec.md`

## Summary

建立不依赖外层框架的 Run 领域内核：不可变 AgentVersion 引用、集中状态机、硬预算、
稳定事件/结果/错误，以及基于命令收据与预期版本的应用命令服务。持久化通过异步端口
表达，内存适配器只用于离线契约与并发验收。该切片不接入 PostgreSQL、租约、Worker、
LangGraph、REST/SSE、Model Gateway 或 Tool 执行。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: 新增产品代码只使用 Python 标准库；沿用已锁定的 pytest
9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.4 和 mypy 2.3.1；不新增运行时依赖

**Storage**: 框架无关 `RunRepository` 异步端口与进程内内存验证适配器；无数据库、
Schema、迁移或文件持久化

**Testing**: pytest 单元/契约/并发/性能测试、可控 UTC 时钟、确定性标识生成器；全部
离线且无需 Provider、LangGraph 或数据库

**Target Platform**: Linux/OCI 生产目标，macOS/Linux 本地开发；异步 Python Runtime

**Project Type**: 模块化单体中的领域 library 与 application use-case slice

**Performance Goals**: 排除测试调度与持久化等待后，10,000 次纯本地领域转换 p95
< 1 ms；至少 1,000 组异步并发竞争无丢失更新、重复事件或部分状态

**Constraints**: domain 只依赖标准库且不得导入外层项目模块；所有值不可变；金额使用
`Decimal`；时间必须为 UTC aware datetime 并以不会倒退的观察值推进；命令、版本、
Run/事件序列与收据原子提交；错误/事件/结果不得包含敏感正文或第三方对象

**Scale/Scope**: 8 个 Run 状态、4 个终态、8 类预算维度、状态/预算/取消/结果命令、
一个持久化端口和一个内存适配器；不包含生产队列、租约或执行图

## Constitution Check

*GATE: Phase 0 前通过；Phase 1 设计后复核通过。*

- **I. Specification Before Implementation — PASS**: `spec.md` 已完成、质量检查
  16/16 通过且无澄清标记；实现只能在 plan/tasks/analyze 后开始。
- **II. Product Semantics Own the Framework — PASS**: Run、AgentVersion、预算、事件和
  结果位于 domain；application 只依赖 domain/ports；内存持久化位于 adapter。领域层不
  导入 Pydantic、LangChain、LangGraph、FastAPI、SQLAlchemy 或 Provider 类型。
- **III. Test-First and Traceability — PASS**: 每个故事先建立失败测试；正常、非法、
  边界、租户隔离、并发、幂等、取消、时间倒退、终态、脱敏和性能均有准确测试路径。
- **IV. Recoverable, Idempotent Agent Execution — PASS**: 每个 Run 固定 AgentVersion；
  每个非终态可取消/超限；命令收据和预期版本定义恢复重放语义；本 Feature 不执行 Tool。
- **V. Tools and Context Are Untrusted — PASS**: Feature 不接收 Tool/Context 正文且不执行
  副作用；事件载荷为允许列表中的领域元数据，不允许任意对象或隐藏推理。
- **VI. Tenant Isolation, Privacy, Least Privilege — PASS**: Run、命令、收据、事件和仓储
  操作均显式携带 tenant_id；跨租户返回统一未找到；敏感正文不进入可打印契约。
- **VII. Observable Without Hidden Reasoning — PASS**: 状态和预算变化生成稳定事件；只
  保存决策、摘要、标识和安全错误，不保存原始 Chain-of-Thought。
- **VIII. Simple, Versioned, Reversible — PASS**: 不新增依赖或迁移；事件/结果带载荷版本；
  回滚只移除本切片及文档状态更新，不影响 Provider Gateway 或外部状态。

**Post-design re-check**: PASS。`research.md`、`data-model.md`、
`contracts/run-lifecycle.md` 和 `quickstart.md` 保持领域内聚、端口/适配器方向、原子提交、
确定性时钟、租户隔离和零副作用边界，没有宪法例外。

## Project Structure

### Documentation (this feature)

```text
specs/004-run-lifecycle-kernel/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── run-lifecycle.md
├── checklists/
│   ├── requirements.md
│   └── lifecycle-safety.md
├── tasks.md
└── drift-report.md
```

### Source Code (repository root)

```text
src/zhiyi/
├── domain/
│   ├── __init__.py
│   └── runs/
│       ├── __init__.py
│       ├── aggregate.py
│       ├── budget.py
│       ├── errors.py
│       ├── events.py
│       ├── identifiers.py
│       └── results.py
├── application/
│   ├── commands/
│   │   ├── __init__.py
│   │   └── run_lifecycle.py
│   ├── ports/
│   │   ├── clock.py
│   │   ├── identifier_generator.py
│   │   └── run_repository.py
│   └── services/
│       └── run_lifecycle.py
└── adapters/
    └── persistence/
        ├── __init__.py
        └── memory_run_repository.py

tests/
├── contract/persistence/test_run_repository_contract.py
├── performance/test_run_lifecycle_overhead.py
└── unit/
    ├── domain/runs/
    │   ├── test_aggregate.py
    │   ├── test_budget.py
    │   └── test_events_results.py
    ├── application/
    │   ├── commands/test_run_lifecycle_commands.py
    │   └── services/test_run_lifecycle.py
    └── adapters/persistence/test_memory_run_repository.py
```

**Structure Decision**: 首次创建技术方案中既定的 `domain/runs` 领域包。值对象、状态机、
预算和事件不依赖 application；命令服务负责时钟/标识生成、租户作用域、命令指纹和
仓储编排；仓储 Protocol 放 application port；仅内存实现放 adapter。现有 Model Gateway
模块不移动、不反向依赖 domain，也不在本切片集成。

## Design Decisions

### 1. 不可变聚合与集中转换

`Run`、预算、事件和结果使用冻结值对象。公开方法不原地修改，而是返回新的 Run
快照和零或一个领域事件；所有状态转换通过聚合内部的唯一转换表。Run 从版本 1、
事件序号 1 开始，每次成功状态或预算变化同时递增版本和事件序号。非法转换、验证
错误、重复和版本冲突不产生新快照或事件。

### 2. 命令收据与乐观并发是两个独立机制

每个命令生成不包含正文的规范化 SHA-256 意图指纹。指纹包含租户、目标、命令类型与
业务 payload，但不包含 command_id、expected_version 或由服务生成的时间/标识；因此
版本冲突后可用同一 command_id 和更新后的 expected_version 重试同一业务意图。仓储
原子提交时按以下顺序判定：

1. 同租户、同命令标识已有收据且指纹相同：返回原收据，标记 replay，不检查旧版本。
2. 同命令标识但指纹不同：返回 `idempotency_conflict`。
3. 新命令的预期版本与当前版本不符：返回 `version_conflict`。
4. 校验更新快照、连续事件和收据后一次性提交。

这保证丢失响应后的重试能够成功重放，同时不同并发意图保持单一获胜者。命令收据只
保存规范化摘要、结果状态/版本和事件标识，不复制答案、错误正文或其他敏感内容。

### 3. 持久化端口表达原子边界，不泄露 ORM

`RunRepository` 提供 tenant-scoped load、event read、`find_command` 和 atomic commit。
应用服务先用 command_id/fingerprint 查找已完成命令，使终态 Run 的合法重放不会先被
领域转换拒绝；commit 在同一原子边界内再次执行判重，以关闭 lookup 与 commit 之间的
并发窗口。commit 输入旧版本、新 Run、待追加事件和 CommandReceipt，输出
`CommitOutcome(receipt, replayed)`；端口错误使用平台类型。内存适配器以异步锁实现与
未来 PostgreSQL 唯一约束/CAS 等价的契约，但不承诺跨进程持久化、队列或租约语义。

### 4. 预算采用显式硬上限和幂等消耗

`RunBudget` 固定 UTC deadline、次数/Token 上限、Decimal 成本上限和 ISO 4217 风格
币种键。`BudgetCharge` 所有维度非负且至少一项大于零，使用稳定 charge_id 与安全
指纹去重。`BudgetSnapshot` 只累计已确认消耗，时钟观察值取历史最大值，避免时钟倒退
放宽截止时间。

`assess()` 先检查当前状态和 deadline，再计算候选快照：等于上限允许；超过任一上限
返回明确维度并在新工作开始前终止为 `limit_exceeded`。相同 charge 重放不重复累计，
同 ID 不同内容冲突。若相同 charge 通过新的 command_id 重放，调用方必须使用当前
expected_version；仓储原子保存零事件、零版本变化的收据。过期版本先返回冲突，调用方
可用同一 command_id 和更新版本重试。该内核不预留/结算 Provider 金额，后续 Runtime 必须在开始工作前
用最保守候选消耗执行检查，并按实际结果提交确认消耗。

成本仅接受有限非负 `Decimal`，禁止 float；比较和指纹使用不丢失精度的规范十进制文本，
负零归一为零。本阶段不根据币种最小单位量化金额，因为没有受控币种精度数据源。

### 5. 每个成功命令最多一个稳定事件

事件 envelope 固定 `event_id/tenant_id/run_id/sequence/type/occurred_at/
payload_version/payload`。创建、非终态转换、预算消耗和四类终态使用可区分事件类型；
终态事件引用唯一 RunResult 版本。载荷采用深度冻结的 JSON 值允许列表，只包含状态、
版本、预算维度、引用与安全错误码，不包含完整答案、Prompt、凭据、Provider 正文或任意
第三方对象。重复与失败命令新增事件数为零。

### 6. RunResult 在进入终态时冻结

四个终态都生成一个 `RunResult`。结果包含基础版本、Run/AgentVersion、状态、可选答案、
引用、警告、BudgetSnapshot、可选安全错误和关联标识。成功不得携带失败错误；失败、
取消和超限使用稳定错误码。终态后不允许补写结果或用量；调用方必须在终态命令中提交
所有已确认信息，未知数据保持未知而非猜测。

### 7. 时间与标识全部注入

application 使用 `Clock` 和 `IdentifierGenerator` Protocol；生产实现留给基础设施切片，
当前测试使用可控 UTC 时钟与确定性序列标识。所有传入时间必须带 UTC 时区，Run 保存
`last_observed_at` 并只向前推进。这样 deadline、事件顺序和命令重放可以稳定测试。

## Failure, Security, Rollback, and Operations

- 错误分类固定为 `invalid_input/not_found/illegal_transition/version_conflict/
  idempotency_conflict/budget_limit/invariant_violation/cancelled/failed`，公开消息使用静态安全文本；内部
  输入值和异常正文不拼接到 `str/repr`。
- 任一 commit 失败都不得留下 Run、事件或收据的部分更新；内存适配器使用一次锁内的
  copy-validate-swap，未来数据库适配器必须以一个短事务和约束实现同一契约。
- 跨租户 load/command/event read 统一表现为 `not_found`；仓储键同时包含 tenant_id。
- 不记录日志正文；本切片以领域事件和稳定错误作为可观测边界。后续日志/Trace 适配器
  只能读取这些安全字段。
- 无迁移、网络、Tool 或外部状态。回滚为移除新增 domain/application/adapter 文件和
  对应文档状态更新；保留 003 Model Gateway 不变。
- `doc/功能文档.md` 与 `doc/技术方案.md` 将同步已实现边界、完整取消/超限转换和后续
  PostgreSQL/Worker 范围；`README.md`、`doc/PROJECT.md` 将从“首个 M0 切片”更新为两个
  已完成基础切片，但不会宣称 M0 Runtime 完成。

## Complexity Tracking

无宪法违规，不需要例外说明。
