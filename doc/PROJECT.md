# ZHIYI Agent Runtime Platform 项目说明

> 当前阶段：方案基线与工程治理
> 项目状态：产品编码未开始；SDD 治理已建立
> 文档版本：v0.1
> 更新日期：2026-08-21

## 1. 项目章程

ZHIYI 是一个面向开发者、自托管、基于 LangChain 生态的 Agent Runtime Platform。项目目标是提供可恢复、可约束、可审批、可审计和可评测的 Agent 执行环境。

首个参考应用是企业知识与操作 Agent，用于验证 RAG、Context、Memory、Tool、审批、幂等、恢复和 Citation 的完整闭环。

## 2. 文档入口

| 文档 | 用途 |
|---|---|
| [README](./README.md) | 项目总入口和阅读导航 |
| [产品价值](./产品价值.md) | 为什么做、为谁创造什么价值 |
| [需求文档](./需求文档.md) | 产品范围、功能与非功能验收要求 |
| [功能文档](./功能文档.md) | 用户流程和功能行为 |
| [技术方案](./技术方案.md) | 架构、数据、运行、可靠性和部署方案 |
| [SDD 开发规范](./SDD开发规范.md) | Spec Kit、设计漂移与自动门禁 |
| [AGENTS](./AGENTS.md) | 后续编码 Agent 必须遵守的工作规范 |

## 3. 当前状态

### 已完成

- 明确产品定位：开发者 Agent Runtime Platform，而非最终用户万能助手。
- 确认自托管 Runtime Service + Python SDK。
- 确认 LangChain、LangGraph和 Langfuse 的职责边界。
- 确认异步 Run、Tool 可靠性、Context、Memory、RAG、Artifact 和评测原则。
- 确认 M0、M1、M2 三阶段交付策略。
- 形成首版产品和技术文档。
- 初始化官方 Spec Kit，并安装 Codex 和 Claude Code 集成。
- 建立项目宪法、设计漂移检查器、Git/Claude Hooks 和 CI 门禁。

### 未开始

- 工程脚手架和依赖锁定。
- 数据库 Schema 与迁移。
- Runtime、API、Worker 和 Console 实现。
- 测试、压测、安全审计和部署。

### 历史规划草案

`openspec/changes/build-langchain-agent-platform/` 中存在较早的 OpenSpec 草案。
该草案形成于完整方案确认和 Spec Kit 治理之前，只能作为历史输入。所有新
实现必须使用 `specs/NNN-feature-name/`，不能继续扩展该旧草案。

## 4. 项目目标

### 4.1 产品目标

- 开发者能以 Code-first Python API 定义和版本化 Agent。
- 用户任务以持久异步 Run 执行，可流式查看并跨故障恢复。
- Tool 受到 Schema、权限、Policy、审批和幂等约束。
- Context 与 Memory 可解释、可控制、可删除。
- RAG 结果有来源链，最终结果具有稳定 Schema。
- Agent 质量、成本和安全回归能够被持续评测。

### 4.2 工程目标

- Runtime API 月可用性目标 99.9%。
- Worker 故障后 30 秒内接管可恢复 Run。
- SSE 事件交付额外延迟 p95 小于 500 ms。
- 单 Worker Pool 建立 100 个活跃 Run 的容量基线。
- 不允许永久卡住的 Run 和无法解释的状态转换。
- 已确认完成的幂等写 Tool 不得在恢复时重复产生副作用。

## 5. 项目非目标

- 首期公有 SaaS、计费和租户运营。
- 动态上传和执行不可信代码。
- 自治多 Agent 网络。
- 拖拽式 Graph 编辑器。
- 完整 Tool Marketplace。
- 对外部系统承诺 Exactly-once。
- 自动修改生产 Agent 配置。
- 首期完整隔离网商业交付认证。

## 6. 里程碑

### M0 Runtime Alpha

目标：证明 Runtime 能够可靠执行和恢复，而不是只在单次请求内完成演示。

计划交付：

- 项目脚手架、配置、数据库和迁移。
- 领域对象和平台端口。
- AgentSpec、AgentVersion 和标准 Graph。
- 异步 Run、PostgreSQL租约、Worker 和 Reconciler。
- Fake Model、Model Gateway 和一个只读 Tool。
- LangGraph PostgreSQL Checkpoint。
- RunEvent、REST、SSE 和 RunResult。
- 预算、取消、失败分类、日志和健康检查。

退出标准：

- Worker 在节点间被强制终止后，Run 可以恢复。
- 重复领取和重复命令不会产生并发状态破坏。
- SSE 可以从最后事件继续。
- 无限 Tool Loop 被硬预算终止。
- 核心状态机、租约、Graph 和 API 集成测试通过。

M0 不包含：真实写 Tool、长期 Memory、RAG、控制台、MCP 和 Subagent。

### M1 Platform Beta

目标：完整跑通企业知识与操作 Agent，验证平台业务价值。

计划交付：

- OpenAI、Anthropic Provider 与契约测试。
- Context Engine 与 Context Manifest。
- 短期摘要和受治理长期 Memory。
- 文档、pgvector 检索和 Citation。
- Artifact 与 TXT/Markdown/PDF 解析。
- Tool Policy、审批和幂等写 Tool。
- Langfuse Trace、PromptRegistry 和评测。
- 最小 React 开发者控制台。
- 数据脱敏、留存和删除流程。

退出标准：

- 参考 Agent 从检索到审批写入完整闭环通过。
- Langfuse不可用不会影响 Run。
- Prompt Injection、越权和 Memory 污染测试通过。
- 外部写 Tool 在成功、失败、重试和未知结果场景行为明确。
- 两个 AgentVersion 能通过同一数据集进行质量、成本和延迟对比。

M1 只面向可信开发或试点环境，不开放通用外部生产流量。

### M2 Production V1

目标：达到可对外生产部署、升级和治理的标准。

计划交付：

- MCP Tool 发现、授权和隔离。
- 受控 Subagent 和委派预算。
- OIDC、RBAC 和生产租户隔离。
- Agent Package/AgentVersion 兼容路由。
- 自动化评测发布门禁。
- Helm、滚动升级、迁移和回滚。
- 指标、告警、备份、容量和保留策略。
- 故障注入、100 活跃 Run 基线压测和安全审计。

退出标准：

- 达到初始 SLO。
- 旧 AgentVersion 的活跃和等待审批 Run 能在升级后安全处理。
- 安全审计无未处理高风险问题。
- 备份恢复、数据库迁移回滚和应用回滚经过演练。
- 生产运行手册和故障处理手册完成。

## 7. 工作流分解

| 工作流 | M0 | M1 | M2 |
|---|---|---|---|
| Domain/API Contract | 核心对象与事件 | Memory/RAG/Approval | MCP/Subagent/RBAC |
| Runtime | 标准 Graph 与恢复 | Planning/Context/Tool | 版本路由与复杂编排 |
| Data | PostgreSQL/Checkpoint | pgvector/S3/留存 | HA/备份/容量 |
| Model | Fake + Gateway | OpenAI/Anthropic | 更多 Provider 按需求 |
| UX | OpenAPI/SSE | 最小 Console | 生产运维体验 |
| Quality | 单元/集成/恢复 | 评测/安全/故障注入 | 压测/审计/发布门禁 |
| Delivery | Compose | 试点环境 | Helm/滚动升级/回滚 |

## 8. 已确认决策

| 编号 | 决策 | 状态 |
|---|---|---|
| D-001 | 产品是开发者 Runtime Platform | 已确认 |
| D-002 | 自托管 Runtime Service + Python SDK | 已确认 |
| D-003 | 企业知识与操作 Agent 为参考场景 | 已确认 |
| D-004 | Code-first AgentSpec，保留自定义 StateGraph | 已确认 |
| D-005 | 自研服务嵌入 LangGraph OSS | 已确认 |
| D-006 | 异步 Run 是标准执行模型 | 已确认 |
| D-007 | Tool 采用 At-least-once + 幂等 | 已确认 |
| D-008 | 长期 Memory 经过 Policy | 已确认 |
| D-009 | Context Engine 统一治理上下文 | 已确认 |
| D-010 | 显式模型选择与受控 Fallback | 已确认 |
| D-011 | 平台 Tool Registry 适配 LangChain/MCP | 已确认 |
| D-012 | Langfuse为唯一生产 LLM 观测平台 | 已确认 |
| D-013 | 核心 Prompt Git 管理，业务 Prompt 可实验 | 已确认 |
| D-014 | REST 命令 + SSE 事件 | 已确认 |
| D-015 | 单主 Agent + 受控 Subagent | 已确认 |
| D-016 | 禁止运行时动态代码上传 | 已确认 |
| D-017 | 数据模型从第一天支持 tenant_id | 已确认 |
| D-018 | PostgreSQL + pgvector + S3 为首期数据栈 | 已确认 |
| D-019 | OCI/Compose/Helm 部署路径 | 已确认 |
| D-020 | AgentVersion 不可变，Run 固定版本 | 已确认 |
| D-021 | 评测是发布门禁 | 已确认 |
| D-022 | 提供最小开发者控制台 | 已确认 |
| D-023 | 分级留存和最小化 Trace | 已确认 |
| D-024 | Policy Engine 确定性决定审批 | 已确认 |
| D-025 | Artifact 为一等对象 | 已确认 |
| D-026 | RAG 必须具备 Provenance/Citation | 已确认 |
| D-027 | 不暴露原始 Chain-of-Thought | 已确认 |
| D-028 | 不依赖外部 SaaS 控制面 | 已确认 |
| D-029 | V1 只提供 Python Agent SDK | 已确认 |
| D-030 | 三阶段交付，不做大爆炸版本 | 已确认 |
| D-031 | 开源方式设计，许可证 M1 后决定 | 已确认 |
| D-032 | 简单 Tool Loop，复杂任务启用 Planning | 已确认 |
| D-033 | 生产 Agent 禁止自我修改 | 已确认 |
| D-034 | RunResult 为稳定结构化契约 | 已确认 |

## 9. 风险登记

| 风险 | 概率 | 影响 | 当前缓解 | 所属门禁 |
|---|---|---|---|---|
| LangChain/LangGraph升级破坏行为 | 中 | 高 | 版本锁定、适配器、契约测试 | 每次升级 |
| 产品状态与 Checkpoint 漂移 | 中 | 高 | 幂等、Reconciler、故障注入 | M0 |
| 写 Tool 重复或结果未知 | 中 | 极高 | 幂等键、结果查询、人工处理 | M1 |
| Context/Trace 泄露敏感数据 | 中 | 极高 | 分类、脱敏、TTL、安全测试 | M1/M2 |
| AgentVersion升级后不能恢复旧任务 | 中 | 高 | 包摘要、兼容 Worker、审批 TTL | M2 |
| PostgreSQL队列成为瓶颈 | 低到中 | 中 | 索引、容量指标、明确拆分阈值 | M2 |
| 范围过大导致核心未稳定 | 高 | 高 | 阶段退出标准、非目标、Spec Kit SDD | 全阶段 |
| AI 编码与设计静默漂移 | 中 | 高 | Spec Kit、漂移报告、Git/AI Hook、CI | 全阶段 |
| 本地 Hook 或仓库内 CI 被主动绕过 | 中 | 高 | 远端 Ruleset、Required Check、治理路径评审 | 建仓后 |
| 多租户越权 | 中 | 极高 | Tenant Context、RBAC、负向测试 | M2 |

## 10. 待确认事项

以下事项不阻塞方案，但必须在对应阶段前确认：

- 项目正式名称和仓库命名。
- M1 第一个真实业务系统及写 Tool。
- 生产数据所在区域和合规要求。
- Langfuse Cloud 或自托管选择。
- OIDC Provider 和组织角色映射。
- M1 后是否公开项目以及采用何种许可证。
- 生产预算、Token 单价来源和租户配额策略。

## 11. 项目治理

### 11.1 事实源优先级

1. `.specify/memory/constitution.md` 和已接受 ADR。
2. 当前 Spec Kit feature 的 `spec.md` 和验收标准。
3. 当前 feature 的 `plan.md`、契约和数据模型。
4. 当前 feature 的 `tasks.md`。
5. `doc/需求文档.md`、`doc/功能文档.md` 和 `doc/技术方案.md`。
6. 测试、生成的 API Schema 和代码。

若代码与已确认需求冲突，不能用“代码已经这样实现”作为改变需求的理由。

### 11.2 变更门禁

- 改产品行为、状态机、权限、数据或公开 API：先更新当前 Spec Kit
  `spec.md`、`plan.md` 和 `tasks.md`，再运行 `$speckit-analyze`。
- 改核心架构决策：新增或替代 ADR。
- 改 AgentVersion行为：执行评测门禁。
- 改数据库：提供迁移、回滚和并发影响说明。
- 改 Tool 副作用语义：必须补故障与幂等测试。
- 改实现：更新 `drift-report.md`；存在语义漂移时同步受影响长期文档，
  架构硬违规直接禁止。

### 11.3 进入下一阶段

当前下一门禁不是编码，而是文档评审：

1. 审阅 `doc/` 文档的一致性和范围。
2. 解决待确认事项中与 M0 有关的项目名称和运行边界。
3. 使用 Spec Kit 为 M0 建立 `spec → plan → tasks` 工件。
4. 运行 `$speckit-analyze`，生成验收测试和漂移报告。
5. 明确批准后再初始化产品工程。

## 12. 项目健康指标

- 当前阶段任务是否有明确退出标准。
- 未解决高风险问题数量。
- Run 终态覆盖率和卡住任务数量。
- 自动化测试、恢复测试和安全测试通过率。
- AgentVersion评测质量与成本回归。
- Spec Kit 工件、长期文档、API、测试和代码的一致性。
- 设计漂移门禁失败数、豁免数和修复时长。

后续实现代理必须遵守 [AGENTS.md](./AGENTS.md)。
