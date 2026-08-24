# Data Model: Run Lifecycle Kernel

本文定义领域和持久化端口的逻辑模型，不是数据库 Schema。

## Identifier Values

所有标识均为不可变、非空、去除首尾空白后不超过 128 字符的受控字符串。标识类型
至少包括 `TenantId`、`AgentId`、`AgentVersionId`、`TaskId`、`RunId`、`CommandId`、
`EventId`、`ChargeId`、`CorrelationId` 和 `ReferenceId`。不同类型不可混用。

## AgentVersionRef

| Field | Type | Rules |
|---|---|---|
| tenant_id | TenantId | 必须与 Run tenant 相同 |
| agent_id | AgentId | 创建后不可变 |
| version_id | AgentVersionId | 创建后不可变 |
| build_digest | string | 小写 `sha256:` 加 64 位十六进制摘要 |

该引用不包含 Prompt、Tool Schema、模型凭据、包内容或运行连接。

## RunStatus

| Status | Terminal | Allowed next states |
|---|---:|---|
| queued | No | running, cancelled, limit_exceeded |
| running | No | waiting_approval, waiting_resolution, succeeded, failed, cancelled, limit_exceeded |
| waiting_approval | No | running, cancelled, limit_exceeded |
| waiting_resolution | No | running, cancelled, limit_exceeded |
| succeeded | Yes | none |
| failed | Yes | none |
| cancelled | Yes | none |
| limit_exceeded | Yes | none |

## RunBudget

| Field | Type | Rules |
|---|---|---|
| deadline_at | UTC datetime | 晚于 Run created_at |
| max_steps | integer | `>= 0` |
| max_model_calls | integer | `>= 0` |
| max_tool_calls | integer | `>= 0` |
| max_input_tokens | integer | `>= 0` |
| max_output_tokens | integer | `>= 0` |
| max_total_tokens | integer | `>= 0`，不得小于任一单项 Token 上限 |
| max_cost | Decimal | 有限、`>= 0`；拒绝 float，不按币种精度量化 |
| currency | string | 3 位大写 ASCII 字母 |

所有字段是创建前已合并的硬上限。Run 内没有提高预算的转换。

## BudgetCharge

| Field | Type | Rules |
|---|---|---|
| charge_id | ChargeId | Run 内稳定去重键 |
| steps | integer | `>= 0` |
| model_calls | integer | `>= 0` |
| tool_calls | integer | `>= 0` |
| input_tokens | integer | `>= 0` |
| output_tokens | integer | `>= 0` |
| cost | Decimal | 有限、`>= 0` |

至少一个消耗维度大于零。`total_tokens` 从 input+output 派生。成本比较和指纹使用不
丢失精度的规范十进制文本，负零归一为零；本阶段没有币种精度数据源，因此不执行按
最小货币单位量化。规范化指纹包含全部维度和币种语义，但收据/错误不包含调用正文。

## BudgetSnapshot

| Field | Type | Rules |
|---|---|---|
| steps | integer | 单调不减 |
| model_calls | integer | 单调不减 |
| tool_calls | integer | 单调不减 |
| input_tokens | integer | 单调不减 |
| output_tokens | integer | 单调不减 |
| cost | Decimal | 单调不减 |
| charge_fingerprints | ordered tuple | charge_id 唯一；同 ID 不同指纹冲突 |

`total_tokens` 派生。候选 Snapshot 任一维度超过 RunBudget 时不会提交该 charge，Run
转入 `limit_exceeded` 并记录触发维度。

## SafeRunError

| Field | Type | Rules |
|---|---|---|
| code | RunErrorCode | 稳定枚举 |
| message | string | 静态安全消息，不包含输入值或下游正文 |
| correlation_id | CorrelationId | 可选；用于内部诊断关联 |
| limit_dimension | BudgetDimension | 仅预算超限时可选 |

错误码：`invalid_input`、`not_found`、`illegal_transition`、`version_conflict`、
`idempotency_conflict`、`budget_limit`、`invariant_violation`、`cancelled`、`failed`。

## RunResult

| Field | Type | Rules |
|---|---|---|
| result_version | integer | 本 Feature 固定为 1 |
| tenant_id/run_id | identifiers | 必须与 Run 相同 |
| agent_version | AgentVersionRef | 必须与 Run 固定版本相同 |
| status | terminal RunStatus | 与 Run 终态一致 |
| answer | optional string | 成功可为空；不保存隐藏推理 |
| warning_codes | tuple[string] | 去重、稳定排序或保留规范顺序 |
| citation_ids | tuple[ReferenceId] | 仅引用，不含正文 |
| artifact_ids | tuple[ReferenceId] | 仅引用，不含下载地址 |
| approval_ids | tuple[ReferenceId] | 仅引用，不含审批 payload |
| usage | BudgetSnapshot | 进入终态时冻结 |
| error | optional SafeRunError | succeeded 必须为空，其他终态必须存在 |
| correlation_id | optional CorrelationId | 安全关联标识 |

终态后 RunResult 不可替换或修改。

## RunEvent

| Field | Type | Rules |
|---|---|---|
| event_id | EventId | 全局稳定标识 |
| tenant_id/run_id | identifiers | 与 Run 相同 |
| sequence | integer | 从 1 开始，在 Run 内连续递增 |
| type | RunEventType | 稳定枚举 |
| occurred_at | UTC datetime | 不早于 Run last_observed_at |
| payload_version | integer | 当前为 1，必须为正数 |
| payload | frozen JSON object | 只允许事件类型定义的安全字段 |

事件类型：`run.created`、`run.started`、`run.waiting_approval`、
`run.waiting_resolution`、`run.resumed`、`run.budget_consumed`、`run.succeeded`、
`run.failed`、`run.cancelled`、`run.limit_exceeded`。

## Run Aggregate

| Field | Type | Rules |
|---|---|---|
| tenant_id/run_id/task_id | identifiers | 创建后不可变 |
| agent_version | AgentVersionRef | 创建后不可变 |
| status | RunStatus | 仅通过集中状态机变化 |
| version | integer | 创建为 1；成功 mutation 每次 +1 |
| budget | RunBudget | 创建后不可放宽或替换 |
| usage | BudgetSnapshot | 只通过预算消耗更新 |
| created_at/updated_at | UTC datetime | updated 不早于 created |
| last_observed_at | UTC datetime | 取历史最大观察值 |
| next_event_sequence | integer | 等于最后事件 sequence + 1 |
| result | optional RunResult | 仅终态存在且唯一 |

不在 Run 内保存命令收据正文、数据库租约、Worker、Graph Thread、Checkpoint、Provider
对象或 Tool 结果。

## CommandReceipt

| Field | Type | Rules |
|---|---|---|
| tenant_id/command_id/run_id | identifiers | tenant+command 唯一 |
| command_type | string | 稳定受控名称 |
| intent_fingerprint | string | `sha256:` 摘要；含业务意图，不含 command_id、expected_version、生成时间/标识或原始正文 |
| resulting_status | RunStatus | 第一次提交后的状态 |
| resulting_version | integer | 第一次提交后的版本 |
| event_ids | tuple[EventId] | 第一次提交产生的事件，可能为空 |
| created_at | UTC datetime | 第一次提交时间 |

重复相同意图返回同一 receipt；调用结果另带 `replayed=true`，但不修改 receipt。

## Repository Atomicity

一次 commit 的一致性单位是：

```text
expected Run version
  + replacement Run snapshot
  + append-only RunEvent(s)
  + unique CommandReceipt
```

四者要么全部成功，要么全部不变。对新 command_id 重放相同 charge 的合法无变化提交，
调用方仍须提供当前 expected_version；replacement Run 可与当前快照相同、事件为空，
但新的 CommandReceipt 仍必须原子写入。若 expected_version 过期，先返回版本冲突；因
版本不参与 intent_fingerprint，调用方可用同一 command_id 和更新版本安全重试。
跨租户 key 不共享记录。事件只能追加且必须与 Run 的版本、序号和状态一致。
