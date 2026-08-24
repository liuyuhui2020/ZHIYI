# Feature Specification: LLM Provider Gateway

**Feature Branch**: `codex/003-llm-provider`

**Created**: 2026-08-24

**Status**: Ready for Planning

**Input**: User description: "现在请实现 LLM PROVIDER"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 通过统一契约调用不同模型 (Priority: P1)

作为 Agent 开发者，我希望使用同一套平台消息、请求、响应和流式事件契约调用
OpenAI 或 Anthropic 模型，从而能够切换 Provider，而不让业务代码依赖供应商对象。

**Why this priority**: 稳定、可替换的模型边界是后续 Agent Runtime、Context 和
评测能力的前提，也是本功能最小可交付价值。

**Independent Test**: 使用两个 Provider 的离线协议桩分别执行相同的非流式和流式
对话，验证调用方收到结构一致、顺序一致且不含供应商原始对象的结果。

**Acceptance Scenarios**:

1. **Given** 已配置可用的 OpenAI 模型，**When** 开发者提交平台标准消息请求，**Then** 返回统一的文本响应、结束原因、模型标识、请求标识和用量。
2. **Given** 已配置可用的 Anthropic 模型，**When** 开发者提交相同语义的请求，**Then** 返回与 OpenAI 调用相同的平台响应契约。
3. **Given** 开发者请求流式输出，**When** Provider 连续返回内容和结束信息，**Then** 平台按原顺序输出统一增量并以一个明确终止事件结束。
4. **Given** Provider 返回其专有字段，**When** 平台完成映射，**Then** 业务调用方只能访问经允许的平台字段，不能获得原始响应对象。

---

### User Story 2 - 使用 Tool Calling 与结构化输出 (Priority: P1)

作为 Agent 开发者，我希望模型调用能够携带受控 Tool Schema 并请求结构化结果，
从而让 Runtime 在进入 Tool Policy 或业务处理前获得可验证、可移植的数据。

**Why this priority**: Tool Calling 和 Structured Output 是平台 Agent Loop 的核心输入，
如果仅支持纯文本，Provider 实现无法支撑既定产品范围。

**Independent Test**: 对两个 Provider 回放包含单个、多个和流式 Tool Call 的协议样例，
并对有效及无效结构化结果执行校验，验证平台输出一致且错误分类稳定。

**Acceptance Scenarios**:

1. **Given** 请求包含合法 Tool 描述，**When** 模型提出一个或多个 Tool Call，**Then** 每个调用均具有稳定调用标识、Tool 名称和完整参数，且顺序不丢失。
2. **Given** Tool Call 参数分散在多个流式增量中，**When** 流结束，**Then** 平台能够生成完整、可解析的 Tool Call，且不会重复增量。
3. **Given** 请求声明结构化结果约束，**When** 模型返回符合约束的数据，**Then** 调用方获得通过校验的平台结果。
4. **Given** 模型返回不符合约束的数据，**When** 校验失败，**Then** 平台返回明确的可预算错误，不把未验证数据伪装为成功结果。

---

### User Story 3 - 在故障下受控重试与降级 (Priority: P2)

作为 Runtime 维护者，我希望模型调用具有总超时、流式空闲超时、有限重试、熔断
和能力兼容的 Fallback，从而避免瞬时故障导致无界等待或错误切换。

**Why this priority**: 第三方模型故障不可避免；错误的重试和降级会放大延迟、成本
和重复调用风险。

**Independent Test**: 通过可编程离线 Provider 依次注入超时、限流、临时不可用、
鉴权失败、取消和流式中断，验证每类故障的重试次数、Fallback 选择与终止错误。

**Acceptance Scenarios**:

1. **Given** 首选模型发生可重试的瞬时故障，**When** 重试预算尚未耗尽，**Then** 平台只在配置上限内退避重试并记录每次尝试。
2. **Given** 同模型重试耗尽且存在能力兼容的候选模型，**When** 错误允许降级，**Then** 平台切换到候选模型并记录原因和累计用量。
3. **Given** 错误属于鉴权失败、无效请求、内容策略拒绝或调用方取消，**When** 调用失败，**Then** 平台不重试、不 Fallback，并返回稳定错误分类。
4. **Given** 只有能力不兼容的 Fallback，**When** 首选模型失败，**Then** 平台拒绝静默降级并返回原始平台错误及兼容性诊断。
5. **Given** 调用超过总超时或流式空闲超时，**When** 限制触发，**Then** 平台取消底层工作并在有限时间内结束调用。

---

### User Story 4 - 安全配置与可观测用量 (Priority: P2)

作为平台运维人员，我希望 Provider 凭据始终通过密钥引用获取，错误和日志经过脱敏，
同时每次调用都提供可归因的用量和尝试摘要，从而安全地排障并管理模型成本。

**Why this priority**: Provider 密钥和完整请求内容属于高风险数据；缺少安全边界或
用量记录会使实现无法进入真实环境。

**Independent Test**: 使用包含可识别假密钥和敏感请求内容的失败样例，检查公开错误、
日志字段和调用记录，同时验证成功、重试及 Fallback 场景的 Token 汇总。

**Acceptance Scenarios**:

1. **Given** Agent 模型配置使用密钥引用，**When** Provider 被创建并调用，**Then** 密钥只在调用边界解析，不进入请求契约、结果、事件或可打印配置。
2. **Given** Provider 返回包含请求正文、认证信息或内部细节的错误，**When** 平台映射错误，**Then** 对外错误只包含安全消息、稳定错误码和关联标识。
3. **Given** 调用经历重试或 Fallback，**When** 调用终止，**Then** 用量摘要区分每次尝试并给出累计输入、输出及总 Token。
4. **Given** Provider 未返回可计费用量或金额，**When** 平台生成结果，**Then** 明确标记该数据不可用，不使用猜测值冒充实际成本。

### Edge Cases

- 空消息列表、空文本、未知角色、重复 Tool Call ID 或无效 Tool Schema 在发起网络调用前被拒绝。
- 消息包含非 BMP 字符、空白文本、超长内容或多模态引用时，映射保持内容与顺序，不隐式扩大权限或读取本地资源。
- 流式响应只有结束原因没有文本、先返回用量后返回结束、或连接在半个 Tool Call 参数处中断时，终止状态仍明确且不产生伪造的完整调用。
- Provider 返回未知结束原因、未知错误类型或缺失模型/请求标识时，平台采用向前兼容的安全默认值并保留诊断关联。
- 调用方在重试退避、等待首个流式块或处理流式块期间取消或提前关闭流时，后续重试和 Fallback 均停止；提前关闭时底层迭代器必须被取消和释放，但调用方不会再收到 terminal。
- 多个并发调用使用不同租户或密钥引用时，不共享凭据、请求数据、重试状态或熔断状态以外的可变调用数据。
- Fallback 链包含重复模型、首选模型自身、空候选或循环引用时，在调用前拒绝配置。
- 熔断器半开探测并发到达时，只允许受控数量的探测，不形成请求风暴。
- Structured Output 校验失败的修复尝试受独立次数和总调用预算共同限制。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 平台 MUST 提供 Provider 无关的请求、消息、响应、流式增量、Tool Call、用量、能力和错误契约，且这些契约不得暴露供应商原始对象。
- **FR-002**: 平台 MUST 正式支持 OpenAI 和 Anthropic 两个 Provider，并允许调用方通过稳定模型引用显式选择首选模型及有序 Fallback。
- **FR-003**: 平台 MUST 支持系统、用户、助手和 Tool 结果消息，并保持消息及内容块的原始顺序。
- **FR-004**: 平台 MUST 同时支持非流式和流式调用；每个流必须只有一个明确终止结果，增量不得重复或乱序。
- **FR-005**: 平台 MUST 将单个、多个和流式 Tool Call 映射为稳定的平台结构，并在暴露给调用方前验证名称、标识和参数完整性。
- **FR-006**: 平台 MUST 支持调用方声明结构化结果约束，对结果执行校验，并将校验失败作为受预算限制的类型化错误处理。
- **FR-007**: 每个模型 MUST 具有显式能力档案，至少覆盖文本、Tool Calling、Structured Output、支持的多模态输入、最大上下文、最大输出和用量可用性。
- **FR-008**: 平台 MUST 在发起 Provider 调用前使用明确的保守 Token 估算契约校验请求所需能力、上下文/输出 Token 限制和 Fallback 兼容性；估算不得低估待发送内容，不兼容时不得静默降级。
- **FR-009**: 每次逻辑调用 MUST 具有独立于单目标尝试超时的 route 级总 deadline，覆盖限流等待、密钥解析、全部重试、退避、Fallback 和流消费；流式调用还 MUST 具有可独立配置的首块和空闲超时；调用方取消 MUST 传播到底层调用并停止后续尝试。
- **FR-010**: 平台 MUST 只对限流、超时和明确的临时 Provider 故障执行带退避与抖动的有限重试；无效请求、鉴权、权限、内容策略、结构化校验和取消不得按传输故障重试。
- **FR-011**: Fallback MUST 只在错误允许且候选模型满足全部请求能力时发生；平台 MUST 先完成同模型有限重试，再按配置顺序尝试候选模型。
- **FR-012**: 平台 MUST 为每个 Provider 和模型隔离熔断状态，在连续临时故障后快速失败，并通过受控半开探测恢复。
- **FR-013**: 平台 MUST 将 Provider 故障映射为稳定、可判定重试性的错误分类，同时保留内部关联标识并移除密钥、认证头、完整请求和 Provider 原始正文。
- **FR-014**: 每次调用 MUST 记录首选与实际 Provider、模型、Provider 请求标识、各次尝试、延迟、结束原因、Fallback 原因以及输入、输出和总 Token；金额不可得时 MUST 明确标记未知。
- **FR-015**: Provider 凭据 MUST 通过密钥引用在调用边界按需解析，不得进入模型配置快照、平台消息、错误、事件、日志或可打印对象。
- **FR-016**: 平台 MUST 提供确定性离线 Fake Provider 和两个真实 Provider 的离线契约测试能力，默认测试与本地验证不得要求真实账号、密钥或付费网络调用。
- **FR-017**: 平台 MUST 允许显式启用真实 Provider 冒烟测试，但必须默认关闭、设置硬调用上限和超时，并避免输出请求正文或凭据。
- **FR-018**: 并发调用 MUST 隔离消息、凭据引用、重试计数、流式组装和用量，且共享的限流/熔断状态必须以 Provider 与模型为明确作用域。
- **FR-019**: 公开错误和观测摘要 MUST 不包含原始 Chain-of-Thought、隐藏推理内容或未脱敏的完整 Prompt/输出。
- **FR-020**: 本 Feature MUST 只交付模型契约、Gateway、Provider 适配和配置装配，不包含 Run 数据库、REST/SSE、Graph、Context Engine、Langfuse 上报或控制台实现。

### Key Entities

- **Model Reference**: 对一个可调用模型的稳定引用，包含 Provider、模型标识、能力档案和密钥引用，不包含密钥值。
- **Model Request**: 一次模型调用的不可变意图，包含有序消息、所需能力、Tool 描述、结构化结果约束、输出限制和调用关联信息。
- **Model Response**: 非流式终止结果，包含平台文本/内容、Tool Call、结构化结果、结束原因、实际模型和用量。
- **Model Chunk**: 流式调用的有序增量，区分文本、Tool Call 片段、用量更新和终止信息。
- **Model Capability Profile**: 模型可支持功能与容量限制的显式快照，用于调用前校验和 Fallback 兼容判断。
- **Model Usage**: 单次尝试和整次逻辑调用的输入、输出、总 Token 及可选金额数据。
- **Model Error**: 经脱敏的稳定错误，包含错误码、是否可重试、是否可 Fallback、关联标识和安全诊断。
- **Attempt Record**: 一次 Provider 尝试的模型、序号、结果、延迟、用量和降级原因摘要。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: OpenAI 和 Anthropic 的文本、流式、Tool Calling、Structured Output、Usage 和错误样例 100% 通过同一平台契约验收集。
- **SC-002**: 对所有已声明的不兼容能力、超出容量和无效 Fallback 配置，100% 在 Provider 网络调用前被拒绝。
- **SC-003**: 对限流、超时、临时不可用、鉴权失败、无效请求、内容策略、取消和未知故障的标准故障集，100% 得到预期稳定错误码及重试/Fallback 判定。
- **SC-004**: 在 1,000 次确定性离线并发调用中，不出现跨调用消息、凭据引用、Tool Call 片段或用量串扰。
- **SC-005**: 所有重试、Fallback、超时和熔断测试均能证明尝试次数不超过配置硬上限，且取消后不再发起新尝试。
- **SC-006**: 使用已知假密钥和敏感正文执行全部成功与失败测试时，公开结果、异常文本和捕获日志中的秘密泄漏数为 0。
- **SC-007**: 对每个成功、失败、重试和 Fallback 样例，调用摘要中的模型、尝试次数、结束原因及 Token 合计与协议桩事实 100% 一致。
- **SC-008**: 默认完整测试套件在无 Provider 凭据、无外网和无付费调用条件下可重复通过，在线冒烟测试不会被默认执行。
- **SC-009**: 除去模拟的网络等待，Gateway 在 10,000 次本地调用中的额外处理延迟 p95 不超过 10 毫秒。
- **SC-010**: 新增一个符合平台契约的 Provider 时，不需要修改领域契约或现有 Provider 适配器，且现有契约测试继续全部通过。

## Assumptions

- “LLM Provider” 按项目既定 M1 范围解释为 OpenAI 与 Anthropic 双 Provider，而不是只实现单一供应商或一次性聊天客户端。
- 本 Feature 作为可独立验证的模型基础模块交付，不要求 M0 Runtime、数据库、API 或 LangGraph 已先实现。
- 模型和能力档案由受控配置显式维护；本阶段不从 Provider 自动发现全部模型，也不由另一个 LLM 自动路由。
- Tool Schema 和结构化结果约束均由受信任的 AgentVersion 构建流程提供；Provider 不负责 Tool 权限、审批或副作用执行。
- Token 用量优先采用 Provider 返回的可计费字段；金额只有在存在明确、带来源的计价信息时才记录，否则保持未知。
- 真实在线冒烟测试只用于人工或受控 CI 环境，离线协议桩和 Fake Provider 是默认发布门禁。
- 多模态仅覆盖安全地传递已提供的内容或受控引用；文件下载、Artifact 权限和内容扫描属于后续 Feature。
- 本 Feature 不持久化模型调用记录；它返回可供后续 Runtime 和观测适配器持久化的安全摘要。
