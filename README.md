<h1 align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="./assets/banners/zhiyi-readme/resumable-orbit-mobile-720x360.png">
    <img
      src="./assets/banners/zhiyi-readme/resumable-orbit-1440x420.png"
      alt="ZHIYI — Reliable agent execution, beyond the demo."
      width="100%"
    >
  </picture>
</h1>

<div align="center">
  <p>面向 Agent 开发者与平台团队，让长时运行任务可恢复、可约束、可审批、可审计、可持续评测。</p>
  <p><strong>方案基线与工程治理阶段 · 产品 Runtime 尚未实现</strong></p>
  <p>
    <a href="./doc/PROJECT.md"><strong>查看当前状态与路线图</strong></a> ·
    <a href="./doc/技术方案.md">阅读技术架构</a> ·
    <a href="#official-docs-site">本地预览官网</a>
  </p>
  <p>
    <a href="https://github.com/liuyuhui2020/ZHIYI/actions/workflows/sdd-governance.yml">
      <img alt="SDD Governance" src="https://github.com/liuyuhui2020/ZHIYI/actions/workflows/sdd-governance.yml/badge.svg?branch=main">
    </a>
    <a href="https://github.com/liuyuhui2020/ZHIYI/actions/workflows/docs-website.yml">
      <img alt="Documentation Website" src="https://github.com/liuyuhui2020/ZHIYI/actions/workflows/docs-website.yml/badge.svg?branch=main">
    </a>
    <img alt="Project stage: design baseline" src="https://img.shields.io/badge/stage-design%20baseline-7c6cff?style=flat-square">
    <img alt="Python target: 3.12" src="https://img.shields.io/badge/Python-target%203.12-0f766e?style=flat-square&amp;logo=python&amp;logoColor=white">
    <img alt="SDD: Spec Kit" src="https://img.shields.io/badge/SDD-Spec%20Kit-334155?style=flat-square">
  </p>
</div>

<p align="center">
  <a href="#overview">项目概览</a> ·
  <a href="#why-zhiyi">设计目标</a> ·
  <a href="#capabilities">核心能力</a> ·
  <a href="#architecture">架构</a> ·
  <a href="#roadmap">路线图</a> ·
  <a href="#documentation">文档</a> ·
  <a href="#official-docs-site">官网</a> ·
  <a href="#development">参与设计与开发</a>
</p>

---

<a id="overview"></a>

## 项目概览

> [!IMPORTANT]
> ZHIYI 当前处于**方案基线与工程治理阶段**。仓库已经建立产品设计、
> Spec Kit SDD、设计漂移检查、Git Hooks 和 CI，但产品 Runtime、API、
> SDK 与控制台尚未开始实现。本文描述的是已确认设计目标，不代表现有可运行能力。

ZHIYI 面向 Agent 开发者和平台团队，目标是建设自托管的 Agent Runtime
Platform。它不以演示 Tool Calling 为终点，而是为长时间运行、存在外部副作用、
需要恢复和审计的 Agent 任务设计稳定执行底座。

| 当前仓库内容 | 状态 |
|---|:---:|
| 产品价值、需求、功能和技术方案 | 已建立基线 |
| Spec Kit、设计漂移检查、Git/Claude Hooks、CI | 已建立 |
| 官网与文档门户 | 本地静态构建已完成，尚未部署 |
| Runtime、REST/SSE、Worker、Checkpoint | M0 计划 |
| Context、Memory、RAG、Tool 审批、Langfuse | M1 计划 |
| MCP、Subagent、OIDC/RBAC、Helm、生产门禁 | M2 计划 |

<a id="why-zhiyi"></a>

## 设计目标与差异

以下差异是项目的**设计方向**，将在 M0–M2 按阶段验证：

- **执行可以恢复：** 持久异步 Run、Checkpoint、租约和明确终态，替代单次请求内不可恢复的循环。
- **副作用受到约束：** Tool 必须经过 Registry、Schema、Policy、审批和幂等控制；未知结果进入人工处理。
- **上下文保持可信：** Context、Memory 与 RAG 遵守固定信任顺序、权限过滤、来源追踪和生命周期策略。
- **质量能够验证：** 稳定领域契约隔离框架变化，运行过程通过事件、Trace、指标和持续评测验证。

<a id="capabilities"></a>

## 核心能力

以下均为已确认的设计范围，不代表已交付能力：

- **可恢复 Runtime：** Code-first AgentSpec、不可变 AgentVersion、持久 Run、预算、取消、Checkpoint、SSE 和稳定 RunResult。
- **受治理智能：** 模型能力校验、Context Manifest、短期摘要、长期 Memory Policy、权限 RAG、Citation 和 Artifact。
- **安全 Tool 执行：** Registry、Schema、Policy、审批、幂等、超时、结果限制、未知结果处理和 MCP 适配。
- **质量与运营：** OpenTelemetry、Langfuse、持续评测、多租户、最小权限、数据治理、Helm 和可回滚发布。

<a id="architecture"></a>

## 架构

ZHIYI 采用模块化单体起步，API 与 Worker 独立进程，共享领域模型和
PostgreSQL。LangGraph 负责执行恢复，产品数据库保存业务事实，Langfuse
只负责观测和评测。

<p align="center">
  <img
    src="./assets/banners/zhiyi-readme/runtime-architecture-1200x520.png"
    alt="ZHIYI Runtime 架构：客户端依次经过 Runtime API、持久队列、Worker 和 LangGraph，最终生成 RunResult；模型与上下文、Tool 与 Policy、状态与可观测能力由运行时统一治理。"
    width="100%"
  >
</p>

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

- **治理基线（当前）：** 建立方案事实源与 SDD 门禁；以 Spec Kit、Hooks、漂移检查和 CI 通过为退出标准。
- **M0 · Runtime Alpha：** 证明 Agent Loop 可可靠执行和恢复；验证 Worker 故障恢复、SSE 续传、预算终止和状态机。
- **M1 · Platform Beta：** 跑通企业知识与操作 Agent；形成 RAG、Memory、审批写 Tool、Langfuse 和最小控制台闭环。
- **M2 · Production V1：** 满足生产部署与治理要求；完成 SLO、升级兼容、安全审计、备份恢复和回滚演练。

阶段范围、风险和已确认决策见 [PROJECT.md](./doc/PROJECT.md)。

<a id="documentation"></a>

## 文档导航

### 快速了解

- [产品价值](./doc/产品价值.md)：为什么做、为谁创造价值、如何验证价值。
- [项目说明与路线图](./doc/PROJECT.md)：当前状态、阶段范围、风险和已确认决策。

### 评估产品与技术

- [需求文档](./doc/需求文档.md)：产品范围、功能要求、非功能目标和验收标准。
- [功能文档](./doc/功能文档.md)：用户流程、对象行为和功能边界。
- [技术方案](./doc/技术方案.md)：架构、状态、数据、可靠性、安全和部署设计。

### 参与设计与开发

- [SDD 开发规范](./doc/SDD开发规范.md)：Spec Kit、设计漂移、Hooks 和 CI 操作流程。
- [仓库 Agent 规则](./AGENTS.md)：AI 编程工具必须读取的强制入口。
- [详细 Agent 工作规范](./doc/AGENTS.md)：工程、架构、测试、安全与完成标准。

<a id="official-docs-site"></a>

## 官网与文档门户

`apps/docs/` 提供 Astro + Starlight 静态官网和文档门户。它通过显式白名单直接读取
上述七份 `doc/*.md`，不会复制或改写正文；构建会校验路由、内部链接、锚点、
Pagefind 搜索索引、响应式、键盘、明暗主题、可访问性和 Lighthouse 门槛。

```bash
corepack enable
corepack install --global pnpm@11.22.0
pnpm --dir apps/docs install --frozen-lockfile
pnpm --dir apps/docs exec playwright install chromium
pnpm --dir apps/docs dev
```

生产构建与完整质量检查：

```bash
pnpm --dir apps/docs build
pnpm --dir apps/docs test:e2e
pnpm --dir apps/docs quality
```

当前仅提供本地构建，仓库没有部署命令、公开域名或分析追踪。公开站点必须作为
独立 Spec Kit Feature 明确审批，配置真实 `SITE_URL` 后再实施。

<a id="development"></a>

## 参与设计与开发

当前可参与产品文档、Spec Kit 工件、架构评审和治理工具改进。产品 Runtime
仍未获准进入实现，任何产品代码必须先完成对应 Feature 的设计门禁并获得明确批准。

当前仓库尚无产品安装和启动命令。参与文档与治理工作时，首次克隆后安装版本化
Git Hooks：

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
