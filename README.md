<div align="center">
  <h1>ZHIYI</h1>
  <p><strong>Production-grade Agent Runtime Platform for the LangChain ecosystem</strong></p>
  <p>让 Agent 不止能运行，更能恢复、约束、审批、审计与持续评测。</p>
  <p>
    <a href="./doc/PROJECT.md"><strong>项目路线图</strong></a> ·
    <a href="./doc/技术方案.md"><strong>技术架构</strong></a> ·
    <a href="./doc/需求文档.md"><strong>需求基线</strong></a> ·
    <a href="./doc/SDD开发规范.md"><strong>开发规范</strong></a>
  </p>
  <p>
    <a href="https://github.com/liuyuhui2020/ZHIYI/actions/workflows/sdd-governance.yml">
      <img alt="SDD Governance" src="https://github.com/liuyuhui2020/ZHIYI/actions/workflows/sdd-governance.yml/badge.svg?branch=main">
    </a>
    <img alt="Project stage: design baseline" src="https://img.shields.io/badge/stage-design%20baseline-f59e0b?style=flat-square">
    <img alt="Python target: 3.12" src="https://img.shields.io/badge/Python-target%203.12-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white">
    <img alt="SDD: Spec Kit" src="https://img.shields.io/badge/SDD-Spec%20Kit-6f42c1?style=flat-square">
  </p>
</div>

<p align="center">
  <a href="#overview">项目概览</a> ·
  <a href="#why-zhiyi">为什么选择 ZHIYI</a> ·
  <a href="#capabilities">核心能力</a> ·
  <a href="#architecture">架构</a> ·
  <a href="#roadmap">路线图</a> ·
  <a href="#documentation">文档</a> ·
  <a href="#development">参与开发</a>
</p>

---

<a id="overview"></a>

## 项目概览

> [!IMPORTANT]
> ZHIYI 当前处于**方案基线与工程治理阶段**。仓库已经建立产品设计、
> Spec Kit SDD、设计漂移检查、Git Hooks 和 CI，但产品 Runtime、API、
> SDK 与控制台尚未开始实现。本文描述的是已确认设计目标，不代表现有可运行能力。

ZHIYI 是一个面向开发者、自托管的 Agent Runtime Platform。它不是另一个
仅演示 Tool Calling 的聊天机器人，而是为长时间运行、存在外部副作用、需要
恢复和审计的 Agent 任务提供稳定执行底座。

| 当前仓库内容 | 状态 |
|---|:---:|
| 产品价值、需求、功能和技术方案 | ✅ 已建立基线 |
| Spec Kit、设计漂移检查、Git/Claude Hooks、CI | ✅ 已建立 |
| Runtime、REST/SSE、Worker、Checkpoint | ⏳ M0 计划 |
| Context、Memory、RAG、Tool 审批、Langfuse | ⏳ M1 计划 |
| MCP、Subagent、OIDC/RBAC、Helm、生产门禁 | ⏳ M2 计划 |

<a id="why-zhiyi"></a>

## 为什么选择 ZHIYI

以下差异是项目的**设计方向**，将在 M0–M2 按阶段验证：

| 维度 | 常见 Agent Demo | ZHIYI 设计目标 |
|---|---|---|
| 执行模型 | 单次请求内循环 | 持久异步 Run、Checkpoint、租约和故障恢复 |
| Tool 安全 | 模型直接发起调用 | Registry、Schema、Policy、审批、幂等和结果查询 |
| 上下文 | 拼接 Prompt | Context Engine、固定信任顺序和 Context Manifest |
| Memory | 默认保存完整对话 | Policy 接受、来源追踪、TTL、删除和敏感级别 |
| RAG | 返回相关文本 | 权限过滤、版本、Provenance 和 Citation |
| 可靠性 | 失败后重新开始 | 明确终态、有限重试、恢复和人工处理未知副作用 |
| 可观测性 | 打印日志 | Run/Step/Model/Tool Trace、指标、事件和持续评测 |
| 框架边界 | 业务绑定框架对象 | 领域语义归产品所有，LangChain/LangGraph 可替换 |

<a id="capabilities"></a>

## 核心能力

| 能力域 | 已确认设计目标 |
|---|---|
| **Agent Runtime** | Code-first AgentSpec、不可变 AgentVersion、持久 Run、预算、取消和恢复 |
| **Model Gateway** | 能力校验、Structured Output、受控 Fallback、Token 与成本核算 |
| **Context & Memory** | 场景化上下文装配、Manifest、短期摘要和受治理长期记忆 |
| **RAG & Artifact** | pgvector 检索、权限过滤、Citation、S3 Artifact 和文档解析 |
| **Tool Execution** | Tool Registry、Policy、审批、幂等、超时、结果限制和 MCP 适配 |
| **Events & API** | REST Command、持久 RunEvent、SSE 断线续传和稳定 RunResult |
| **Quality & Observability** | OpenTelemetry、Langfuse、数据集评测和 AgentVersion 发布门禁 |
| **Security & Operations** | 多租户、最小权限、脱敏、留存、OIDC/RBAC、Helm 和回滚 |

<a id="architecture"></a>

## 架构

ZHIYI 采用模块化单体起步，API 与 Worker 独立进程，共享领域模型和
PostgreSQL。LangGraph 负责执行恢复，产品数据库保存业务事实，Langfuse
只负责观测和评测。

```mermaid
flowchart LR
    Client["SDK / Console / Application"] --> API["Runtime API"]
    API --> Queue["Durable Run Queue"]
    Queue --> Worker["Runtime Worker"]
    Worker --> Graph["LangGraph Execution"]
    Graph --> Models["LangChain Model Adapters"]
    Graph --> Context["Context / Memory / RAG"]
    Graph --> Tools["Tool Registry / Policy / Approval"]
    Graph --> Events["Run Events / SSE"]
    Graph --> Trace["OpenTelemetry"]
    Trace --> Langfuse["Langfuse"]
    Queue --> PG["PostgreSQL + pgvector"]
    Graph --> PG
    Graph --> S3["S3-compatible Artifacts"]
```

| 组件 | 责任边界 |
|---|---|
| **ZHIYI Domain** | 拥有 Tenant、Agent、Run、Tool、Approval、Memory、Artifact 和 Event 语义 |
| **LangGraph** | Graph 执行、Checkpoint、Interrupt 和恢复，不是业务事实源 |
| **LangChain** | 模型、消息、Tool Schema、Structured Output 和 Provider 生态适配 |
| **Langfuse** | Trace、Prompt 实验和评测；不可用时不得影响 Run 正确性 |
| **PostgreSQL** | 产品业务事实、Run 队列、事件、租约和向量数据 |

完整设计见 [技术方案](./doc/技术方案.md)。

<a id="roadmap"></a>

## 路线图

| 阶段 | 目标 | 关键退出标准 |
|---|---|---|
| **治理基线** | 建立方案事实源与 SDD 门禁 | Spec Kit、Hooks、漂移检查和 CI 通过 |
| **M0 · Runtime Alpha** | 证明 Agent Loop 可可靠执行和恢复 | Worker 故障恢复、SSE 续传、预算终止、状态机测试 |
| **M1 · Platform Beta** | 跑通企业知识与操作 Agent | RAG、Memory、审批写 Tool、Langfuse 和最小控制台闭环 |
| **M2 · Production V1** | 满足生产部署与治理要求 | SLO、升级兼容、安全审计、备份恢复和回滚演练 |

阶段范围、风险和已确认决策见 [PROJECT.md](./doc/PROJECT.md)。

<a id="documentation"></a>

## 文档导航

| 文档 | 用途 |
|---|---|
| [产品价值](./doc/产品价值.md) | 为什么做、为谁创造价值、如何验证价值 |
| [需求文档](./doc/需求文档.md) | 产品范围、功能要求、非功能目标和验收标准 |
| [功能文档](./doc/功能文档.md) | 用户流程、对象行为和功能边界 |
| [技术方案](./doc/技术方案.md) | 架构、状态、数据、可靠性、安全和部署设计 |
| [PROJECT.md](./doc/PROJECT.md) | 当前状态、路线图、风险和已确认决策 |
| [SDD 开发规范](./doc/SDD开发规范.md) | Spec Kit、设计漂移、Hooks 和 CI 操作流程 |
| [仓库 Agent 规则](./AGENTS.md) | AI 编程工具必须读取的强制入口 |
| [详细 Agent 工作规范](./doc/AGENTS.md) | 工程、架构、测试、安全与完成标准 |

<a id="development"></a>

## 参与开发

当前仓库尚无产品安装和启动命令。首次克隆后只需安装版本化 Git Hooks：

```bash
git clone https://github.com/liuyuhui2020/ZHIYI.git
cd ZHIYI
./scripts/sdd/install_hooks.sh
```

任何产品实现开始前必须：

1. 阅读本 README、[PROJECT.md](./doc/PROJECT.md) 和核心产品文档。
2. 解决 M0 对应的待确认事项。
3. 使用 `$speckit-specify` 创建独立 Feature。
4. 完成 `spec → plan → tasks → analyze` 并建立失败测试。
5. 填写 `drift-report.md`，通过本地设计漂移检查。
6. 获得明确的“进入实现”批准后再运行 `$speckit-implement`。

```bash
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
python3 -m unittest discover -s scripts/sdd/tests -v
```

> [!CAUTION]
> 不得使用 `--no-verify`、削弱 CI、绕过 Spec Kit，或通过伪造
> `ALIGNED` 报告压制真实设计漂移。

## 许可证与公开状态

项目目前为私有方案阶段，许可证尚未确定。在许可证正式选定前，不应将仓库
内容视为已授权的开源软件，也不应对外发布第三方受限代码。

---

<div align="center">
  <sub>Reliable Agent execution, beyond the demo.</sub>
</div>
