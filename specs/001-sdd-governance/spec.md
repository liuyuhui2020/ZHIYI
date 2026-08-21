# Feature Specification: SDD Governance and Design Drift Gates

**Feature Branch**: `001-sdd-governance`

**Created**: 2026-08-21

**Status**: Approved

**Input**: User description: "AI 编程必须使用 Spec Kit 的 SDD 流程；设置 Hook 检测实现与设计漂移，漂移时同步文档或禁止修改。"

## User Scenarios & Testing

### User Story 1 - 在编码前建立可追溯设计 (Priority: P1)

作为开发者或 AI 编码 Agent，我必须在修改产品实现前完成当前功能的
Spec、Plan 和 Tasks，从而让每项实现都有可审查的需求与设计依据。

**Why this priority**: 没有编码前门禁，后续漂移检测只能事后猜测设计意图。

**Independent Test**: 暂存一个没有完整 Spec Kit 工件的实现文件，提交 Hook
必须拒绝并指出缺失工件；补齐工件和任务路径后检查通过。

**Acceptance Scenarios**:

1. **Given** 实现文件已暂存且当前功能缺少 `plan.md`，**When** 执行 pre-commit，**Then** 提交被拒绝并给出修复命令和缺失路径。
2. **Given** Spec、Plan、Tasks 完整且实现文件在任务中可追溯，**When** 执行检查，**Then** SDD 完整性门禁通过。

---

### User Story 2 - 发现并处理设计漂移 (Priority: P1)

作为架构负责人，我希望系统将漂移分成架构硬违规、需要同步文档的语义
变化和无需文档变更的实现细节，避免静默偏离设计，也避免无意义文档更新。

**Why this priority**: 简单地要求“代码和文档同时变化”无法识别架构违规，也
会导致形式主义更新。

**Independent Test**: 分别提交禁止的分层导入、声明已同步文档的语义变化和
有充分理由的实现细节，验证系统依次阻止、验证文档和允许提交。

**Acceptance Scenarios**:

1. **Given** Domain 文件导入 LangChain 或 SQLAlchemy，**When** 执行任一门禁，**Then** 无论漂移报告如何声明都必须拒绝。
2. **Given** 漂移报告声明 `Docs-Impact: UPDATED`，**When** 没有相应文档进入变更集，**Then** 门禁拒绝。
3. **Given** 变更属于已批准设计内的实现细节，**When** 报告声明 `Docs-Impact: NONE` 并给出有效理由，**Then** 门禁允许继续。

---

### User Story 3 - 跨 AI 工具一致执行 (Priority: P2)

作为项目维护者，我希望 Codex、Claude Code、人类 Git 客户端和 CI 共用同一个
确定性检查器，从而不依赖某个 AI 产品的私有 Hook。

**Why this priority**: AI 工具 Hook 只能改善本地反馈，Git 和 CI 才能提供跨工具
的一致底线。

**Independent Test**: 从 Git pre-commit、pre-push、Claude Stop Hook 和 CI 命令
调用同一检查器，确认相同输入得到相同违规代码。

**Acceptance Scenarios**:

1. **Given** Claude Code 即将停止且工作区存在未处理漂移，**When** Stop Hook 运行，**Then** Claude 被要求继续修复，并获得可执行原因。
2. **Given** 开发者跳过本地 Hook，**When** 分支进入 CI，**Then** 同一违规仍被拒绝。
3. **Given** 仓库尚无提交或远端，**When** 安装并运行 Hook，**Then** 检查器安全退化到暂存区/工作区，不崩溃也不静默跳过。

### Edge Cases

- 仓库没有 `origin/main`、没有首次提交或处于 detached HEAD。
- 工作区同时存在已暂存、未暂存和未跟踪文件。
- 分支名包含 `codex/` 等工具前缀，或通过 `SPECIFY_FEATURE` 显式指定功能。
- 一次变更误触多个 feature 目录，当前功能无法唯一解析。
- Python 文件语法错误、配置 JSON 损坏、Spec Kit 工件仍有模板占位符。
- Claude Stop Hook 再次触发自身，必须避免无限阻止循环。
- 文档声明已更新但文件不存在，或没有进入实际变更集。

## Requirements

### Functional Requirements

- **FR-001**: 仓库 MUST 使用官方 Spec Kit 目录、模板、脚本和 Codex/Claude 集成。
- **FR-002**: 项目 MUST 在 `.specify/memory/constitution.md` 定义不可绕过的 SDD、架构、测试、安全和漂移原则。
- **FR-003**: 实现变更 MUST 能唯一关联 `specs/NNN-feature-name/`，且 `spec.md`、`plan.md`、`tasks.md` 和 `drift-report.md` 完整无阻塞占位符。
- **FR-004**: 每个实现文件路径 MUST 在当前 `tasks.md` 中出现。
- **FR-005**: 漂移报告 MUST 声明对齐状态、文档影响、文档列表和判断理由。
- **FR-006**: 架构硬违规 MUST 直接失败，不能通过修改漂移报告或普通产品文档豁免。
- **FR-007**: 声明文档已更新时，受影响文档 MUST 存在并进入当前变更集。
- **FR-008**: 声明无文档影响时 MUST 提供具体、非占位的理由。
- **FR-009**: 检查器 MUST 支持 worktree、staged 和 base-ref 三种变更范围。
- **FR-010**: 原生 Git pre-commit、pre-push、Claude Stop Hook 和 GitHub Actions MUST 调用同一个检查器。
- **FR-011**: Codex 和其他读取 `AGENTS.md` 的工具 MUST 在根目录获得强制 SDD 指令；Claude Code MUST 通过 `CLAUDE.md` 和项目 Hook 接入。
- **FR-012**: 检查失败 MUST 输出稳定违规代码、文件位置、原因和修复方向，并返回非零退出码。
- **FR-013**: 检查器 MUST 仅依赖 Python 标准库，避免门禁本身依赖尚未安装的应用环境。
- **FR-014**: 配置错误和 Git 命令错误 MUST fail closed，不能被当作“无漂移”。
- **FR-015**: 老的 `openspec/` 草案 MUST 明确标为历史输入，不得作为新实现事实源。
- **FR-016**: 架构验证 MUST 先按版本化规则筛选路径，再读取或解析受约束的 Python 源文件；不受架构规则约束的路径不得产生无意义的文件系统读取。

### Key Entities

- **Feature Context**: 当前 Spec Kit 功能目录及其解析来源。
- **Changed File Set**: worktree、staged 或 base-ref 范围内的标准化仓库相对路径集合。
- **Drift Finding**: 稳定代码、严重级别、文件、原因和修复建议。
- **Drift Report**: 功能级人工/Agent 对齐声明，不替代静态硬门禁。
- **Design Map**: 实现路径、文档映射、忽略路径和架构导入规则的版本化配置。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 所有实现变更在提交前均可关联到唯一 Spec Kit feature，缺失工件场景检出率为 100%。
- **SC-002**: 配置中定义的 Domain/Application/Runtime/API 禁止导入测试场景检出率为 100%。
- **SC-003**: 文档影响声明与实际变更集不一致的测试场景检出率为 100%。
- **SC-004**: 检查器在无远端、无首个提交和正常 feature 分支三种仓库状态下均可得到确定结果。
- **SC-005**: Git、Claude 和 CI 入口复用单一检查逻辑，无重复漂移规则实现。
- **SC-006**: 纯文档或纯 Spec 变更不因缺少实现任务而误报。
- **SC-007**: 本地 staged 检查在 5,000 个变更路径下目标耗时小于 2 秒，不执行网络调用。

## Assumptions

- Python 3.9+ 和 Git 是治理工具的环境基础；产品运行时仍以 Python 3.12 为目标。
- GitHub Actions 是首个 CI 示例，检查器本身与 CI 平台无关。
- 本地 Hook 可被 `--no-verify` 跳过，因此受保护分支必须以 CI 结果作为最终门禁。
- `drift-report.md` 是审查证据，不是自动证明；高风险变更仍需人类架构/安全评审。
- 当前旧 `openspec/` 内容保留以避免未经授权删除历史资料，但不再具有规范优先级。
