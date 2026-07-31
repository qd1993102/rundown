# Bug: 未同步到运动记录时被误判为休息日

- **发现日期**: 2026-07-31
- **修复日期**: 2026-07-31
- **严重程度**: major
- **影响范围**: 日报、历史训练上下文、AI 教练、Web/CLI/MCP 展示

## 现象

指定日期没有本地活动记录时，日报和教练上下文直接显示“休息日”。当该日期尚未同步、同步失败或同步覆盖无法确认时，这会把数据缺失误说成运动者主动休息。

## 根因

`MemoryWriter._summarize_activities()` 只接收活动列表和健康数据，并把空活动列表固定映射为 `is_rest_day=true`。系统已有逐日 Provider 同步状态，但日报聚合与历史工具没有消费这份覆盖证据，展示入口只能依赖错误的二态字段。

## 修复方案

- 引入统一 `activity_state`：`training`、`confirmed_rest`、`unknown`。
- 有活动事实时为 `training`；只有 `neurun_provider_sync=completed` 且无活动时为 `confirmed_rest`；其余空活动日均为 `unknown`。
- `is_rest_day` 作为兼容字段，只在 `confirmed_rest` 时为 `true`。
- 日报、规则洞察、AI 工具历史、Web、CLI、HTML 与 MCP 均消费同一状态；`unknown` 统一表达为运动数据未同步或状态未知。

## 相关文件

- [src/training_analysis.py](../../src/training_analysis.py) — 定义统一每日运动状态。
- [src/memory.py](../../src/memory.py) — 读取同步覆盖并生成日报及历史状态。
- [src/coach.py](../../src/coach.py) — 排除未知日期，不再计作训练或休息。
- [docs/design/training-system.md](../design/training-system.md) — 记录 Activity Data Coverage 与三态合同。

## 验证

- 复现测试确认空活动且无同步覆盖时为 `unknown`，不是休息日。
- 同步覆盖已完成且无活动时仍正确标记 `confirmed_rest`。
- HTML 报告显示“运动数据未同步”，不出现休息日标题。
- 全量 `pytest`：240 passed。
