# Feature Specification: Run Lifecycle Kernel

**Feature Branch**: `codex/004-run-lifecycle-kernel`

**Created**: 2026-08-24

**Status**: Ready for Planning

**Input**: User description: "按照建议实现 Agent/Run 领域内核，先稳定状态机、幂等、预算、取消、事件和结果契约，不提前接入数据库、Worker、LangGraph 或 API。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 创建并推进可解释的 Run (Priority: P1)

作为 Runtime 开发者，我希望创建绑定不可变 AgentVersion 的 Run，并且只能沿平台
认可的状态转换推进，从而让后续 Worker、数据库和 API 共享同一套产品语义。

**Why this priority**: Run 状态是恢复、调度、事件和客户端结果的共同事实源；如果状态
语义不先稳定，任何外层实现都会固化不一致行为。

**Independent Test**: 在无数据库、无网络和无图执行框架的环境中创建 Run，依次执行
所有合法与非法转换，验证版本、终态、错误和不可变 AgentVersion 行为。

**Acceptance Scenarios**:

1. **Given** 有效租户、AgentVersion 引用和有效预算，**When** 创建 Run，**Then** Run 以 `queued` 状态、版本 1 和固定 AgentVersion 建立。
2. **Given** 一个 `queued` Run，**When** Runtime 开始执行，**Then** Run 转为 `running`，版本递增，并生成一次对应状态事件。
3. **Given** 一个 `running` Run，**When** Runtime 请求等待审批或人工处理，**Then** Run 只进入相应的非终态，且后续仍具有恢复、取消或终止路径。
4. **Given** 任一非法状态转换，**When** 调用方提交命令，**Then** Run 保持原状态和版本，并返回稳定冲突错误。
5. **Given** 任一终态 Run，**When** 再提交改变状态的命令，**Then** 命令被拒绝，终态、结果和事件保持不变。

---

### User Story 2 - 安全处理重复与并发命令 (Priority: P1)

作为 Runtime 维护者，我希望重复命令返回既有结果，并发冲突基于预期版本被明确拒绝，
从而避免重试、重复投递或多执行者竞争破坏 Run 状态。

**Why this priority**: 幂等和并发控制是后续 PostgreSQL 租约、Worker 恢复和 REST 命令
正确性的前置条件。

**Independent Test**: 使用内存中的持久化边界重复和并发提交相同及不同命令，验证每个
命令最多产生一次状态变化和一次事件，冲突不产生部分写入。

**Acceptance Scenarios**:

1. **Given** 一个已经成功处理的命令，**When** 使用相同命令标识和相同意图再次提交，**Then** 返回第一次的确定性结果，不增加版本或事件。
2. **Given** 一个已经使用的命令标识，**When** 以不同意图复用该标识，**Then** 返回稳定幂等冲突，Run 不发生变化。
3. **Given** 两个调用方基于同一预期版本提交不同命令，**When** 它们并发执行，**Then** 至多一个命令提交成功，另一个得到版本冲突。
4. **Given** 调用方使用另一租户身份访问 Run，**When** 查询或提交命令，**Then** 返回不泄露资源存在性的结果。

---

### User Story 3 - 通过硬预算和取消保证 Run 可终止 (Priority: P1)

作为平台运维人员，我希望 Run 的时间、步骤、模型调用、Tool 调用、Token 和成本预算
只能消耗不能放宽，并且所有非终态 Run 都可取消，从而阻止无限循环和永久运行。

**Why this priority**: 明确终止边界直接控制成本、恢复复杂度和故障影响范围，是 M0
退出标准中的硬门禁。

**Independent Test**: 使用可控时钟和确定性预算输入覆盖恰好到达上限、超过上限、重复
记账、过期和取消场景，验证终态与预算快照一致。

**Acceptance Scenarios**:

1. **Given** Run 的某项累计消耗仍在硬上限内，**When** 记录一次唯一消耗，**Then** 预算快照单调增加且 Run 可继续。
2. **Given** 新消耗会超过任一硬上限，**When** Runtime 尝试记录消耗，**Then** 不启动新的受预算工作，Run 进入 `limit_exceeded` 并生成终态结果。
3. **Given** 相同消耗标识被重复提交，**When** 再次记账，**Then** 已记录消耗不重复累计。
4. **Given** 任一非终态 Run，**When** 收到有效取消命令，**Then** Run 进入 `cancelled` 并生成终态结果；后续执行命令不能重新启动它。
5. **Given** Run 到达时间截止点，**When** 检查执行许可，**Then** Run 进入 `limit_exceeded`，不再允许开始新步骤。

---

### User Story 4 - 输出稳定且安全的事件与结果 (Priority: P2)

作为后续 API、Worker 和观测组件的开发者，我希望每次有效生命周期变化生成有序、
可版本化、无供应商对象的 RunEvent，并且每个终态具有稳定 RunResult，从而能够安全
持久化、回放和转换对外协议。

**Why this priority**: 事件和结果是外层组件集成所需的稳定边界，但它们必须建立在已
正确的状态机、并发和预算语义之上。

**Independent Test**: 对完整生命周期、失败、取消、超限、重复命令和敏感错误样例进行
离线回放，验证事件序号、结果字段、版本和脱敏规则。

**Acceptance Scenarios**:

1. **Given** Run 完成一次有效状态变化，**When** 读取新事件，**Then** 事件具有稳定标识、Run 内连续递增序号、类型、发生时间、载荷版本和最小安全载荷。
2. **Given** 重复命令或失败的并发命令，**When** 读取事件，**Then** 不产生重复或伪造的状态事件。
3. **Given** Run 进入成功、失败、取消或超限终态，**When** 读取 RunResult，**Then** 基础字段完整、状态一致，并固定 AgentVersion 与累计用量。
4. **Given** 错误包含密钥、完整 Prompt、Provider 原始正文或隐藏推理，**When** 生成错误、事件或结果，**Then** 这些敏感来源不会进入公开字段或可打印表示；RunResult 只允许显式提交且已通过上游策略的最终用户答案。

### Edge Cases

- 空白或超长标识、非法时间、非有限金额、负数预算和负数消耗在创建或记账前被拒绝。
- 有效上限可以恰好被消耗完；只有下一项工作需要超出上限或时间到达截止点时才进入 `limit_exceeded`。
- 同一命令标识在不同租户或不同 Run 下不共享幂等结果。
- 同一消耗标识重复出现但维度或数值不同，必须作为冲突拒绝，不能静默覆盖。
- `waiting_approval` 与 `waiting_resolution` 都可以恢复到 `running`，也可以取消；等待状态本身不占用新的执行预算。
- 失败、取消和超限发生时已有累计用量保留，尚未确认的消耗不得伪造成已完成用量。
- 时钟倒退不能减少已观察的运行时长或重新开放已经触发的截止时间。
- 事件序号溢出、重复事件标识或不支持的载荷版本必须明确失败，不能生成不可回放事件。
- 空答案可以是合法成功结果，但成功结果不得携带失败错误；失败、取消和超限结果不得伪装为成功答案。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 平台 MUST 创建具有稳定 `tenant_id`、`run_id`、`task_id`、不可变 AgentVersion 引用、有效预算、创建时间、状态和版本的 Run。
- **FR-002**: Run MUST 在创建后固定 AgentVersion；任何命令均不得替换其 Agent、版本或构建摘要。
- **FR-003**: 平台 MUST 只支持 `queued`、`running`、`waiting_approval`、`waiting_resolution`、`succeeded`、`failed`、`cancelled` 和 `limit_exceeded` 八种 Run 状态。
- **FR-004**: 平台 MUST 集中定义状态转换；正常执行仅允许 `queued → running|cancelled|limit_exceeded`、`running → waiting_approval|waiting_resolution|succeeded|failed|cancelled|limit_exceeded`、`waiting_approval|waiting_resolution → running|cancelled|limit_exceeded`。
- **FR-005**: 每个非终态 Run MUST 能够被取消；`succeeded`、`failed`、`cancelled` 和 `limit_exceeded` MUST 是不可逆终态。
- **FR-006**: 每个改变 Run 的命令 MUST 携带稳定命令标识和预期 Run 版本；成功变化 MUST 原子递增版本。
- **FR-007**: 使用相同命令标识和相同规范化意图的重试 MUST 返回第一次的确定性结果，不得重复改变 Run、预算、结果或事件；规范化意图 MUST 排除预期版本及服务生成的时间和标识，使版本冲突后的同一业务意图可以使用原命令标识和更新版本安全重试。
- **FR-008**: 使用相同命令标识但不同意图的请求 MUST 返回稳定幂等冲突；使用过期预期版本的新命令 MUST 返回版本冲突。
- **FR-009**: 并发命令 MUST 保证单一获胜者和无部分更新；失败命令不得留下状态、预算、事件或幂等记录的部分变化。
- **FR-010**: 所有查询和命令 MUST 显式使用租户边界；跨租户访问 MUST 返回不泄露资源是否存在的结果。
- **FR-011**: Run MUST 持有已经合并平台、租户和 AgentVersion 限制的有效硬预算；普通命令不得提高任何既有上限。
- **FR-012**: 预算 MUST 至少覆盖运行时间、步骤、模型调用、Tool 调用、输入 Token、输出 Token、总 Token 和成本；计数与金额 MUST 非负、有限且单调不减。
- **FR-013**: 每笔预算消耗 MUST 具有 Run 内稳定消耗标识；相同标识和相同内容的重试幂等，相同标识的不同内容产生冲突。新命令标识重放相同消耗时仍 MUST 校验当前预期版本；成功只保存零事件收据且不改变 Run 版本或用量，过期版本可使用原命令标识和更新版本重试。
- **FR-014**: 平台 MUST 允许累计值恰好等于上限；如果新工作会超过任一上限，MUST 在该工作开始前将 Run 终止为 `limit_exceeded`。
- **FR-015**: 平台 MUST 使用单调时间语义检查运行截止点；到达或超过截止点后不得开始新工作，并 MUST 进入 `limit_exceeded`。
- **FR-016**: 成功、失败、取消和超限终态 MUST 各自生成且只生成一个与终态一致的稳定 RunResult。
- **FR-017**: RunResult MUST 至少包含结果版本、终态、Run 与 AgentVersion 标识、可选答案、警告、Citation/Artifact/Approval 引用、累计用量、安全错误和关联标识；业务扩展不得删除基础字段。
- **FR-018**: 每次成功的生命周期变化 MUST 生成一个 RunEvent；创建、预算消耗和终态结果也 MUST 产生可区分的事件类型。
- **FR-019**: RunEvent MUST 包含稳定事件标识、租户、Run、Run 内单调连续序号、事件类型、发生时间、载荷版本和不可变安全载荷；事件读取 MUST 按序返回，默认最多 100 条，单次 `limit` MUST 限制在 1 至 1,000 条。
- **FR-020**: 重复命令、版本冲突、非法转换和校验失败 MUST NOT 生成新的领域事件。
- **FR-021**: 公开错误 MUST 使用稳定错误码和安全消息；事件、结果、异常及其可打印表示 MUST NOT 包含密钥、认证信息、完整 Prompt、未批准的原始模型输出、Provider 原始正文或原始 Chain-of-Thought。RunResult MAY 包含调用方显式提交且已通过上游策略的最终用户答案，但不得保留该答案之外的原始 Provider 响应。
- **FR-022**: 领域语义 MUST 与具体数据库、ORM、Web、模型 Provider、LangChain 和 LangGraph 类型解耦，并能通过可替换持久化边界执行。
- **FR-023**: 平台 MUST 提供无需数据库、网络、Provider 凭据或图执行框架的确定性离线验证方式，覆盖状态、版本、幂等、并发、预算、取消、租户隔离、事件、结果和脱敏。
- **FR-024**: 本 Feature MUST 只交付 Run/AgentVersion 最小领域对象、生命周期命令、预算、事件、结果、错误、持久化端口及内存验证适配，不包含 PostgreSQL Schema/迁移、租约、Worker、Reconciler、LangGraph、REST/SSE、AgentSpec 编译、Approval/Tool 执行、Context、Memory、RAG 或 Langfuse。
- **FR-025**: 本 Feature MUST NOT 执行网络、文件、数据库或 Tool 副作用；回滚不得涉及迁移或外部状态恢复。

### Key Entities

- **AgentVersion Reference**: Run 固定的最小不可变版本身份，包含租户、Agent、版本和可复现构建摘要，不包含运行连接或密钥。
- **Run**: 一次异步执行的领域聚合，拥有稳定身份、固定 AgentVersion、生命周期状态、版本、预算、事件序号和可选终态结果。
- **Run Command**: 一次具有租户、命令标识、目标 Run、预期版本和规范化意图的状态变更请求。
- **Command Receipt**: 已处理命令的不可变结果摘要，用于重复命令返回相同结果并检测命令标识冲突。
- **Run Budget**: Run 的不可放宽硬上限，包括运行截止、步骤、模型/Tool 调用、Token 和成本限制。
- **Budget Snapshot**: 截至当前版本已经确认的累计消耗；每项消耗通过稳定标识去重。
- **Run Event**: Run 内有序、持久化友好的领域事实，使用稳定 envelope 和版本化安全载荷。
- **Run Result**: 每个终态 Run 唯一的结构化结果，保留 AgentVersion、累计用量、引用、警告和安全错误。
- **Run Error**: 面向调用方的稳定错误分类，区分无效输入、未找到、非法转换、版本冲突、幂等冲突、预算超限和内部不变量破坏。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 状态转换矩阵中的全部合法路径和全部非法状态组合均有自动验收，结果与规格 100% 一致。
- **SC-002**: 对每种终态执行重复命令和后续变更命令时，终态、版本、结果和事件保持 100% 不变。
- **SC-003**: 在至少 1,000 组确定性并发竞争中，每组至多一个不同命令成功，Run 不出现丢失更新、重复事件或部分状态。
- **SC-004**: 对相同命令和相同预算消耗各重复提交至少 100 次，只产生一次状态或消耗变化；冲突复用的拒绝率为 100%。
- **SC-005**: 对时间、步骤、模型、Tool、输入/输出/总 Token 和成本边界的低于、等于与超过上限样例，终止判定 100% 符合规格。
- **SC-006**: 所有非终态的取消验收均在一次命令内进入 `cancelled`；取消后开始新工作的成功数为 0。
- **SC-007**: 每个完整生命周期的事件序号从 1 连续递增，无重复、无缺口；重复和冲突命令新增事件数为 0。
- **SC-008**: 成功、失败、取消和超限样例全部生成且只生成一个与终态、AgentVersion 和累计用量一致的 RunResult。
- **SC-009**: 使用已知假密钥、敏感 Prompt、未批准原始模型输出、Provider 错误正文和隐藏推理标记执行全部成功与失败验收时，除测试显式标记为已批准最终答案的内容外，公开值、异常文本、事件、结果和可打印表示中的泄漏数为 0。
- **SC-010**: 默认完整验证在无数据库、无网络、无 Provider 凭据和无 LangGraph 条件下可重复通过，领域包不依赖外层框架类型。
- **SC-011**: 排除测试调度与持久化等待后，10,000 次本地状态转换的额外处理延迟 p95 不超过 1 毫秒。
- **SC-012**: 新增后续 PostgreSQL 持久化实现时，不需要修改 Run 状态、预算、事件或结果领域契约，且当前离线契约验收继续全部通过。

## Assumptions

- 本 Feature 的调用者是后续 Runtime/Application 用例，不是最终用户；身份认证和角色授权将在 API Feature 中完成，本 Feature 只强制租户作用域。
- AgentVersion 的完整发布、能力校验和 Graph 编译属于后续 Feature；本阶段只保存经过上游验证的不可变引用和构建摘要。
- 有效预算由平台、租户和 AgentVersion 限制在 Run 创建前合并，本 Feature 只接受最终硬上限并禁止放宽。
- 成本使用非负十进制定点值表达，币种由同一预算显式固定；本 Feature 不计算 Provider 单价。
- Citation、Artifact 和 Approval 的完整领域对象尚未实现；RunResult 只保存不泄露内容的稳定引用。
- 内存持久化实现只用于契约和并发验证，不代表生产持久化或队列语义。
- RunEvent 是后续持久化与 SSE 投影的输入，不等同于当前已实现 SSE 协议。
- 本 Feature 不调用 Model Gateway；后续标准 Graph/Worker Feature 将按这里的预算和生命周期契约集成模型调用。
