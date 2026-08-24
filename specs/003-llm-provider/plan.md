# Implementation Plan: LLM Provider Gateway

**Branch**: `codex/003-llm-provider` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-llm-provider/spec.md`

## Summary

建立首个产品 Python 包，以纯 Python 平台契约定义消息、内容块、Tool Schema、
请求、响应、流式增量、能力、用量和错误；应用层 `ModelGateway` 负责能力预检、
限流、总/流式超时、取消、有限重试、兼容 Fallback、熔断和尝试汇总；外层适配器
通过 LangChain 专用 OpenAI/Anthropic 集成完成消息与错误映射，并提供确定性 Fake
Provider。所有默认测试离线执行，真实 Provider 冒烟测试显式启用且受硬预算限制。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `langchain-core==1.6.0`, `langchain-openai==1.6.0`,
`langchain-anthropic==1.6.1`, `pydantic==2.13.4`; 不引入 `langchain-community`

**Storage**: N/A；本 Feature 不持久化调用、用量、熔断或限流状态

**Testing**: pytest 9.1.1、pytest-asyncio 1.4.0、离线协议桩、确定性 Fake
Provider；真实 OpenAI/Anthropic 冒烟测试默认跳过

**Target Platform**: Linux/OCI 生产目标，macOS/Linux 本地开发；异步 Python Runtime

**Project Type**: 模块化单体内的可复用 Python library 模块

**Performance Goals**: 排除网络等待后，10,000 次本地 Gateway 调用额外处理延迟
p95 < 10 ms；1,000 次离线并发调用无状态串扰

**Constraints**: Provider/LangChain/Pydantic 类型不得进入 domain 或公共平台契约；
默认离线；无秘密日志；所有网络路径必须有总超时，流式还具有首块与空闲超时；
重试与 Fallback 必须有硬上限

**Scale/Scope**: 2 个真实 Provider、1 个 Fake Provider、文本/图像/文档引用、
非流式/流式、多个 Tool Call、Structured Output、Usage/Error；不包含数据库、API、
Graph、Context Engine、Langfuse 或控制台

## Constitution Check

*GATE: Phase 0 前通过；Phase 1 设计后复核通过。*

- **I. Specification Before Implementation — PASS**: `spec.md` 已完成且无占位符；
  本计划、任务、分析和实现将按独立 feature 顺序执行。
- **II. Product Semantics Own the Framework — PASS**: 平台契约位于 application
  边界且只使用标准库；LangChain、Pydantic 和 Provider SDK 仅存在于 adapters 与
  infrastructure。公共返回不暴露第三方对象。
- **III. Test-First and Traceability — PASS**: 计划以契约、Gateway、适配器的失败测试
  开始；正常、边界、错误、超时、重试、取消、并发、流中断和脱敏都有明确测试目录。
- **IV. Recoverable, Idempotent Agent Execution — PASS**: 本 Feature 不执行 Tool 副作用；
  流式首个可见增量后禁止自动重试/Fallback，避免重复消息或 Tool Call。
- **V. Tools and Context Are Untrusted — PASS**: Tool Schema 与模型返回均校验；图像/文档
  只传递已给定引用，不读取任意本地路径，不执行 Provider 内置 Tool。
- **VI. Tenant Isolation, Privacy, Least Privilege — PASS**: 密钥只通过 SecretProvider
  引用解析；每次调用状态隔离；日志、错误和摘要不含密钥、认证头或完整 Prompt。
- **VII. Observable Without Hidden Reasoning — PASS**: 只返回文本、Tool Call、结构化结果、
  Usage 和安全尝试摘要；reasoning/thinking 块被丢弃，不形成稳定契约。
- **VIII. Simple, Versioned, Reversible — PASS**: 只引入官方专用 Provider 包并锁版本；
  适配器可移除，平台契约不依赖供应商；回滚是移除装配并恢复依赖锁，无数据迁移。

**Post-design re-check**: PASS。`data-model.md`、`contracts/model-gateway.md` 和
`quickstart.md` 未引入宪法例外；依赖方向、秘密边界、无 CoT、有限重试和离线测试
保持一致。

## Project Structure

### Documentation (this feature)

```text
specs/003-llm-provider/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── model-gateway.md
├── checklists/
│   ├── requirements.md
│   └── security.md
├── tasks.md
└── drift-report.md
```

### Source Code (repository root)

```text
pyproject.toml
uv.lock
src/zhiyi/
├── __init__.py
├── application/
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── contracts.py
│   ├── ports/
│   │   ├── __init__.py
│   │   ├── model_provider.py
│   │   ├── secret_provider.py
│   │   └── token_estimator.py
│   └── services/
│       ├── __init__.py
│       ├── circuit_breaker.py
│       ├── model_gateway.py
│       └── rate_limiter.py
├── adapters/
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── anthropic.py
│   │   ├── fake.py
│   │   ├── langchain_base.py
│   │   ├── openai.py
│   │   ├── structured_output.py
│   │   └── token_estimator.py
│   └── secrets/
│       ├── __init__.py
│       └── environment.py
└── infrastructure/
    ├── __init__.py
    └── config/
        ├── __init__.py
        └── models.py

tests/
├── contract/models/
│   ├── test_anthropic_contract.py
│   ├── test_openai_contract.py
│   └── test_provider_contract.py
├── integration/models/
│   ├── test_anthropic_smoke.py
│   └── test_openai_smoke.py
├── performance/
│   └── test_model_gateway_overhead.py
└── unit/
    ├── application/models/test_contracts.py
    ├── application/services/test_circuit_breaker.py
    ├── application/services/test_model_gateway.py
    ├── application/services/test_rate_limiter.py
    ├── adapters/models/test_langchain_mapping.py
    ├── adapters/models/test_structured_output.py
    ├── adapters/models/test_token_estimator.py
    ├── adapters/secrets/test_environment.py
    └── infrastructure/config/test_models.py
```

**Structure Decision**: 使用项目技术方案既定六边形目录的最小子集。平台中立数据
与端口放在 `application`，因为本功能没有新增核心业务实体；重试、Fallback、限流
和熔断属于应用策略；LangChain、Provider SDK、Pydantic 与环境变量访问全部留在
`adapters`/`infrastructure`。不创建空的 domain、runtime 或 api 脚手架。

## Design Decisions

### 1. Gateway 是唯一调用策略所有者

两个真实适配器都禁用自身自动重试。Gateway 按“能力预检 → 限流 → 熔断准入 →
单模型有限重试 → 兼容 Fallback”执行，避免 SDK、LangChain 和平台三层重试相乘。
退避等待、Provider 调用与流读取均响应取消。

### 2. 流式重试边界

在第一个用户可见文本或 Tool Call 增量发出前发生的可重试错误允许重试/Fallback；
一旦发出可见增量，任何中断都返回终止错误，不自动重放。Usage-only 元数据不视为
用户可见增量。流式 Tool Call 按 `(index, call_id)` 组装，流终止时统一验证。

### 3. Structured Output 的依赖隔离

应用契约只依赖 `StructuredOutputContract` Protocol，暴露名称、JSON Schema 和
`validate()`。`PydanticOutputContract` 在 adapter 层包装 Pydantic v2 模型；Provider
使用 JSON Schema 请求原生结构化输出，Gateway 在成功返回前再次执行本地校验。

### 4. 能力与配置

`ModelTarget` 固定开放式 Provider 注册键、模型 ID、SecretReference、能力档案和
单目标尝试限制；`ModelRoute` 独立声明覆盖限流、密钥解析、重试、退避、Fallback
和流消费的逻辑调用总 deadline。能力档案来自受控配置而非在线自动发现。Fallback
链在配置加载和每次请求所需能力已知后再次校验。官方 OpenAI API 与官方 Anthropic
API 是当前唯一预置端点范围，新增显式注册的 Provider 不修改平台契约。

调用前通过 `TokenEstimator` port 计算不会低估的保守上界；默认 adapter 以 UTF-8
字节、Tool/Schema 序列化字节和固定协议开销估算文本 Token，多模态使用能力档案中
显式配置的上界。估算结果只用于拒绝确定超限请求，后续 Provider Usage 用于实际记账。

### 5. 秘密与安全观测

`SecretValue` 禁止有意义的 `repr/str`，环境适配器按显式引用读取且不枚举环境。
Provider 异常先按 SDK 类型映射，再生成无原始正文的 `ModelError`。AttemptRecord
只保存标识、错误码、延迟、Token 和 Fallback 原因；消息、结构化正文、认证信息和
reasoning/thinking 块不进入摘要。

### 6. 限流与熔断作用域

进程内令牌桶和熔断器以 `(provider, model_id)` 为键；调用局部状态始终独立。
熔断状态为 CLOSED/OPEN/HALF_OPEN，半开只允许一个探测。该状态不跨进程持久化，
分布式配额属于后续 Runtime/基础设施 Feature。

## Failure, Rollback, and Operations

- Provider 错误按 `invalid_request/authentication/permission/content_policy/rate_limited/
  timeout/unavailable/malformed_response/cancelled/unknown` 分类，只有明确临时故障可重试。
- route 级总 deadline 是硬上限；任何单目标尝试、退避或流式超时都不得把工作延长到
  deadline 之后。
- 配置或能力错误在任何网络访问前失败；无能力兼容 Fallback 时保留首选模型错误。
- 本地结构化校验失败计入解析预算但不按网络瞬时故障处理。
- 真实冒烟测试通过专用环境开关启用，并要求独立测试密钥、最大一次调用和短超时。
- 回滚不涉及数据：从装配移除真实 Provider，保留 Fake Provider 和平台契约，恢复
  `pyproject.toml`/`uv.lock` 即可；不得删除已被后续 Feature 采用的平台契约而不先同步规格。
- 后续 Runtime 负责持久化 AttemptRecord、指标与 Trace；本 Feature 只返回安全摘要。

## Complexity Tracking

无宪法违规，不需要例外说明。
