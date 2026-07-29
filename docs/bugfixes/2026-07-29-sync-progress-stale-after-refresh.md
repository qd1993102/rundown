# Bug: Garmin 批量同步进度长时间停在阶段 2/4

- **发现日期**: 2026-07-29
- **修复日期**: 2026-07-29
- **严重程度**: major
- **影响范围**: Garmin Web 批量同步任务、轮询进度和刷新恢复

## 现象

批量同步实际持续逐日推进并最终成功，但任务卡在健康指标阶段长时间只显示 `2 / 4`，`updated_at`
也不变化。用户刷新页面后仍看到同一粗粒度阶段，容易误判后台假死；直到任务完成才突然变为 `4 / 4`。

## 根因

Web 任务只在认证、整段健康指标、活动和备份边界更新进度。`Storage.sync_range()` 内部虽然通过
garmy `ProgressReporter` 为每个日期/指标发出完成、跳过或失败事件，但这些事件没有适配到
`SyncTaskStore`。前端又只认识顶层四阶段结构，因此无法显示长耗时阶段内部的真实推进。

## 修复方案

- 新增 garmy 进度 Reporter 适配器，保留原日志行为并把逐项事件转换为真实计数、日期、指标和结果。
- 顶层四阶段字段保持不变，逐项明细持久化到 `progress.items`，按日期、时间间隔和最终项节流写盘。
- 同步页增加独立的阶段内进度条与明细；页面刷新后从轮询接口恢复同一持久化快照。
- CLI/MCP 不传进度回调时行为不变。

## 相关文件

- [src/storage.py](../../src/storage.py) — 适配 garmy 逐项进度钩子。
- [src/main.py](../../src/main.py) — 将 Garmin 指标进度桥接到任务阶段回调。
- [src/sync_tasks.py](../../src/sync_tasks.py) — 持久化嵌套逐项进度。
- [web/templates/sync.html](../../web/templates/sync.html) — 展示并恢复阶段内真实进度。
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 更新任务进度 Schema 与轮询行为。

## 验证

- 单元测试覆盖 Reporter 的开始、完成、跳过、失败和最终计数。
- 任务状态测试覆盖 `progress.items` 原子持久化与读取。
- Web 合同测试覆盖运行中轮询返回逐项进度，以及页面保持四阶段条并显示独立明细条。
- 完整 `pytest` 零失败，并重启本地服务验证最新代码。
