# Bug: 浏览训练页周复盘会隐式写入进程决策

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: major
- **影响范围**: 训练页周复盘、训练进程决策、报告归档

## 现象

用户在训练页点击“自然周复盘”时，即使只是查看，也会保存新的 Progression Decision。周报、日报和同步的职责边界因此不清晰，用户无法区分阅读结论与确认训练变化。

## 根因

`TrainingService.review_week()` 同时承担周事实计算和决策持久化，训练页直接调用该方法，导致读操作带有写副作用。

## 修复方案

将 `review_week()` 收紧为纯候选计算；新增仅由用户显式触发的 `create_weekly_report()`，在报告域保存同一自然周快照。训练页改为跳转报告中心，报告只能深链训练页，不能确认、启用或推进方案。

## 相关文件

- [src/training.py](../../src/training.py) — 分离只读周复盘与显式周报写入。
- [src/web.py](../../src/web.py) — 新增周报与调整记录只读接口。
- [docs/design/training-system.md](../design/training-system.md) — 同步报告与训练的写入边界。

## 验证

- 连续调用 `review_week()` 不创建周报文件或训练决策文件。
- 显式调用周报接口后才产生 `reports/weekly/<week_id>.md`。
- 报告接口只返回训练深链，训练确认接口保持在 `/api/training/*`。
