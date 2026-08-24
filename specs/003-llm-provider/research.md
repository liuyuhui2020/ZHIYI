# Research: LLM Provider Gateway

**Date**: 2026-08-24

## Decision 1: 使用专用 LangChain Provider 包并精确锁定

**Decision**: 使用 `langchain-core==1.6.0`、`langchain-openai==1.6.0`、
`langchain-anthropic==1.6.1` 和 `pydantic==2.13.4`，由 `uv.lock` 锁定完整依赖树；
不安装 `langchain-community` 或完整 `langchain` 元包。

**Rationale**: 2026-08-24 查询 PyPI 官方元数据时，上述版本分别为当前稳定版本，
支持 Python 3.12，Provider 包依赖同一 `langchain-core>=1.6.0,<2.0.0` 范围。
LangChain 官方集成页确认两者均支持 Tool Calling、Structured Output、图像输入、
Token 级流式、原生异步与 Usage。专用包满足仓库最小依赖和可替换性原则。

**Alternatives considered**:

- `langchain-community`: 集成面过大，增加无关供应链与升级风险。
- 直接依赖 OpenAI/Anthropic SDK：可获得更细控制，但会重复 LangChain 已提供的消息、
  Tool Schema 和 Structured Output 适配，并偏离既定技术基线。
- 浮动版本范围：无法复现实验和契约行为，不满足依赖锁定要求。

## Decision 2: 平台契约使用纯 Python 值对象

**Decision**: 请求、响应、消息、Tool、Usage、Capability、Chunk 和 Error 使用标准库
dataclass、Enum、Protocol 与不可变集合；第三方类型只在 adapter 内出现。

**Rationale**: 这保持 Provider 与 LangChain 可替换，防止框架对象成为产品契约，
同时使 Fake Provider 和离线契约测试无需加载真实 SDK 行为。

**Alternatives considered**:

- 直接向 Runtime 返回 LangChain `AIMessage`: 会把框架升级风险传播到应用层。
- 在 domain 使用 Pydantic 模型: 违反 constitution 对 domain 依赖的禁止；本 Feature
  也没有需要进入核心 domain 的持久业务实体。

## Decision 3: Structured Output 使用 Protocol + Pydantic adapter

**Decision**: 应用请求接收 `StructuredOutputContract` Protocol；
`PydanticOutputContract` 在 adapter 层包装 Pydantic v2 模型，提供 JSON Schema 与
本地 `model_validate`。Provider 原生约束用于提高生成正确率，本地校验是最终边界。

**Rationale**: 既满足技术方案的 Pydantic 校验要求，也不让 Pydantic 类型穿透平台
契约。官方 Provider 文档确认 OpenAI 与 Anthropic 均支持 Structured Output，但
Provider 支持的 JSON Schema 子集不同，因此配置预检和本地复验都不可省略。

**Alternatives considered**:

- 只信任 Provider 原生 strict 模式：不同模型与 Schema 子集不一致，无法形成统一保证。
- 自行实现完整 JSON Schema 验证器：重复成熟能力且容易遗漏边界。

## Decision 4: Gateway 独占重试与 Fallback

**Decision**: Provider 客户端的 `max_retries` 固定为 0，由 Gateway 实现有限指数退避、
抖动、能力兼容 Fallback、每模型熔断和按模型限流。

**Rationale**: 只有单一策略层才能准确限制尝试次数、总延迟和成本，并生成完整
AttemptRecord。隐藏 SDK 重试会导致尝试次数不可观测和预算失真。

**Alternatives considered**:

- 复用 SDK 默认重试：简单但不可统一控制两个 Provider 的分类和累计预算。
- 通用自动路由器：超出 V1 范围，且可能把路由决策交给另一个 LLM。

## Decision 5: 流式在首个可见增量后禁止自动重放

**Decision**: 首个文本或 Tool Call 增量前的瞬时错误允许重试/Fallback；之后的任何
中断生成终止错误，不自动重试。Tool Call 参数仅在流结束时组装和验证。

**Rationale**: 调用方已消费增量后重放会产生重复文本和重复 Tool Call。Anthropic
官方文档还明确说明细粒度 Tool Streaming 可能产生部分或无效 JSON，必须处理未完成
参数而不能假定每个增量可独立解析。

**Alternatives considered**:

- 任意时点自动重试并从头流式：无法可靠去重，可能触发重复 Tool 行为。
- 完全缓冲后再输出：失去流式首字节价值并增加内存。

## Decision 6: 只映射允许的内容块，不暴露 reasoning

**Decision**: 平台只保留文本、受控多模态引用、Tool Call、Structured Output、
Usage 和允许的结束元数据；OpenAI 扩展内容及 Anthropic reasoning/thinking 块不进入
响应契约、日志或事件。

**Rationale**: LangChain 官方文档展示两个 Provider 都可能返回专有 content block，
Anthropic 可返回详细 thinking。仓库明确禁止把原始 Chain-of-Thought 作为稳定接口。

**Alternatives considered**:

- 原样透传全部 content blocks：造成供应商耦合和隐含推理泄露。

## Decision 7: 密钥引用按调用解析

**Decision**: `ModelTarget` 只保存 `SecretReference`；Gateway 在每次 Provider 尝试前
通过 `SecretProvider` 取得不可打印的 `SecretValue`，adapter 使用后不放入结果或
异常。首个实现提供显式环境变量引用适配器。

**Rationale**: 支持密钥轮换并避免 AgentVersion、日志与配置快照持有密钥正文。

**Alternatives considered**:

- 在模型配置中保存 API Key：直接违反安全基线。
- 全局读取固定环境变量：不利于租户隔离和测试，也难以表达不同目标的凭据引用。

## Decision 8: 用量以 Provider 事实为准，金额未知即未知

**Decision**: 标准化 input/output/total tokens 与可选细分；重试和 Fallback 保留逐次
与累计用量。金额字段仅接受 Provider 明确返回或后续受控计价组件提供的值，本 Feature
不内置易漂移的价格表。

**Rationale**: 官方文档确认两端提供 Usage Metadata，但模型价格会变化且未必由响应
返回。猜测金额会污染预算、审计和评测。

**Alternatives considered**:

- 在代码中硬编码当前价格：时间敏感且需要持续更新，不适合作为 Provider adapter 事实。

## Decision 9: 默认离线契约测试，在线测试显式选择

**Decision**: 通过 LangChain 消息协议桩、SDK 异常样例和 Fake Provider 覆盖所有默认
测试。在线 smoke 使用独立 marker 与环境开关，默认跳过，每端最多一次短调用。

**Rationale**: CI 不应依赖付费账号、网络可用性或非确定性模型结果；真实冒烟仍可在
受控升级评审中验证认证和最小连通性。

**Alternatives considered**:

- 默认调用真实 API：成本、稳定性、数据暴露和可重复性均不可接受。

## Official Sources

- [LangChain ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai)
- [LangChain ChatAnthropic integration](https://docs.langchain.com/oss/python/integrations/chat/anthropic)
- [PyPI: langchain-core](https://pypi.org/project/langchain-core/)
- [PyPI: langchain-openai](https://pypi.org/project/langchain-openai/)
- [PyPI: langchain-anthropic](https://pypi.org/project/langchain-anthropic/)
- [PyPI: Pydantic](https://pypi.org/project/pydantic/)

## Dependency Review

| Dependency | Purpose | Maintenance source | License | Locked |
|---|---|---|---|---|
| langchain-core 1.6.0 | 标准消息、流式块与模型抽象适配 | LangChain 官方 / PyPI | MIT | `uv.lock` |
| langchain-openai 1.6.0 | OpenAI 专用 Chat Model adapter | LangChain 官方 / PyPI | MIT | `uv.lock` |
| langchain-anthropic 1.6.1 | Anthropic 专用 Chat Model adapter | LangChain 官方 / PyPI | MIT | `uv.lock` |
| pydantic 2.13.4 | 外层配置与结构化结果校验 | Pydantic 官方 / PyPI | MIT | `uv.lock` |
| pytest 9.1.1 | 单元、契约与集成测试 | pytest-dev / PyPI | MIT | `uv.lock` |
| pytest-asyncio 1.4.0 | 异步测试执行 | pytest-dev / PyPI | Apache-2.0 | `uv.lock` |
| ruff 0.16.4 | Python lint 与格式门禁 | Astral / PyPI | MIT | `uv.lock` |
| mypy 2.3.1 | 严格静态类型门禁 | mypy 官方 / PyPI | MIT | `uv.lock` |

以上依赖均支持 Python 3.12；实际解析后的传递依赖和哈希以生成的 `uv.lock` 为准。
