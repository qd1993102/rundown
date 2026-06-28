# Changelog

Rundown 项目变更日志，按日期倒序。

---

## 2026-06-26

### Fixed
- **Garmin 活动数据丢失**: `GarminActivity.fetch_activities()` 因 garmy `ActivitySummary.to_dict()` 返回空字典，导致所有活动被过滤掉。改为直接读取 raw API 响应。
- **Garmin 活动距离缺失**: `ActivitySummary` 不解析 `distance` 字段，改为从 raw API 响应直接获取。
- **garmy ActivitiesIterator 状态 bug**: garmy SyncManager 的 ActivitiesIterator 是单向游标，按日期升序处理时跳过后面的日期。新增 `_sync_garmin_activities()` 绕过此问题，直接将活动写入 DB。
- **Sync 不清理 pending 记录**: `cmd_sync` 同步前不清理 `sync_status` 表中的 `pending`/`failed` 记录，导致 garmy SyncManager 可能跳过重试。新增 `reset_pending_metrics()` 调用。
- **`cmd_daily` 不自动同步**: 普通路径（不带 `--image`）不自动同步数据，导致日报显示过期内容。新增自动同步逻辑。
- **Coros activity_date 解析错误**: `_sync_coros` 错误地将已格式化的 `start_time` 字符串当作 Unix 时间戳解析。

### Changed
- **AI 教练上下文增强 (`coach.py`)**: 从仅收集 7 天日报摘要，扩展为多源收集：
  - 新增 `_collect_profile()` — 读取竞技档案（PB、身体数据）
  - 新增 `_collect_preferences()` — 读取训练偏好
  - 重写 `_collect_goals()` — 读取目标 body 正文（含配速表），而非仅 FM
  - 扩展 `_collect_history()` — 增加近 3 天训练细节（配速、步频、功率）
  - 更新 `_build_coach_prompt()` — 提示 AI 结合运动员竞技水平给出针对性建议
  - Token 上限 800 → 1200，适配更丰富的上下文
- **命名对齐**: 注释/文档中的 "昨日训练" → "当日训练/今日训练"，减少混淆
- **CLI 收敛**: `rundown daily` 移除 `--ai`/`--html`/`--image`/`--no-ai` 参数。每次执行自动完成：同步数据 → md → HTML → PNG → AI 洞察。保留 `--date`/`--theme`/`--format`

---

## 2026-06-24

### Added
- 初始化项目结构：CLI 框架、Garmin 数据同步、记忆系统、HTML 日报渲染
- MCP Server 支持，可对接 Claude Desktop / OpenClaw
- 项目规范文件 `CLAUDE.md`，定义文档同步规则、Bug 修复记录规范、Code Conventions

### Changed
- （无）

### Fixed
- （无）
