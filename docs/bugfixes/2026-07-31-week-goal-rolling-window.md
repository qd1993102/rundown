# Bug: 周目标错误使用滚动七天窗口

- **发现日期**: 2026-07-31
- **修复日期**: 2026-07-31
- **严重程度**: major
- **影响范围**: AI 教练周目标完成度、MCP 周进度查询、训练历史统计

## 现象

AI 教练提示词要求调用 `get_training_history(days=7)` 计算“本周”完成度，导致上一自然周末的训练在周一至周六仍可能被计入当前周目标，周目标达成判断随每天滚动。

## 根因

负荷/恢复趋势的滚动七天窗口与训练计划的自然周统计共用了同一个历史工具，没有把“最近七天”和“本周”建模为不同时间边界。

## 修复方案

- 新增共享 `natural_week_bounds()`，统一返回报告日期所在自然周的周一与周日。
- 新增 AI 教练 `get_current_week_progress(date)` 和 MCP `query_current_week_progress`，只累计自然周周一至报告日期的实际训练。
- Prompt 明确周目标只使用自然周工具；`get_training_history(days=7)` 保留给滚动负荷和恢复趋势。
- 未同步日期单独计为数据未知，不进入训练次数、跑量或休息日统计。

## 相关文件

- [src/training_analysis.py](../../src/training_analysis.py) — 共享自然周边界。
- [src/coach.py](../../src/coach.py) — 自然周进度工具与历史状态过滤。
- [src/mcp_server.py](../../src/mcp_server.py) — MCP 自然周进度查询。
- [prompts/coach.md](../../prompts/coach.md) — 周目标和滚动趋势的调用边界。
- [docs/design/training-system.md](../design/training-system.md) — 自然周统计技术合同。

## 验证

- 以 2026-07-29（周三）为报告日期，查询窗口固定为 2026-07-27 至 2026-08-02，实际累计只读取周一至周三。
- 回归测试验证周目标工具不会读取上一周末数据。
- Prompt 回归测试禁止使用 `get_training_history(days=7)` 计算本周完成度。
- 全量 `pytest`：240 passed。
