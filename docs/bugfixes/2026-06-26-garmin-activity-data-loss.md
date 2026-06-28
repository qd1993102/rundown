# Bug: Garmin 活动数据丢失导致日报显示为休息日

- **发现日期**: 2026-06-26
- **修复日期**: 2026-06-26
- **严重程度**: critical
- **影响范围**: 日报生成、活动查询、Garmin 数据同步

## 现象

用户 6 月 25 日有跑步数据（Garmin 手表记录），但 `rundown daily --date 2026-06-25` 生成的日报显示为休息日（`is_rest_day: true`，无活动记录）。

## 根因

共发现 **3 层 bug**：

### Bug 1: `GarminActivity.fetch_activities()` 永远返回空

[src/providers/garmin.py](src/providers/garmin.py) `fetch_activities()` 调用 `ActivitySummary.to_dict()` 获取活动字段，但 garmy 的 `ActivitySummary.to_dict()` 返回空字典 `{}`。导致 `start_time_local` 取到空字符串，日期过滤 `"" <= "2026-06-25"` 永远为 False，所有活动被丢弃。

### Bug 2: garmy ActivitiesIterator 状态 bug

garmy SyncManager 的 `ActivitiesIterator` 是单向游标（从新到旧消费活动），但 `sync_range` 按日期升序处理（23→24→25→26）。处理完 6/23 后游标已越过 6/24-26，后续日期的 `get_activities_for_date()` 返回空。

### Bug 3: `cmd_daily` 不自动同步

`rundown daily` 普通路径（不带 `--image`）不触发数据同步，直接读本地 DB 生成报告。即使 Garmin Connect 有新数据也不会拉取。

## 修复方案

1. **`GarminActivity.fetch_activities()`**: 改为直接读取 raw API 响应（`aa.raw(limit=200)`），绕开 `to_dict()` 问题。同时解决 distance 缺失问题（`ActivitySummary` 不解析 `distance` 字段）。
2. **新增 `_sync_garmin_activities()`**: 在 `cmd_sync` 中 SyncManager 同步后，用修复好的 `fetch_activities()` 直接将活动写入 DB，绕开 garmy ActivitiesIterator 的状态 bug。
3. **`cmd_sync` 增加 `reset_pending_metrics()`**: 同步前清理 `sync_status` 表中的 `pending`/`failed` 记录。
4. **`cmd_daily` 增加自动同步**: 普通路径在生成日报前先同步数据。
5. **`_enrich_activity_distances` 修复**: 检查条件从 `is not None` 改为 truthy 判断。`distance_meters=0`（DB 默认值）被错误当作"已有距离"跳过了 API 补全。
6. **`_sync_garmin_activities` 增加 UPDATE**: 对已存在但 `distance_meters=0` 的记录执行 UPDATE 补全，不只是 INSERT 新记录。
7. **HTML 标题修正**: `render.py` 中 "昨日训练" → "今日训练"。

## 相关文件

- [src/providers/garmin.py](src/providers/garmin.py) — `fetch_activities()` 改用 raw API 响应
- [src/main.py](src/main.py) — 新增 `_sync_garmin_activities()`、`reset_pending_metrics()`、`cmd_daily` 自动同步
- [docs/design.md](docs/design.md) — 新增 Section 4.7 AI 教练模块，更新 8.4.3 上下文注入策略
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — 记录所有修复

## 验证

1. 运行 `rundown sync --days 3` → 确认 activities 不再 stuck 在 pending
2. 运行 `rundown daily --date 2026-06-25` → 确认日报显示「静安区 跑步 15.0km 79min」
3. 查询 DB 确认 6/25 activities 已入库
