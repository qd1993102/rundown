# Bug: Coros 活动时长错误包含暂停时间

- **发现日期**: 2026-07-19
- **修复日期**: 2026-07-19
- **严重程度**: major
- **影响范围**: Coros 活动同步、训练时长汇总、配速与训练负荷展示

## 现象

Coros 活动同步后的 `duration_seconds` 偏大，日报中的运动时长包含活动暂停期间的经过
时间。距离不变时，这还会让基于总距离和时长展示的平均配速明显偏慢。

## 根因

Coros `/activity/query` 同时返回：

- `totalTime`：包含暂停的总经过时间。
- `workoutTime`：排除暂停的实际运动时间。

项目原先通过 coros-mcp 的 `ActivitySummary.duration_seconds` 读取时长；该上游模型只将
`totalTime` 映射为 `duration_seconds`，没有暴露 `workoutTime`，导致暂停时间被写入
SQLite。与此同时，统一入库流程对已存在活动只跳过、不更新，使后续修正无法覆盖旧值。

## 修复方案

- Coros Provider 直接分页读取 `/activity/query` 原始列表，保留 `workoutTime`。
- `duration_seconds` 优先使用合法的正数 `workoutTime`；缺失、为零或大于
  `totalTime` 时回退 `totalTime`。
- 在 `ActivityData.extra` 记录 `workout_time_seconds`、`total_time_seconds` 和
  `paused_seconds`，明确数据口径。
- 非 Garmin 活动重同步时比较已有 `duration_seconds`，有差异则原地更新。
- Coros API 返回非成功 result 或 token 失效时抛出可操作错误，不再返回空列表并误报
  同步成功。
- `result=1019` 时将用户连接状态更新为 `expired`，同步页展示重新绑定入口，并沿用
  原 API key 和用户数据目录完成绑定。

## 相关文件

- [src/providers/coros.py](../../src/providers/coros.py) — 原始活动分页与实际运动时长映射
- [src/main.py](../../src/main.py) — 重同步更新已有活动时长
- [src/web.py](../../src/web.py) — token 失效状态与重新绑定响应
- [web/templates/sync.html](../../web/templates/sync.html) — 重新绑定入口
- [tests/test_providers.py](../../tests/test_providers.py) — 暂停时间与回退规则测试
- [tests/test_storage.py](../../tests/test_storage.py) — 已有活动时长更新测试
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 同步 Coros 时长口径

## 验证

- 构造 `totalTime=4200`、`workoutTime=3600` 的暂停活动，确认统一时长为 3600 秒、
  `paused_seconds=600`。
- 构造缺少 `workoutTime` 的旧响应，确认安全回退 `totalTime`。
- 同一活动先以 4200 秒入库、再以 3600 秒重同步，确认 SQLite 更新为 3600 秒。
- 确认 `result=1019` 被识别为认证失效，HTTP 返回重新绑定提示。
- 运行完整 `pytest`，确认零失败。
