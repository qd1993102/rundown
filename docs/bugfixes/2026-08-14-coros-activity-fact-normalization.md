# Bug: Coros 活动详情未归一化导致分段与下游事实失真

- **发现日期**: 2026-08-14
- **修复日期**: 2026-08-14
- **严重程度**: major
- **影响范围**: Coros 活动详情、日报训练深度分析、周报、训练草稿输入

## 现象

Coros 活动列表中的总距离、运动时长和平均心率正确，但详情中的距离、时长、
每公里配速、步频和步幅没有按 Provider 单位与字段名归一化。8.14126 km 样本曾生成
`distance_m=814126`、`duration_s=0`、12 个重复层级分段和
`quantity_gate.ratio=3.0`；`frequencyList` 已保存却没有进入 L2 聚合。

## 根因

统一摘要直接按 Garmin 风格的 `duration/pace/strideLength` 解析 Coros 的
`time/avgPace/avgStrideLength`，且没有处理百分之一米、百分之一秒单位。
`lapList` 同时返回公里、5 km 和全程层级，旧逻辑把三个层级的叶子段全部相加。
摘要提取器也未把 Coros `frequencyList` 转成内部 1 Hz 指标。

## 修复方案

- 原始 `detail_json` 保持不变；进入分段和 session-summary 前生成 Provider-neutral 副本。
- Coros 跑步距离除以 100 转米，时间除以 100 转秒，热量除以 1000 转 kcal，映射平均配速、心率、步频和步幅。
- 只选择叶子数最多的权威 lap 层级，分段距离仍由 `[0.85, 1.15]` 门禁校验。
- 将 `frequencyList` 的时间、配速、心率和步频聚合为 L2 输入，不把原始点传给报告或 AI。
- 重同步时识别旧的 L1、零时长或量门禁失败摘要，直接从本地原始详情幂等重建。
- 今日摘要从 session-summary 回填配速、步频和步幅；草稿最近两日保留 session-summary
  聚合事实和显式 gaps；活动重同步同时更新平均心率和训练负荷。

## 相关文件

- [src/providers/normalization.py](../../src/providers/normalization.py) — Provider Adapter 注册表与 Coros normalizer
- [src/summary_extraction.py](../../src/summary_extraction.py) — 仅消费 canonical detail 的 Hz/分段聚合
- [src/activity.py](../../src/activity.py) — 原始详情与归一化消费边界
- [src/main.py](../../src/main.py) — 存量摘要重建及活动字段更新
- [src/training_day_summary.py](../../src/training_day_summary.py) — 六项事实回填与 gap
- [src/training_planning.py](../../src/training_planning.py) — 草稿最近两日事实投影
- [docs/design/summary-extraction.md](../design/summary-extraction.md) — Provider 归一化设计

## 验证

- 脱敏 Coros 合同夹具验证 8141.26 m、2582.45 s、约 317 s/km、9 个权威分段和 L2 聚合。
- 本地真实只读详情回放验证 `quantity_gate.ratio=1.0`、平均步频约 161 spm、步幅约 117 cm。
- 对唯一受影响的本地 Coros 记录执行受控重建；重建前使用 SQLite 在线备份，重建后原始
  `detail_json` 不变，持久化摘要为 L2、9 个分段、`317.2 s/km`，分段距离比为 1.0。
- Garmin 既有分段和相关测试保持通过。
