# 周进度手动刷新与数据新鲜度引导

- **日期**: 2026-08-14
- **类型**: feature

## 背景与动机

用户反馈核心痛点：同步完数据后，训练页/报告页的周进度不会自动更新，且没有任何提示告诉用户"数据有更新、进度已过期"。经方案评审确认三条原则：

1. 同步后周进度**不自动重算**，由用户决定是否更新（避免 AI 调用风暴与意外覆盖）；
2. 但必须给出**明确的数据状态引导**（数据截至、缺几天、有新数据），不能静默过期；
3. 按钮采用**渐进披露**：常态弱化，有更新时高亮，缺同步时引导去同步。

## 方案选择

| 方案 | 结论 |
|---|---|
| 同步完成后自动重算 checkpoint | 放弃：用户要求保留控制权；AI 任务有等待成本 |
| 仅手动按钮、无状态提示 | 放弃：静态按钮无法引导用户感知"需要更新" |
| **手动刷新按钮 + 三态新鲜度徽标（渐进披露）** | 采纳：状态由数据驱动（`last_sync` vs `data_as_of`），按钮形态随状态切换 |

## 实现步骤

1. 后端：`/api/reports/weekly` POST 响应附带 `last_sync`（修正了一处误区——`result_mapper` 只写入任务记录，HTTP 响应体需单独加字段）。
2. 报告页：`weeklyFreshnessLevel` 纯函数三态判定（incomplete 优先于 stale）；`weeklyFreshnessMarkup` 徽标 + 渐进披露按钮；已归档周重新生成前 `confirm` 提示覆盖；页面加载 `GET /api/user` 获取 `last_sync`。
3. 训练页：`weekRhythmCardMarkup` 从 `renderActive` 中提取为独立卡渲染函数；新增"刷新进度"按钮（重新 `GET /api/training/home` 仅替换该卡）与数据状态行（`sync_coverage.checked_through/covered_days/expected_days`）。
4. 测试：模板断言 + weekly POST `last_sync` 后端断言 + Playwright 三态/按钮/刷新请求验证。

## 遇到的问题与解决

- **`result_mapper` 与 HTTP 响应体混淆**：`ai_inference_coordinator.run()` 返回 work 原始结果，`result_mapper` 结果只进任务记录。最初把 `last_sync` 放进 mapper 导致响应缺失，改为直接放在 `JSONResponse` 中。
- **训练页卡内事件重绑定**：替换卡 DOM 后 `data-session-detail` 点击事件需重新绑定，`refreshWeekProgress` 内对替换后的卡内元素补绑。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/training-system.md §7.2](../design/training-system.md)
- Product: [docs/product/daily-report.md](../product/daily-report.md)、[docs/product/training-experience.md](../product/training-experience.md)
