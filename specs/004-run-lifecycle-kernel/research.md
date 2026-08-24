# Research: Run Lifecycle Kernel

## Decision 1: Domain-first immutable aggregate

**Decision**: Run、预算、事件和结果使用标准库冻结 dataclass；转换返回新快照，不允许
外部设置状态字符串。

**Rationale**: 不可变对象使非法部分更新、跨协程共享和回滚更容易推理，也能确保外层
ORM、Graph 或 API 不反向定义产品状态机。

**Alternatives considered**:

- 可变 ORM 实体直接承载状态机：拒绝，因为 domain 会依赖数据库实现且并发行为难测。
- 状态字符串由 service 任意更新：拒绝，因为合法转换无法集中审计。

## Decision 2: Expected version plus durable command receipt

**Decision**: 新命令使用 expected_version 防丢失更新；重试使用 tenant-scoped command_id
和规范化意图指纹重放原收据。原子提交先判重，再判版本。

**Rationale**: 仅靠版本会把丢失响应后的安全重试误判为冲突；仅靠幂等键又不能解决
两个不同命令同时修改同一 Run。组合后既支持至少一次投递，也保持单一获胜者。

**Alternatives considered**:

- 只使用进程锁：拒绝，因为未来多 Worker/多进程不可用。
- 把命令结果缓存为 TTL 内存：拒绝，因为恢复后丢失且不能成为持久化契约。
- 指纹包含完整序列化正文：拒绝，因为会复制答案或错误敏感数据。

## Decision 3: Atomic repository commit contract

**Decision**: 仓储端口以一个操作提交新 Run、追加事件和 CommandReceipt；重复命令可从
同一端口得到 replay outcome。

**Rationale**: Run 已变化但事件/收据未写入会破坏恢复、SSE 和重试语义。端口先定义
原子边界，可让后续 PostgreSQL 用短事务、唯一约束和 CAS 实现而无需修改 domain。

**Alternatives considered**:

- Run、事件、收据三个独立 save：拒绝，因为进程在中间失败会形成不可判定状态。
- 在 application 依次补偿：拒绝，因为补偿无法可靠恢复并发提交顺序。

## Decision 4: One event per successful mutating command

**Decision**: 创建、非终态转换、预算消耗和终态各生成一个可区分事件；失败、冲突与
幂等 replay 不生成事件。sequence 从 1 连续增长。

**Rationale**: 事件数量与成功命令一一对应，便于去重、审计和后续 SSE 续传，不需要
在本切片引入事件总线或投影框架。

**Alternatives considered**:

- 每次命令生成多条细粒度事件：暂不采用，会增加原子提交和客户端顺序复杂度。
- 从日志重建事件：拒绝，日志不是业务事实源。

## Decision 5: Explicit hard budget and confirmed usage

**Decision**: Budget 固定 deadline、步骤、模型/Tool 调用、输入/输出/总 Token 和 Decimal
成本上限；Snapshot 只保存已确认消耗；候选 charge 在开始工作前评估，等于上限允许，
超出则进入 `limit_exceeded`。

**Rationale**: 明确维度和边界能阻止无限 Loop；Decimal 避免二进制浮点成本误差；
confirmed-only 防止把估算冒充实际用量。

**Alternatives considered**:

- 浮点金额：拒绝，边界比较不可重复。
- 只在工作完成后检查：拒绝，可能先产生无法接受的费用或副作用。
- 允许普通命令动态提高上限：拒绝，破坏平台硬预算。

## Decision 6: UTC deadline with monotonic observation

**Decision**: 持久化 deadline 和 occurred_at 使用 UTC aware datetime；Run 保存历史最大
观察时间，新的较早时钟值不会降低已观察运行时长。

**Rationale**: 单调进程时钟不能跨 Worker/重启持久化，而纯墙钟可能倒退。UTC deadline
加最大观察值在持久化边界可重放，并能防止时间倒退重新开放已到期 Run。

**Alternatives considered**:

- 只保存 monotonic float：拒绝，跨进程和重启无意义。
- 直接信任每次系统墙钟：拒绝，NTP/人工调整可能放宽 deadline。

## Decision 7: Safe versioned JSON event payload

**Decision**: 事件 envelope 稳定，payload 递归限制为 JSON 标量/对象/数组并深度冻结；
每类事件使用 payload_version=1 和允许字段。

**Rationale**: 后续 PostgreSQL JSONB 和 SSE 容易投影，同时阻止 ORM、Provider、Graph
和可变对象泄漏到公共边界。

**Alternatives considered**:

- 事件直接保存领域对象：拒绝，序列化和兼容性不可控。
- 任意 `dict[str, Any]`：拒绝，无法静态限制或防止敏感/第三方对象进入。

## Decision 8: No new dependency

**Decision**: 本 Feature 不新增第三方包；领域校验、Decimal、hash、Protocol 和 async
内存锁均使用 Python 3.12 标准库。

**Rationale**: 当前功能不需要 ORM、Web 或验证框架。保持小依赖面符合可逆性和领域
纯净约束，也不改变 `uv.lock`。

**Alternatives considered**:

- 使用 Pydantic 定义 domain：拒绝，宪法禁止领域依赖外层验证框架。
- 提前引入 SQLAlchemy/Alembic：拒绝，生产持久化是下一独立 Feature。

## Dependency and License Review

没有新增依赖、许可证或供应链变化。继续使用 003 已锁定的开发工具执行测试、类型和
格式门禁；`uv.lock` 预期保持不变。
