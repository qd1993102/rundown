# 移除教练模式转换提案模块

- **日期**: 2026-08-12
- **类型**: refactor

## 背景与动机

用户确认删除"模式转换待确认"模块。`design/training-system.md` 已把
`continuous_running | race_preparation | recovery_transition`、`coaching-context.md`
和模式转换接口标记为待移除复杂度；目标架构只保留有目标赛事的单一 Training Scheme，
无目标时不创建方案。

## 方案选择

- **删除**：提案模块（propose/confirm/reject/_apply/pending/存储/API/MCP/前端"模式转换待确认"区块）、
  `_ensure_race_transition`、激活流程的模式转换拦截。
- **保留**：`coaching_mode` 概念与 `race_strategy`/`propose_scheme_revision` 的
  `race_preparation` 检查——改为由生效方案**派生**（`get_coaching_context` 有 active
  备赛方案 → `race_preparation`，否则 `continuous_running`），不再持久化
  `coaching-context.md`、不再需要提案确认切换。
- **放弃**：recovery_transition 模式与"恢复过渡"UI（依赖提案，随提案一并移除）。

## 实现步骤

1. `src/training.py`：删提案方法/存储/`_ensure_race_transition`/激活拦截/输出字段；
   `get_coaching_context` 改为派生。
2. `src/web.py`：删 3 个 mode-transitions API 路由。
3. `src/mcp_server.py`：删 3 个模式转换 tools。
4. `web/templates/training.html`：删"模式转换待确认"区块与恢复过渡 UI。
5. 测试：删 `test_training.py` 3 个提案测试、`test_web.py` 区块断言、
   `test_registration.py` MCP tool 断言。
6. 文档：training-system.md / ai-coaching.md / product/training-experience.md / CHANGELOG。

## 遇到的问题与解决

- `_planning_week_pattern` 的 `@staticmethod` 装饰器在删除 `_ensure_race_transition` 时被
  连带删除，导致大量测试 TypeError；已恢复装饰器。
- 前端删除脚本对 `async function` 处理有缺陷：以 `function X(` 定位 async 函数时
  命中函数名而非行首，残留 3 个孤立 `async` 行并误删 `intensityZoneLabel` 定义
  （含测试断言的 `Z2 轻松有氧` 标签）；已清理残留并重建五级强度带标签。
- `close_active_scheme` 仅被提案确认调用；删除提案后保留 repository 方法（
  未来"结束备赛"入口复用），但不再有调用路径——目标归档（archive_goal）独立于本模块。
  **注意**：删除后"结束备赛/恢复过渡"UI 入口消失，用户目前无法通过 UI 关闭备赛方案；
  如需保留该能力，应补一个独立的"结束备赛"入口（归档目标 + 关闭方案）。
- `coaching-context.md` 旧文件保留读取兼容（旧数据可读），新逻辑以派生为准。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [training-system.md](../design/training-system.md)、[ai-coaching.md](../design/ai-coaching.md)
- Product: [training-experience.md](../product/training-experience.md)
