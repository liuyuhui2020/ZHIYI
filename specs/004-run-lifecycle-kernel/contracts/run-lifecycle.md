# Contract: Run Lifecycle Application Boundary

## Purpose

本契约定义后续 Worker、API 和持久化适配器可依赖的 application/domain 边界。它是
Python library 契约，不是 REST 或 SSE 契约。

## Common Command Envelope

每个 mutation command 必须具有：

```text
tenant_id: TenantId
command_id: CommandId
expected_version: integer (create 使用 0)
observed_at: 由应用服务 Clock 提供，不接受调用方伪造
```

目标 Run 命令还包含 `run_id`。命令类型、tenant、目标和业务 payload 形成 intent
fingerprint；command_id、expected_version、服务生成时间和服务生成标识不参与指纹。
因此版本冲突后可以使用同一 command_id 和更新后的 expected_version 重试同一意图。
命令对象自身不可变，未知字段或外层框架对象不得进入领域边界。

## Use Cases

### create_run

Input:

- tenant_id, command_id, task_id
- AgentVersionRef
- effective RunBudget
- optional caller-provided correlation_id

Behavior:

- 分配 RunId/EventId，创建版本 1 的 queued Run。
- 原子写入 Run、`run.created` 和 CommandReceipt。
- 同命令同意图重放原 receipt；同命令不同意图冲突。

### start_run

Input: tenant_id, command_id, run_id, expected_version

Behavior: `queued → running`，产生 `run.started`。

### wait_for_approval / wait_for_resolution

Input: tenant_id, command_id, run_id, expected_version, safe reference id

Behavior: 只允许从 running 进入目标等待状态；事件只保存引用，不保存审批或 Tool 正文。

### resume_run

Input: tenant_id, command_id, run_id, expected_version

Behavior: waiting_approval 或 waiting_resolution → running，产生 `run.resumed`。

### consume_budget

Input: tenant_id, command_id, run_id, expected_version, BudgetCharge

Behavior:

- 只允许 running；先检查 deadline 和候选总量。
- 相同 charge_id/内容幂等，不重复累计。
- 使用新 command_id 重放相同 charge 时仍须匹配当前 expected_version；成功时只原子写入零事件的 CommandReceipt，Run 版本和用量保持不变。过期版本返回 version_conflict，随后可用同一 command_id 和更新版本重试。
- 同 charge_id/不同内容返回 idempotency_conflict。
- 候选等于上限时提交 usage 和 `run.budget_consumed`。
- 候选超过上限或 deadline 已到时，不提交候选 charge，转为 limit_exceeded 并生成唯一终态结果/事件。

### cancel_run

Input: tenant_id, command_id, run_id, expected_version, optional correlation_id

Behavior: 任一非终态 → cancelled；终态拒绝；生成唯一 cancelled RunResult 和
`run.cancelled`。

### succeed_run

Input: tenant_id, command_id, run_id, expected_version, safe result draft

Behavior: 只允许 running → succeeded；结果 draft 不允许错误，不允许隐藏推理或任意对象。

### fail_run

Input: tenant_id, command_id, run_id, expected_version, SafeRunError, safe result references

Behavior: 只允许 running → failed；生成唯一 failed RunResult 和 `run.failed`。

### enforce_deadline

Input: tenant_id, command_id, run_id, expected_version

Behavior:

- 任一非终态达到 deadline：转为 limit_exceeded。
- 未到 deadline：返回 allowed decision，不改变 Run、事件或版本；不写 mutation receipt。
- 终态：返回当前终态 decision，不改变任何状态。

### get_run / list_events

Input: tenant_id + run_id；list_events 另带非负 `after_sequence` 与 `1 <= limit <= 1000`，默认 limit 为 100。

Behavior: 只返回同租户记录；不存在或跨租户均返回相同 not_found。事件按 sequence 升序，
本契约不提供流式连接。

## Command Outcome

每个成功 mutation 返回：

```text
receipt: CommandReceipt
replayed: boolean
events: tuple[RunEvent, ...]  # 第一次提交生成的稳定事件；重放返回相同事件
```

查询使用 `get_run` 获取最新不可变 Run。CommandReceipt 不携带完整答案或错误正文。

## RunRepository Port

端口必须支持以下语义：

```text
load(tenant_id, run_id) -> Run | None
list_events(tenant_id, run_id, after_sequence, limit) -> tuple[RunEvent, ...]
find_command(tenant_id, command_id, intent_fingerprint) -> CommitOutcome | None
commit(expected_version, updated_run, new_events, receipt) -> CommitOutcome
```

`find_command` 在同租户同指纹时返回原收据和事件，不同指纹时返回
idempotency_conflict；不存在时返回 None。应用服务必须在加载/转换 Run 前调用它，使
终态命令重放不会被当前终态抢先拒绝。`commit` 仍必须原子执行，并再次按“同命令
replay/冲突 → expected_version → invariant 校验 → 写入”的顺序工作，以关闭并发窗口。
adapter 不得要求 application/domain 传入 ORM session 或数据库类型。

## Stable Error Contract

| Error code | Meaning | Retry guidance |
|---|---|---|
| invalid_input | 命令或值对象无效 | 修正输入，不原样重试 |
| not_found | 资源不存在或租户不可见 | 不推断资源存在性 |
| illegal_transition | 当前状态不允许该行为 | 读取最新状态后决定 |
| version_conflict | 新命令的 expected_version 过期 | 读取最新状态并重新决策 |
| idempotency_conflict | 命令或 charge 标识被不同意图复用 | 使用新标识或修复调用方 |
| budget_limit | deadline 或硬预算阻止新工作 | Run 已终止，不重试工作 |
| invariant_violation | 持久化或领域不变量破坏 | 平台故障，人工诊断 |
| cancelled | Run 被授权命令取消 | Run 已终止，不启动新工作 |
| failed | Run 发生不可恢复失败 | Run 已终止，按 correlation_id 诊断 |

异常文本只使用安全模板和 correlation_id，不回显命令字段、Prompt、凭据或下游错误
正文。RunResult 可以包含显式提交并已由上游策略批准的最终用户答案，但不得携带其余
原始 Provider 响应。

## Compatibility Rules

- RunStatus、RunEventType、RunErrorCode 和 payload/result version 是版本化契约。
- 新事件 payload 可添加向前兼容字段，但不能改变已有字段语义。
- 生产 PostgreSQL adapter 必须通过与内存 adapter 相同的仓储契约测试。
- REST/SSE adapter 必须转换这些契约，不得直接序列化任意 domain `__dict__`。
