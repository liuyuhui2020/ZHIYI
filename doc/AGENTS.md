# ZHIYI 项目 Agent 工作规范

> 状态：生效
> 适用项目：ZHIYI Agent Runtime Platform
> 更新日期：2026-08-21

## 1. 适用方式

本文定义 AI 编码 Agent 和人类开发者在本项目中的详细工程规则。仓库根目录
`AGENTS.md` 是工具发现入口，只维护强制流程摘要并引用本文和项目宪法；
本文负责完整工程规范，两者职责不同，不复制两套详细规则。

## 2. 当前项目阶段

项目当前处于方案阶段。Spec Kit、Hook、漂移检查器和 CI 等治理基础设施已经
获准建立，产品 Runtime 尚未开始编码。

除非用户明确批准进入 M0 实现，否则 Agent 只能：

- 阅读和评审文档。
- 补充需求、架构、ADR、任务和验收标准。
- 检查文档一致性和技术可行性。
- 执行不改变项目状态的调研。

不得擅自创建工程脚手架、安装依赖、设计数据库迁移或提交实现代码。

## 3. 必读文档

开始任何项目工作前，按顺序阅读：

1. `README.md`
2. `doc/PROJECT.md`
3. `doc/需求文档.md`
4. `doc/功能文档.md`
5. `doc/技术方案.md`
6. `.specify/memory/constitution.md`
7. 当前 Spec Kit feature 的 `spec.md`、`plan.md`、`tasks.md` 和
   `drift-report.md`

若文档冲突，先报告冲突并请求或形成明确决策，不能选择最方便实现的一份直接编码。

## 4. 项目目标

建设面向开发者、自托管、基于 LangChain 生态的 Agent Runtime Platform。平台必须优先保证：

1. Tool 副作用安全和状态正确性。
2. Run 可恢复且具有明确终态。
3. 租户隔离和敏感数据治理。
4. Context、Memory 和 Citation 可解释。
5. 行为可观测、可评测和可回滚。
6. 框架适配可替换，领域模型可长期维护。

## 5. 不可破坏的架构约束

- 产品拥有 Tenant、Agent、Session、Task、Run、Tool、Approval、Memory、Artifact 和 Event 语义。
- LangGraph只负责图执行、Checkpoint、Interrupt 和恢复。
- LangChain只负责模型、消息、Tool Schema、Structured Output 和生态适配。
- Langfuse只负责观测、Prompt 实验和评测，不参与运行正确性。
- PostgreSQL产品表是业务事实源；LangGraph Checkpoint 是恢复状态源。
- Domain 层不得导入 LangChain、LangGraph、Langfuse、FastAPI、SQLAlchemy 和具体 Provider 类型。
- 公共 API 和事件不得暴露 LangGraph或 Provider 原始对象。
- Run 必须绑定不可变 AgentVersion。
- Tool 执行语义为 At-least-once + 幂等，禁止宣称 Exactly-once。
- 不保存、不展示、不把原始 Chain-of-Thought 当作稳定接口。

修改上述任一约束前，必须新增或替代 ADR，并同步需求、功能和技术文档。

## 6. 计划技术栈

- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy 2 + Alembic
- LangChain v1 + 专用 Provider 包
- LangGraph + PostgreSQL Checkpointer
- PostgreSQL + pgvector
- S3 兼容对象存储
- OpenTelemetry + Langfuse
- React + TypeScript + Vite + TanStack Query
- Docker Compose；生产 OCI + Helm

版本以实现时的 `pyproject.toml`、`uv.lock` 和前端 lockfile 为准。文档中的版本描述不能替代锁文件。

## 7. 代码组织规则

目标模块：

```text
src/zhiyi/
├── domain/
├── application/
├── runtime/
├── adapters/
├── api/
└── infrastructure/
```

依赖只能向内：

- `domain` 不依赖其他项目层。
- `application` 依赖 `domain` 和端口。
- `runtime` 依赖 `application`、`domain` 和端口。
- `adapters` 实现端口并封装第三方库。
- `api` 调用 application use case，不直接操作 ORM。
- `infrastructure` 负责装配，不承载业务规则。

禁止循环依赖、跨层直接访问数据库、在路由中编写业务流程，以及把所有逻辑堆入 Graph Node。

## 8. 开发工作流

### 8.1 需求与设计

- 每项实现必须先有独立的 `specs/NNN-feature-name/`。
- 编码前必须依次完成 `$speckit-specify → $speckit-plan →
  $speckit-tasks → $speckit-analyze`；高风险变更还要运行
  `$speckit-clarify` 和 `$speckit-checklist`。
- 行为、数据、API、权限、状态机或副作用变化必须先更新当前
  `spec.md` 和 `plan.md`。
- 设计必须覆盖失败、恢复、并发、幂等、安全、观测和回滚。
- 任务必须足够小，能够独立测试和评审，并包含准确实现/测试文件路径。
- `$speckit-analyze` 的 critical finding 未清零不得运行
  `$speckit-implement`。
- 实现后运行 `$speckit-converge`；新发现工作必须回写 `tasks.md`。

### 8.2 测试驱动

核心行为遵循：

1. 先写失败测试。
2. 验证测试因预期原因失败。
3. 实现满足测试的最小生产代码。
4. 运行测试并保持通过。
5. 在绿灯下重构。

脚手架、纯配置和文档不强制先写测试，但必须有适当验证。

### 8.3 提交前验证

工程初始化后，默认验证命令应收敛为：

```bash
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
python3 -m unittest discover -s scripts/sdd/tests -v
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

涉及 PostgreSQL 的 Feature 还必须使用模块级 `postgresql` marker，并分别运行：

```bash
uv run pytest -m "not online and not postgresql"
uv run alembic upgrade head
uv run alembic current --check-heads
uv run alembic check
uv run pytest -m postgresql
```

应用进程不得调用 `create_all`、Alembic upgrade/stamp 或自动修复 Schema。迁移必须由独立
发布步骤执行；`downgrade base` 等破坏性操作必须先校验数据库、用户、主机和一次性环境身份，
不得用于含生产数据的原库回退。

前两项治理命令已经生效；其余是产品工程建立后的目标契约，并应以仓库实际
命令为准。前端需补类型检查、单元测试和生产构建。

不得在没有运行证据时宣称“测试通过”或“实现完成”。

## 9. Python 代码标准

- 使用完整类型标注，公共接口不得依赖隐式 `Any`。
- 输入、配置、模型结构化输出和 Tool Schema 使用 Pydantic 校验。
- 使用明确异常类型和稳定错误码，不用裸 `Exception` 表达业务失败。
- 禁止 `except Exception: pass`。
- 异步路径不得调用阻塞 I/O；阻塞 SDK 必须隔离到线程或专用执行器。
- 网络、模型、Tool 和存储调用必须配置超时。
- 重试必须有限、带退避和抖动，并区分可重试错误。
- 日志使用结构化字段，不拼接密钥、完整 Prompt 或敏感 Tool Payload。
- 函数和类保持单一职责，Graph Node 只协调一个清晰步骤。

## 10. Domain 与状态机

- 状态转换集中定义，不允许任意字符串状态更新。
- 转换必须校验当前状态和版本。
- 重复 Command 必须幂等或明确返回冲突。
- 所有非终态 Run 必须具有可继续、可取消、可超时或可人工处理的路径。
- 不得产生永久 `running` 状态。
- Product Session 与 LangGraph Thread 是不同概念，不得复用同一 ID 逃避映射。

## 11. LangChain 与 LangGraph 规则

- 主 Agent 使用显式 StateGraph；`create_agent` 仅用于简单叶子或受控 Subagent。
- LangChain Message 在 Adapter 或 Runtime 边界转换，不能进入 Domain API。
- Provider 能力必须显式记录和校验。
- Checkpointer 必须使用生产持久化实现，测试可以使用内存实现。
- Interrupt 前不得执行不可幂等副作用。
- 恢复测试必须验证已完成 Tool 不会再次调用。
- Graph State 只放紧凑状态和引用，大结果进入 Artifact。

## 12. Model 规则

- M1 首先维护 OpenAI 和 Anthropic 专用 Adapter。
- 不要为“支持更多模型”批量引入未验证 Provider 包。
- Fallback 必须能力兼容，且记录原因和用量。
- 解析 Structured Output 失败必须受重试和成本预算限制。
- 测试默认使用确定性 Fake Model，不依赖在线 Provider。
- 每个 Provider 必须有消息、流式、Tool Calling、Structured Output、Usage 和错误契约测试。

## 13. Context 与 Memory 规则

- Context 必须由 Context Engine 组装，遵守固定信任和优先级。
- 用户、RAG、Memory 和 Tool 内容不得提升为平台安全指令。
- 每次模型调用生成 Context Manifest。
- 摘要调用计入 Token、成本、Trace 和预算。
- 长期 Memory 只能由 Memory Policy 接受，模型不能直接写库。
- Memory 必须具有来源、范围、敏感级别、TTL 和删除路径。
- 跨租户和跨主体的 Memory 负向测试是强制项。

## 14. Tool 规则

- Tool 必须先注册 ToolDescriptor，再暴露给模型。
- Descriptor 必须声明 Schema、版本、权限、风险、副作用、超时、结果限制和幂等能力。
- 写 Tool 必须串行经过 Policy 和必要审批。
- 只读 Tool 只有明确标记 `parallel_safe` 才能并行。
- 写 Tool 必须使用稳定 idempotency_key。
- 外部调用结果未知时进入 `waiting_resolution`，禁止自动重试。
- 未注册或缺少可信风险元数据的 MCP Tool 默认禁用。
- V1 禁止动态上传 Python 代码、任意 Shell、Eval 和任意 SQL。

## 15. 数据库与迁移规则

- 所有租户资源表和高频索引包含 `tenant_id`。
- 数据库约束保护唯一性、幂等和合法状态，不能只依赖应用检查。
- 事务保持短小，不在持有数据库锁时调用模型或外部 Tool。
- Worker领取使用租约和行锁，租约更新必须校验 lease_token。
- 数据库迁移必须向前兼容滚动升级，并提供回滚或恢复说明。
- 应用启动不得由每个副本自动并发执行迁移。
- LangGraph Checkpoint 表使用独立 Schema，业务查询不得依赖其内部结构。

## 16. API 与事件规则

- REST Command 支持幂等请求键。
- API Schema 与领域模型分离。
- 所有请求先解析 TenantPrincipal。
- 无权资源返回不泄露存在性的结果。
- 错误返回稳定 error_code 和 correlation_id。
- SSE 事件先持久化，再由客户端按序消费。
- 事件 Schema 必须版本化，客户端不得依赖 Provider 或 LangGraph原始事件。
- 慢客户端不得对 Worker 形成背压。

## 17. 安全与隐私规则

- 密钥通过 Secret Provider 注入，不进入代码、AgentSpec、Graph State、Event 或 Trace。
- 敏感数据在应用侧脱敏后才能进入 Langfuse。
- 高风险 Tool 的完整输入输出默认不记录。
- 下载 Artifact 使用短时授权。
- Prompt Injection 和越权不能只靠提示词防御，必须依靠权限和 Policy。
- 安全审计记录不能由普通租户用户关闭。
- 涉及认证、租户、Tool 写入、文件、网络和 SQL 的变更必须执行安全评审。

## 18. 可观测规则

- 日志包含 request_id、tenant_id、session_id、run_id、step_id 和 tool_invocation_id 等适用字段。
- 不得记录密钥、认证头、完整敏感 Prompt 和完整敏感 Tool Payload。
- Langfuse故障不得让 Run 失败。
- 每个 Run Attempt 独立 Trace，重试和恢复关系通过元数据关联。
- 指标必须区分平台错误、模型错误、Tool 错误、策略拒绝和用户取消。
- 重要告警要限频，避免观测故障造成日志风暴。

## 19. 测试最低要求

每个功能至少覆盖：

- 正常路径。
- 无效输入和边界值。
- 未授权与跨租户访问。
- 超时、重试和部分失败。
- 并发和重复请求。
- 幂等和恢复。
- 日志/Trace 脱敏。

核心集成场景：

- 创建 Run 到 SSE 终态。
- Worker 崩溃后恢复。
- 审批暂停和恢复。
- 写 Tool 完成结果复用。
- 写 Tool `outcome_unknown` 不自动重试。
- Context 超预算不删除安全策略。
- Langfuse不可用不影响结果。

## 20. 依赖与供应链

- 只引入有明确用途、维护活跃和许可证可接受的依赖。
- 使用 `uv.lock` 和前端 lockfile 锁定完整依赖树。
- 禁止为了一个简单函数引入大型框架。
- LangChain集成优先使用专用 Provider 包。
- 升级 LangChain、LangGraph、Pydantic、SQLAlchemy 或数据库驱动时必须运行契约和恢复测试。
- 生产镜像应生成 SBOM 并扫描高危漏洞；M2 前固化签名策略。

## 21. 文档同步

| 变更类型 | 必须更新 |
|---|---|
| 产品范围或用户行为 | Spec Kit spec/plan/tasks、需求文档、功能文档 |
| 核心架构或组件职责 | 技术方案、ADR、PROJECT.md |
| 公共 API/Event | 功能文档、OpenAPI、兼容说明 |
| 数据模型或迁移 | Spec Kit 工件、技术方案、运行手册 |
| Tool/Policy/Memory 语义 | 需求、功能、技术、评测用例 |
| 部署与 SLO | 技术方案、PROJECT.md、运行手册 |
| SDD/Hook/CI | SDD开发规范、AGENTS、PROJECT.md |

每个实现 feature 必须填写 `drift-report.md`。若声明 `Docs-Impact:
UPDATED`，列出的文档必须进入同一变更集；若声明 `NONE`，必须给出具体
理由。架构硬违规不能通过更新报告或普通文档豁免。

## 22. 禁止行为

- 未经明确批准从方案阶段进入编码。
- 绕过 Spec Kit 或先编码后补描述性 Spec。
- 使用 `--no-verify`、改写 `core.hooksPath`、降低 CI/设计映射强度或伪造
  `ALIGNED` 报告来绕过漂移门禁。
- 把框架类型传播到领域层和公共 API。
- 在 Graph Node 中直接执行不受控副作用。
- 复用一个 Graph Thread 并发处理独立 Run。
- 用无限重试掩盖 Provider 或 Tool 故障。
- 将完整对话默认写入长期 Memory。
- 同时把 LangSmith和 Langfuse作为生产观测事实源。
- 以“未来再补”为理由省略核心错误、并发、安全和恢复测试。
- 未经用户授权执行提交、推送、部署或生产数据操作。

## 23. 完成定义

一个任务只有同时满足以下条件才可标记完成：

- 行为符合当前 Spec Kit spec、plan 和验收标准。
- `tasks.md` 能追踪全部实现文件，`$speckit-analyze` 和
  `$speckit-converge` 无未处理 critical finding。
- 正常、边界、失败、并发、安全和恢复测试按风险覆盖。
- 类型、Lint、单元和集成检查通过。
- 日志、指标、Trace 和错误足以定位失败且不泄密。
- 数据迁移、回滚和兼容性已说明。
- `drift-report.md` 为 `ALIGNED`，受影响 Spec Kit 工件和长期文档已同步。
- Git/CI 设计漂移门禁通过。
- 没有未处理的高风险评审问题。
- 提供了实际验证命令和结果，不使用推测性完成声明。

## 24. 沟通规范

- 先给结论，再说明证据、风险和下一步。
- 对明显错误的方案直接指出并给替代方案。
- 不因小问题停止推进，但会造成产品行为分叉或安全风险时必须请求决定。
- 不把未验证假设写成已实现事实。
- 评审发现按正确性、安全、数据一致性、恢复、性能和可维护性排序。
