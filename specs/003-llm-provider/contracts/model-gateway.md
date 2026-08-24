# Contract: Model Gateway

## Purpose

该契约是 Runtime 与模型层之间的稳定 Python 异步接口。类型的完整字段与验证规则见
[data-model.md](../data-model.md)。本文规定调用行为、错误、流式和扩展兼容性。

## Public interfaces

```python
class ModelGateway(Protocol):
    async def complete(
        self,
        route: ModelRoute,
        request: ModelRequest,
    ) -> ModelResponse: ...

    def stream(
        self,
        route: ModelRoute,
        request: ModelRequest,
    ) -> AsyncIterator[ModelChunk]: ...
```

```python
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> ProviderId: ...

    async def complete(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> ProviderResponse: ...

    def stream(
        self,
        target: ModelTarget,
        request: ModelRequest,
        credential: SecretValue | None,
    ) -> AsyncIterator[ProviderChunk]: ...
```

`ProviderResponse` 与 `ProviderChunk` 是 application port 内部对象，调用方不能访问
LangChain Message、SDK Response 或 Provider 原始异常。

## Complete behavior

1. 校验请求和 route 不变量。
2. 通过 TokenEstimator 得到保守输入上界，并对首选及全部 Fallback 执行能力、上下文
   容量和输出容量检查；配置或估算错误不调用网络。
3. 按目标等待进程内限流许可，并检查熔断准入。
4. 从 SecretProvider 解析目标凭据；Fake Provider 可不解析。
5. 在单目标 attempt timeout 和 route 级逻辑调用总 deadline 的共同约束内调用 Provider。
6. 对允许的错误在同一目标内执行有限重试；每次尝试独立记录。
7. 同目标耗尽后，仅在错误允许时按顺序选择下一兼容目标。
8. 验证 Tool Call 和 Structured Output，生成成功响应或稳定 ModelError。

调用方取消优先级最高：立即停止等待、调用、重试与 Fallback，并以取消语义退出。

## Stream behavior

- 被完整消费的流 sequence 从 0 严格递增，只有一个 terminal 或 error terminal。
- 首块超时控制从 Provider stream 建立到第一个返回块；idle timeout 控制相邻块间隔；
  总超时覆盖整个逻辑调用。
- 在第一个 `text_delta` 或 `tool_call_delta` 发出前，可按 complete 规则重试/Fallback。
- 发出第一个可见增量后，任何超时、断连、格式错误或 Provider 故障直接产生
  error terminal；不得自动重放。
- Tool Call delta 可包含部分 JSON；Gateway 在终止前组装并验证，调用方只能从
  terminal 获得可执行的完整 ToolCall。
- Usage-only 增量可提前出现，但最终 Usage 以 terminal 汇总为准。
- 调用方提前停止迭代时，Gateway 必须关闭底层 async iterator，不继续后台读取、重试
  或 Fallback；因为调用方已关闭消费端，此路径不再发送 terminal。

## Error contract

| Code | Retry | Fallback | Meaning |
|---|---:|---:|---|
| invalid_request | no | no | 请求、消息、Tool 或 Provider 参数无效 |
| capability_mismatch | no | no | 模型能力或容量不满足 |
| authentication | no | no | 密钥缺失或 Provider 鉴权失败 |
| permission | no | no | 账号无权访问模型或资源 |
| content_policy | no | no | Provider 内容安全拒绝 |
| rate_limited | yes | yes | Provider 明确限流 |
| timeout | yes | yes | 首块、空闲、单次或总超时 |
| circuit_open | no | yes | 目标熔断且可尝试兼容 Fallback |
| unavailable | yes | yes | 网络、5xx、过载或临时不可用 |
| malformed_response | no | no | Provider 成功响应无法安全映射 |
| structured_output_invalid | no | no | 本地最终 Schema 校验失败 |
| cancelled | no | no | 调用方取消 |
| unknown | no | no | 未识别且不能安全判断的故障 |

ModelError 的 message 不包含原始 Provider 响应正文、认证头、请求消息、Tool 参数或
Structured Output 正文。内部诊断只能使用 request/correlation/provider-request ID。

## Capability rules

- `tools` 非空要求 `tool_calling`。
- `structured_output` 非空要求 `structured_output`。
- ImagePart/DocumentPart 要求对应 input modality。
- 支持 ImagePart/DocumentPart 的能力档案必须提供相应单项 Token 上界，估算按内容项
  数量累计；缺失上界视为能力配置无效。
- `max_output_tokens` 不得超过目标限制；TokenEstimator 给出的非低估输入上界加输出
  上限不得超过上下文限制；缺少多模态上界时调用前失败。
- Fallback 必须覆盖同一请求的全部要求；不允许通过删除 Tool、Schema、消息或内容块
  来使候选模型“兼容”。

## Tool contract

- Tool 名称在请求内唯一，Schema 顶层为 object。
- Provider 返回的 Tool 名称必须存在于请求定义中。
- ToolCall ID 非空且响应内唯一；参数必须是 JSON object。
- 平台只描述 Tool，不执行 Tool，也不基于模型输出更改权限。
- Provider 内置搜索、代码执行、远程 MCP 等工具不在本 Feature 允许范围。

## Structured output contract

- Provider 接收 JSON Schema；平台不承诺各模型支持 Schema 全集。
- 适配器在调用前拒绝已知不受支持的 Schema 构造。
- Provider 成功后仍调用 `StructuredOutputContract.validate()`。
- 校验失败不返回原始未验证值；未来允许的修复重试必须消耗显式解析预算，本 Feature
  默认不自动添加第二次模型修复调用。

## Extension contract

新增 Provider 只需：

1. 实现 ModelProvider port。
2. 使用新的合法 ProviderId 注册 adapter factory 与能力配置，无需修改 ProviderId 契约。
3. 通过共享 Provider contract suite。

禁止修改既有 ModelRequest/Response 以容纳 Provider 原始对象。新增可移植能力需要先
版本化平台契约；专有能力留在 adapter 私有配置，不能由公共调用方隐式依赖。

## Online smoke boundary

在线测试只有同时满足专用 marker、显式环境开关和相应 SecretReference 可解析时才执行。
每个 Provider 最多一次请求，`max_output_tokens` 与总超时使用测试硬上限；断言只检查
契约与安全元数据，不比较非确定性自然语言正文。
