# Data Model: LLM Provider Gateway

本 Feature 不创建数据库表。以下对象是不可变的应用调用契约；所有集合在构造时
复制或冻结，避免并发调用共享可变状态。

## 1. ProviderId

开放式、不可变、受格式校验的注册键值对象，而不是固定枚举。内置值为 `openai`、
`anthropic`、`fake`；新增 Provider 可创建新键并显式注册 adapter，不修改平台契约。
任意请求字符串不能动态加载模块，未注册键在调用前失败。

## 2. SecretReference / SecretValue

### SecretReference

- `name`: 非空、只允许配置键安全字符的引用名。

### SecretValue

- 持有仅供 adapter 使用的秘密正文。
- `str()` 与 `repr()` 固定返回脱敏占位符。
- 不支持序列化、相等比较输出或日志展开。

关系：`ModelTarget` 持有 `SecretReference`；只有 `SecretProvider` 能产生
`SecretValue`，且它不会进入 `ModelResponse` 或 `AttemptRecord`。

## 3. ModelCapabilityProfile

- `text`: 必须为 true。
- `tool_calling`: bool。
- `structured_output`: bool。
- `input_modalities`: `text/image/document` 的非空冻结集合。
- `modality_token_upper_bounds`: 为每个非 text 模态声明单个内容项的保守 Token 上界；
  支持 image/document 时对应正整数必须存在。
- `max_context_tokens`: 正整数。
- `max_output_tokens`: 正整数且小于上下文上限。
- `usage_available`: bool。

能力兼容规则：候选模型必须覆盖请求的全部 required capabilities、输入模态和输出
上限。更大的容量兼容更小请求，功能缺失永不兼容。

## 4. ModelTarget / ModelRoute

- `provider`: ProviderId。
- `model_id`: 非空稳定模型标识。
- `credential`: SecretReference；Fake 可省略。
- `capabilities`: ModelCapabilityProfile。
- `limits`: 单次 attempt timeout、stream-first-byte、stream-idle、retry、rate-limit 和 circuit-breaker 配置。

唯一逻辑键：`(provider, model_id)`。Fallback 链不得包含重复键或首选目标自身。

### ModelRoute

- `primary`: 首选 ModelTarget。
- `fallbacks`: 有序且无重复/循环的 ModelTarget tuple。
- `total_timeout_seconds`: 覆盖限流等待、SecretProvider、全部尝试、退避、Fallback
  和流消费的正数硬 deadline；任何目标自己的 attempt timeout 不能延长该 deadline。

## 5. Message / ContentPart

### Message

- `role`: `system/user/assistant/tool`。
- `content`: 至少一个有序 ContentPart；Tool 结果消息还需 `tool_call_id`。
- `name`: 可选安全名称。

### ContentPart

- `TextPart(text)`: 保留 Unicode 与空白；整条请求不能只有空白。
- `ImagePart(uri, media_type)`: 只接受 `https` 或受控 `data` 内容，不读取本地路径。
- `DocumentPart(uri, media_type, title?)`: 只传递受控引用，不负责下载或授权。

系统与 Tool 消息只允许文本；多模态输入只能用于能力档案允许的角色/模型。

## 6. ToolDefinition / ToolCall / ToolCallDelta

### ToolDefinition

- `name`: 非空且匹配稳定工具名规则。
- `description`: 非空、长度受限。
- `input_schema`: JSON object schema；禁止非对象顶层。
- `strict`: 是否请求 Provider 原生严格模式。

### ToolCall

- `id`: 非空且在单次响应内唯一。
- `name`: 必须匹配请求内已声明 Tool。
- `arguments`: 完整 JSON object。

### ToolCallDelta

- `index`: 非负整数，标识流内顺序。
- `id/name/arguments_fragment`: 可分段出现。

组装键优先使用 index，并校验最终 ID 唯一。流终止时任何缺失 ID、名称或不可解析
参数都产生 `malformed_response`。

## 7. StructuredOutputContract

- `name`: 稳定 Schema 名称。
- `json_schema`: JSON object schema。
- `validate(value)`: 返回已验证、可安全序列化的平台值，失败抛出类型化校验错误。

adapter 层的 Pydantic 实现负责生成 Schema 与最终本地校验。ModelRequest 不能同时
要求“强制 Tool Call”与“只允许最终结构化结果”等互斥策略。

## 8. ModelRequest

- `request_id`: 调用关联标识，非空。
- `messages`: 非空有序 tuple。
- `required_capabilities`: 冻结集合。
- `tools`: ToolDefinition tuple，名称唯一。
- `structured_output`: 可选 StructuredOutputContract。
- `max_output_tokens`: 正整数。
- `temperature`: 可选有限数值，范围 0..2。
- `stop`: 去重后的非空字符串 tuple。

构造时验证消息、Tool、Schema 和上限，但模型特定能力由 Gateway 对 target 校验。

## 8.1 TokenEstimate / TokenEstimator

### TokenEstimate

- `input_upper_bound`: 非负整数，必须是不低于待发送输入的保守 Token 上界。
- `method`: 稳定估算方法标识。
- `estimated_at`: 可选估算版本/时间元数据，不包含消息正文。

### TokenEstimator

- `estimate(target, request) -> TokenEstimate`。
- 默认离线估算按 UTF-8 字节、Tool/Structured Schema 的稳定 JSON 字节与固定协议开销
  计算文本上界；单个 Token 不可能超过其 UTF-8 字节数，因此允许保守高估但不能低估。
- Image/DocumentPart 使用 ModelCapabilityProfile 中显式配置的每项 Token 上界；缺少
  上界时能力预检失败，而不是猜测或发起网络调用。
- Gateway 在 Provider 网络调用前验证 `input_upper_bound + max_output_tokens <=
  max_context_tokens`。Provider 返回的实际 Usage 不反向改变已完成的预检决定。

## 9. ModelUsage

- `input_tokens`: 非负或 unknown。
- `output_tokens`: 非负或 unknown。
- `total_tokens`: 非负或 unknown；已知时不得小于已知输入与输出之和。
- `input_details/output_details`: 只允许平台白名单计数，不保留供应商任意对象。
- `amount/currency`: 可选；缺任一则二者均为 unknown。

聚合规则：只合计已知值；存在未知尝试时聚合相应字段标记 incomplete，不用 0 替代。

## 10. AttemptRecord

- `attempt_no`: 从 1 单调递增。
- `target`: 实际 Provider/模型标识，不含密钥引用。
- `started_at/duration_ms`: UTC 时间与非负耗时。
- `outcome`: `succeeded/failed/cancelled`。
- `error_code`: 失败时必填。
- `provider_request_id`: 可选安全请求标识。
- `usage`: 可选 ModelUsage。
- `fallback_reason`: 发生模型切换时的稳定错误码。

## 11. ModelResponse

- `request_id`: 与请求一致。
- `provider/model_id/provider_request_id`: 实际目标与可选请求标识。
- `content`: 平台允许的文本内容块；不含 reasoning/thinking。
- `tool_calls`: 完整 ToolCall tuple。
- `structured_output`: 可选已校验值。
- `finish_reason`: `stop/tool_calls/length/content_filter/cancelled/unknown`。
- `usage`: 当前成功尝试用量。
- `total_usage`: 包含失败尝试的逻辑调用累计用量。
- `attempts`: 非空 AttemptRecord tuple。

成功响应至少包含文本、Tool Call 或结构化结果之一；空响应产生 malformed_response。

## 12. ModelChunk / StreamTerminal

- `sequence`: 从 0 严格递增。
- `kind`: `text_delta/tool_call_delta/usage/terminal/error`。
- 对应 payload 只能设置一个。
- `terminal`: 包含结束原因、最终 Tool Call、最终 Usage、AttemptRecord 和可选结构化结果。

状态序列：

```text
created -> waiting_first_chunk -> streaming -> terminal
                          \-> retrying/fallback -> waiting_first_chunk
streaming -> error_terminal
```

完整消费到结束的流只有一个 terminal 或 error terminal。调用方主动取消或提前关闭
迭代时，Gateway 关闭底层 iterator、停止后续尝试且不再向已关闭调用方发送 terminal。
进入 streaming 后禁止 retry/fallback。

## 13. ModelError

- `code`: `invalid_request`, `capability_mismatch`, `authentication`, `permission`,
  `content_policy`, `rate_limited`, `timeout`, `circuit_open`, `unavailable`,
  `malformed_response`, `structured_output_invalid`, `cancelled`, `unknown`。
- `message`: 不含 Provider 原始正文的安全说明。
- `retryable`: bool。
- `fallback_allowed`: bool；只有 retryable 的 Provider/传输错误可为 true。
- `request_id/correlation_id`: 安全关联标识。
- `attempts`: 已完成尝试摘要。

`cancelled` 保持取消语义，不转换为普通失败；配置、能力和 Schema 错误均在网络前产生。

## 14. CircuitBreakerState

状态：`closed -> open -> half_open -> closed/open`。

- CLOSED: 连续临时失败达到阈值后转 OPEN。
- OPEN: 冷却期内快速失败，期满后第一个请求转 HALF_OPEN。
- HALF_OPEN: 只允许一个探测；成功转 CLOSED，失败重新 OPEN。
- 非临时业务/鉴权错误不累计熔断失败。
