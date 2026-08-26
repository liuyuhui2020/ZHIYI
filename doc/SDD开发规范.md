# ZHIYI Spec Kit SDD 开发规范

> 状态：生效
> 适用范围：人类开发者、Codex、Claude Code 及其他 AI 编程工具
> 更新日期：2026-08-21

## 1. 目的

本项目把设计驱动开发从提示词约定升级为仓库门禁。目标不是机械要求“改代码
就改文档”，而是区分三类情况：

1. 违反项目宪法或分层架构：直接禁止。
2. 有意改变产品或技术语义：先同步 Spec Kit 工件和受影响文档。
3. 已批准设计内的实现细节：允许不改产品文档，但必须可追溯并说明理由。

Git 和 CI 使用同一个确定性检查器。AI 工具原生 Hook 只负责更早反馈，不是
唯一防线。

## 2. 事实源与目录

事实源优先级定义在 [项目宪法](../.specify/memory/constitution.md)。每个功能
使用独立目录：

```text
specs/NNN-feature-name/
├── spec.md          # 用户场景、需求、成功标准
├── plan.md          # 技术设计、接口、风险、宪法检查
├── tasks.md         # 测试优先的可执行任务和准确文件路径
└── drift-report.md  # 实现与设计对齐证据
```

`doc/` 保存产品和跨功能长期文档；`specs/` 保存具体变更的可执行规范。
`openspec/` 是历史草案，不再是新实现的事实源。

## 3. 强制工作流

AI 编程必须按以下顺序使用仓库内 Spec Kit skills：

1. 原则需要变化时运行 `$speckit-constitution`。
2. 运行 `$speckit-specify`，先写用户场景、FR 和可度量 SC。
3. 存在影响范围、状态、安全或兼容性的歧义时运行 `$speckit-clarify`。
4. 运行 `$speckit-plan`，完成架构、接口、数据、失败、回滚和 Constitution Check。
5. 高风险变更运行 `$speckit-checklist`。
6. 运行 `$speckit-tasks`；每个生产与测试文件必须使用准确路径。
7. 运行 `$speckit-analyze`；critical finding 未清零不得编码。
8. 运行 `$speckit-implement`，遵循 Red-Green-Refactor 和任务依赖。
9. 运行测试、静态检查和设计漂移检查。
10. 运行 `$speckit-converge`，把缺失工作追加回 `tasks.md` 并完成。

不能先写代码再补一个描述现状的 Spec。紧急修复也必须先建立最小、可验收的
feature spec；紧急程度可以缩短文档，但不能取消事实源。

## 4. Drift Report

从 `.specify/templates/drift-report-template.md` 创建
`specs/NNN-feature-name/drift-report.md`。机器读取以下字段：

```markdown
**Feature**: 001-example
**Status**: ALIGNED
**Docs-Impact**: UPDATED
**Docs-Updated**: doc/功能文档.md, doc/技术方案.md
**Docs-Impact-Reason**: 本次修改改变了公开事件和 Runtime 恢复语义。
**Reviewed-By**: AI+HUMAN
```

- `Status: BLOCKED` 会主动阻止提交。
- `Docs-Impact: UPDATED` 要求列出的文档存在且进入实际变更集；检查器还会
  验证文档是否覆盖被修改的模块。
- `Docs-Impact: NONE` 要求 `Docs-Updated: N/A`，并给出不少于 20 个字符的
  具体理由。
- 报告只是审查证据，不能豁免宪法、架构、测试或安全门禁。

## 5. 执行入口

| 入口 | 变更范围 | 作用 |
|---|---|---|
| `.githooks/pre-commit` | Git index 暂存快照 | 检查当前 feature、工件、任务追踪、漂移声明和架构 |
| `.githooks/pre-push` | 与 `origin/main` 的分支差异 | 确认整个 feature 和报告进入分支 |
| Claude `Stop` Hook | 暂存、未暂存、未跟踪文件 | 在 AI 准备停止时要求继续修复 |
| GitHub Actions | PR base 到 HEAD | 最终、不可由本地 `--no-verify` 绕过的门禁 |
| 手动命令 | 可选择范围 | 本地排查和审查 |

安装本地 Git Hooks：

```bash
./scripts/sdd/install_hooks.sh
git config --local --get core.hooksPath
```

常用检查：

```bash
# 当前完整工作区
python3 scripts/sdd/check_design_drift.py --worktree --gate manual

# 当前暂存区
python3 scripts/sdd/check_design_drift.py --staged --gate commit

# 当前分支相对主线
python3 scripts/sdd/check_design_drift.py \
  --base-ref origin/main \
  --gate push

# 机器可读结果
python3 scripts/sdd/check_design_drift.py --worktree --format json
```

新仓库没有首次提交或 `origin/main` 时，base-ref 检查会保守地检查所有已跟踪
和工作区文件，不会因缺少远端静默通过。

pre-commit 对策略、Spec Kit 工件、漂移报告和 Python 源码统一读取 Git index，
不会被“先暂存违规版本、再在工作区临时修好”的状态绕过。文档可以在 feature
分支的较早提交中完成，只要它仍位于当前分支相对 `origin/main` 的差异中。

### 5.1 远端仓库必须配置的门禁

本仓库当前没有 remote，因此以下规则无法由本地文件替代。创建 GitHub 仓库后
必须配置 Repository Ruleset 或 Branch Protection：

- `main` 禁止直接推送，只允许 Pull Request。
- 必须通过 `Spec Kit and design drift` required check。
- 禁止普通开发者跳过 required check；管理员绕过应只用于经审计的事故处理。
- 治理路径 `.specify/**`、`scripts/sdd/**`、`.githooks/**`、
  `.claude/settings.json`、`.github/workflows/sdd-governance.yml` 和根
  `AGENTS.md` 需要指定维护者评审。
- 确定 GitHub 组织/用户后补充真实 `CODEOWNERS`；不能提交无效占位 owner
  来制造“已保护”的假象。

CI 中第三方 Action 已固定到 v7 的完整 commit SHA，升级时要核对官方 release
并在独立治理 feature 中更新。

## 6. 失败处理

| 代码 | 含义 | 处理 |
|---|---|---|
| `SDD-001/002` | 缺少或冲突的当前 feature | 重新运行目标 Spec Kit feature，或显式指定 `--feature` |
| `SDD-003~005` | 工件缺失、空或有占位符 | 完成 Spec/Plan/Tasks/Report 后重试 |
| `SDD-006` | 实现文件不在任务中 | 先把准确文件路径加入 `tasks.md` |
| `SDD-007/008` | 工件未随提交或分支交付 | 暂存或加入当前 PR |
| `DRIFT-*` | 漂移报告与真实变更不一致 | 同步文档、补充有效理由或保持 BLOCKED |
| `ARCH-*` | 分层/导入硬违规或无法解析 | 重构到端口/适配器边界；不得用报告豁免 |
| `CONFIG-*/GIT-*` | 配置或仓库状态不可可靠判断 | 修复基础状态；检查按 fail-closed 处理 |

若实现本身错误，撤销实现；若设计确实需要改变，按
`spec → plan → tasks → analyze → implement` 顺序更新。禁止把
`Docs-Impact` 改成 `NONE`、缩短检查范围或关闭 Hook 来消除错误。

## 7. 文档影响映射

映射由 `.specify/governance/design-map.json` 版本化维护。当前规则：

| 实现区域 | 可能受影响的长期文档 |
|---|---|
| Domain / Application | 需求、功能、技术方案 |
| Runtime / Model / Context / Memory / Tool | 功能、技术方案 |
| API / Frontend | 需求、功能、技术方案 |
| Migration / Infra / Deploy | 技术方案、PROJECT |
| SDD / Hook / CI | 本文、AGENTS、PROJECT |

修改映射或降低规则强度本身是治理变更，必须先建立 Spec Kit feature 并获得
明确评审，不能作为修复业务变更失败的捷径。检查器还内置最小不可删除集合：
四类 feature 工件、核心实现路径、四层架构规则和关键治理文件不能仅靠修改
`design-map.json` 解除。

### 7.1 PostgreSQL 实现门禁

- 真实数据库模块必须在模块级声明 `postgresql` marker；快速门禁固定使用
  `not online and not postgresql`，数据库门禁独立使用 PostgreSQL 18.6 服务。
- CI 必须先证明 PostgreSQL 集合非空，且 contract/integration/performance 数据库路径不会
  被快速门禁收集；缺少数据库 URL 时真实库门禁失败，不能静默 skip。
- Schema 只能通过独立 `alembic upgrade head` 发布，并执行 `current --check-heads` 与
  `alembic check`；应用构造和启动禁止 `create_all`、stamp、upgrade 或自动修复。
- destructive downgrade/restore 只允许在身份已核验的一次性数据库执行，生产数据回退必须
  先备份并恢复到新数据库验证后切换。

## 8. Spec Kit 维护

本仓库使用官方生成的 `.specify/` 基础设施，项目扩展放在
`.specify/governance/`、`scripts/sdd/` 和 Hook 配置中，避免修改官方托管
文件。检查安装状态和升级集成：

```bash
uvx --from specify-cli specify integration status
uvx --from specify-cli specify integration upgrade codex
uvx --from specify-cli specify integration upgrade claude
```

升级后必须运行治理测试、JSON 校验、Shell 语法检查和设计漂移检查。
