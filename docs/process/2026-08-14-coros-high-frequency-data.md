# 探索与接入 Coros Hz 级分段配速数据

- **日期**: 2026-08-14
- **类型**: feature（数据管线增强：Coros 分段与高频时序）

## 背景与动机

用户提出：Coros 是否提供 Hz 级分段配速数据。调研发现：

1. Coros `/activity/detail/query` 响应的 `data` 里包含高频时序数组
   `graphList` / `frequencyList` / `gpsLightDuration`，但第三方库 coros-mcp 的
   `fetch_activity_detail` 为减小响应体积**主动丢弃**了这三个字段；
2. neurun 的 `_extract_splits` 与 `build_session_summary` 的分段候选列表
   （`lapDTOs/splitSummaries/splits/laps/intervals/segments`）**不包含 Coros 的
   `lapList`**，导致 Coros 用户 `activity_splits` 恒为空、日报"强度分布/配速节奏"
   永远显示"无分段数据"；
3. 顺带发现：Coros `/analyse/query` 返回平台自算乳酸阈值 `lthr`（bpm）与 `ltsp`（s/km），
   neurun 此前未解析。

## 方案选择

- **放弃**：修改 venv 里的 coros-mcp 库（升级/替换依赖会漂移，且与 neurun 的分段
  schema 无关）；
- **选择**：neurun 自建请求直接调 `/activity/detail/query`（保留全部字段），
  `sportType` 从活动项 `extra` 携带；分段候选与 `build_session_summary` 增加
  `lapList`（`lapItemList` 子项优先展开）；lthr/ltsp 解析进 `DailyHealth.extra`。

## 实现步骤

1. `src/providers/coros.py`
   - `_parse_activity_item`：活动 `extra` 携带原始 `sport_type`；
   - `CorosActivity.fetch_activity_detail(activity_id, sport_type=0)`：自建
     `httpx.post` 请求，保留 `graphList`/`frequencyList`/`gpsLightDuration` 等全部字段，
     并对高频字段记录结构日志（type/len），便于确认 Hz 数据格式；
   - `CorosHealth.fetch_daily_health`：从 `/analyse/query` 的 dayList 解析 `lthr`/`ltsp`
     放入 `DailyHealth.extra`。
2. `src/activity.py` `_extract_splits`：分段候选增加 `lapList`，`lapItemList` 子项
   优先展开为顺序分段。
3. `src/summary_extraction.py` `build_session_summary`：`_list()` 增加 `lapList`
   及 `_flatten_lap_list` 展开，Coros 分段进入 L1 分析（强度分布/结构/地形）。
4. `src/main.py` `_sync_activity_details`：拉详情时按 Provider 传 `sport_type`
   （`TypeError` 兼容仅单参数的 Garmin/Huawei）。

## 遇到的问题与解决

- 本地 Coros token 已过期（`result=1001`），无法实测 `graphList`/`frequencyList`
  的确切结构；因此本轮只打通"取数保留 + 分段入库"，Hz 逐点解析的 schema 与
  消费留待拿到有效 token 后的真实响应确认。
- `CorosActivities` 类名在源码里是 `CorosActivity`（单数），测试导入时发现并修正。

## 验证

- 新增测试：lapList 展开（含/不含 lapItemList）、fetch_activity_detail 保留高频字段、
  错误响应返回空、lthr/ltsp 解析进 extra；
- `pytest` 全量零失败（515 passed, 1 xfailed）。

## 实测确认（token 重新有效后）

- `frequencyList` 为 **1Hz 逐点序列**（43 分钟课 = 2584 点），每点含
  `timestamp`（步长 100=1s）、`heart`、`speed`、`cadence`、`distance`；
- `lapList.lapItemList` 展开为逐段（avgHr/avgPace/avgCadence/distance），
  `_extract_splits` 实测 12 段；
- 平台阈值实测：该用户 `lthr=169` bpm、`ltsp=265` s/km、vo2max 51。

## 后续落地：统一归一化与 L2 聚合

真实详情回放进一步确认 Coros 使用百分之一米和百分之一秒，且 `lapList` 同时返回
公里、5 km 和全程三个层级。实现新增 Provider-neutral 副本：原始 JSON 保持可回放，
摘要侧把距离、时长、`avgPace/avgHr/avgCadence/avgStrideLength` 归一到统一合同；
只选择叶子数最多的权威 lap 层级，避免父子重复累计。

`frequencyList.timestamp` 的步长 100 归一为 1 秒权重，`speed` 按 sec/km、`heart`
按 bpm、`cadence` 按 spm 聚合进 L2 强度与步频事实。原始点仍不进入报告、周报或
训练草稿 AI 载荷。存量错误摘要可在下一次覆盖对应日期的同步中从本地详情幂等重建，
不需要删除活动或重新请求已经保存的详情。本地唯一受影响的真实记录已先完成 SQLite
在线备份，再从原始详情受控重建；原始 JSON 保持不变，分段从重复的 12 段收敛为权威 9 段。

真实 8.14126 km 样本回放结果：2582.45 s、平均配速约 317.2 s/km、9 个权威分段、
分段距离比 1.0、平均步频约 161 spm、平均步幅约 117 cm，粒度为 L2。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/summary-extraction.md](../design/summary-extraction.md)
- Product: [docs/product/data-source-sync.md](../product/data-source-sync.md)
