# Bug: 无训练方案时无法生成周复盘

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 报告中心周复盘、周复盘 API、Coach Skill 周复盘合同

## 现象

用户没有 Training Goal、草稿或已生效 Training Scheme 时，调用
`POST /api/reports/weekly` 会返回 HTTP 409 和 `active_plan_required`。因此即使该自然周已经有
完整活动、恢复与同步事实，用户仍无法查看或归档周复盘。

## 根因

`TrainingService.review_week()` 通过训练首页读模型取得 `scheme` 和 `week`，并把二者同时作为
周复盘的必需输入；任一不存在就直接报错。数据模型也把实际周事实和计划执行摘要混在同一个
`execution_summary` 中，Coach Skill 注册表进一步把 `active_scheme` 声明为必需事实，页面则
无条件渲染计划完成率和 Progression Decision。

## 修复方案

- 先独立组装 `actual_summary` 与 `data_quality`，不依赖目标、草稿或训练方案；
- 按目标自然周的历史生效区间可选解析 `plan_context`，只在存在关联时生成
  `plan_execution_summary`、`adaptation_signal` 和 `progression_decision`；
- 方案仅覆盖部分自然周时，将完成率和偏差计算限制在实际生效窗口；
- 为已结束方案写入 `effective_to`，并兼容用历史 `closed_at` / `superseded_at` 推导结束日期，避免后续周误关联；
- 将 `review-training-week` 的必需事实改为 `natural_week_actual`，无方案时跳过计划执行贡献 Skill；
- 报告页优先展示实际运动摘要，无方案时不显示虚假的 `0 / 0 km` 或“尚未形成决定”。

## 相关文件

- [src/training.py](../../src/training.py) — 拆分基础周事实与可选方案比较，允许无方案生成和归档
- [src/coach_runtime/registry.py](../../src/coach_runtime/registry.py) — 更新周复盘 Skill 必需事实
- [prompts/skills/review-training-week/SKILL.md](../../prompts/skills/review-training-week/SKILL.md) — 明确无方案输出边界
- [web/templates/reports.html](../../web/templates/reports.html) — 支持可空方案关联和基础周复盘展示
- [docs/design/training-system.md](../design/training-system.md) — 同步领域对象、API 和测试合同
- [docs/design/memory-system.md](../design/memory-system.md) — 同步周报告持久化 Schema 与可空字段

## 验证

- 无 Goal、无草稿、无方案的已结束自然周可生成并归档正式周复盘；
- 无方案的当前自然周返回不持久化的 `weekly_checkpoint`；
- 有方案周继续返回计划执行与 Progression Decision；
- 方案仅覆盖部分自然周时只计算生效窗口；
- 已结束方案不会关联到关闭日期之后的自然周；
- Web API 无方案路径返回 HTTP 200，页面可渲染空方案关联；
- 完整 `pytest`：350 passed。
