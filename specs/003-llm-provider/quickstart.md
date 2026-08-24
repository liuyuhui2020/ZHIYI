# Quickstart: Validate LLM Provider Gateway

本指南用于验证本 Feature，不启动 Runtime API、数据库或真实 Agent Loop。

## Prerequisites

- Python 3.12
- uv（使用仓库锁文件）
- 默认验证无需 OpenAI/Anthropic 账号、密钥或外网

## Install

```bash
uv sync --all-groups --frozen
```

预期：安装根 `pyproject.toml` 与 `uv.lock` 已锁定依赖，不修改 lockfile。

## Default offline verification

```bash
uv run pytest -m "not online" -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

预期：Fake Provider、OpenAI/Anthropic 离线契约、Gateway 单元、并发、超时、重试、
Fallback、熔断、流中断、Structured Output 和秘密脱敏测试全部通过。

## Focused contract verification

```bash
uv run pytest tests/contract/models -q
uv run pytest tests/unit/application/services/test_model_gateway.py -q
uv run pytest tests/performance/test_model_gateway_overhead.py -q
```

重点验收：

- 两个真实 adapter 的协议桩产生相同平台契约。
- 能力不兼容在任何 Provider 调用前失败。
- 流式首个可见增量后不重试或 Fallback。
- 取消后不发起新尝试。
- 公开错误、repr 和日志不包含测试假密钥或敏感正文。
- 本地 Gateway 额外处理延迟满足 [spec.md](./spec.md) 的 SC-009。

## Optional online smoke

在线调用默认关闭。只在独立测试账号和受控环境中使用：

```bash
ZHIYI_RUN_PROVIDER_SMOKE=1 \
OPENAI_API_KEY='test-account-secret' \
ANTHROPIC_API_KEY='test-account-secret' \
uv run pytest -m online tests/integration/models -q
```

不要把密钥写入 `.env`、测试快照、命令历史共享记录或 CI 日志。在线测试每个 Provider
最多执行一次短请求；缺少任一密钥时对应测试跳过而不是失败。

## Governance verification

```bash
python3 scripts/sdd/check_design_drift.py --worktree --gate manual
python3 -m unittest discover -s scripts/sdd/tests -v
```

预期：当前 Feature 可解析，全部实现/测试路径被 `tasks.md` 追踪，长期文档影响与
`drift-report.md` 一致，架构依赖检查通过。

## Contract references

- [Model Gateway behavior](./contracts/model-gateway.md)
- [Platform data model](./data-model.md)
- [Feature acceptance criteria](./spec.md)
