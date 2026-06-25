# Changelog

## [0.1.0] — 2026-06-25

### Added
- **CLI**: `rundown init` — 引导式创建 `.env` 配置文件，支持自动同步
- **Core**: Garmin 数据同步、日报生成、记忆存储系统
- **AI**: DeepSeek 教练洞察 (`--ai`)、规则引擎 fallback
- **HTML**: 三主题静态日报（清新/运动/暗黑）、本地存储记住偏好
- **Image**: Playwright 跨平台 PNG 截图、Chrome fallback
- **CLI**: `sync` / `daily` / `activities` / `health` / `memory` / `setup` / `status` / `mcp`
- **MCP Server**: 6 Resources + 9 Tools，支持 OpenClaw 集成
- **Provider**: 数据源抽象层，支持 Garmin / Coros
- **Daily Report**: 今日训练数据 + 昨夜恢复数据、分段配速/步频/功率分析
- **Tests**: 46 tests covering config, memory, providers, render

### Changed
- 配置加载：支持 `~/.rundown/.env` 全局配置，优先级：系统 env > 项目 .env > ~/.rundown/.env
- `RUNDOWN_EMAIL` → `RUNDOWN_ACCOUNT`（兼容邮箱和手机号）
- 默认数据库路径改为 `~/.rundown/data.db`
- 日报逻辑：训练数据从"昨日"改为"今日"
- 环境变量：`RUNDOWN_EMAIL/PASSWORD` 替代 `GARMIN_EMAIL/PASSWORD`（兼容旧名）
- 图片生成：从 Chrome 改为 Playwright（跨平台）
- HTML 样式：从暗黑程序员风 → 三主题现代运动风

### Fixed
- Coros 手机号登录（自动检测手机号，使用 mobile + accountType=1）
- garmy 2.0 API 适配（AuthClient/SyncManager/HealthDB 接口变更）
- SyncManager 数据库路径 Bug（数据写到了错误的 DB 文件）
- 活动距离缺失（通过 raw API 补全 `distance_meters`）
- HTML 表格列头和列值偏移
