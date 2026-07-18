# Changelog

Rundown 项目变更日志，按日期倒序。

---

## 2026-07-18

### Changed
- **Web 首页改为日报仪表盘**: `chat.html` 从聊天对话界面改为日报仪表盘，展示训练概览、睡眠评分、身体状态（RHR/HRV/电量/训练准备）、训练负荷/恢复和 AI 教练洞察卡片，不再有消息输入框
- **Web 初始化改为多步向导**: `setup.html` 从单一 Garmin 绑定页改为 3 步向导（数据源选择与绑定 → 个人资料与最佳成绩 → 训练目标与偏好），匹配 CLI `init` + `setup` 流程
- **Web API 扩展**: 新增 `GET /api/dashboard`（仪表盘 JSON）、`POST /api/profile`（个人资料）、`POST /api/goals`（训练目标）、`POST /api/preferences`（训练偏好）；`POST /api/setup` 扩展支持 Coros 和 Huawei 数据源绑定
- **影响范围**: web/templates/chat.html, web/templates/setup.html, src/web.py
- **关联文档**: [docs/design/13-sae-deployment.md](design/13-sae-deployment.md)

## 2026-07-15

### Added
- **Huawei CrewPals 认证**: 新增 Huawei Provider，通过每用户 `GROUP_PALS_TOKEN` 从 CrewPals 预发布 HTTPS 接口获取并缓存 Huawei AT；Authorization 使用原始 JWT，不添加 Bearer 前缀；新增 `rundown auth` 与 MCP 认证入口。
- **Huawei 配置**: 新增 `GROUP_PALS_TOKEN` 和 `HUAWEI_TOKEN_DIR`，Huawei 模式不要求账号密码或开发者 OAuth 凭证；默认以 token 摘要派生隔离目录。
- **CrewPals 响应兼容**: 支持预发布接口的 `data` wrapper 与 `accessToken/refreshToken/expiredAt/openId` camelCase 字段，并统一转换为本地 snake_case token。
- **影响范围**: config.py, main.py, mcp_server.py, providers/huawei.py, providers/__init__.py
- **关联文档**: [docs/design/12-multi-platform.md](design/12-multi-platform.md), [实现过程](process/2026-07-15-huawei-crewpals-auth.md)

### Changed
- **三平台数据标准对齐**: 按 `ActivityData` / `DailyHealth` 逐字段列出 Garmin、Coros、Huawei 的实际接入差异，并明确单位、缺失值、近似语义、平台分数和时区处理规范。
- **影响范围**: docs/design/12-multi-platform.md
- **关联文档**: [docs/design/12-multi-platform.md](design/12-multi-platform.md)
- **Huawei Token store 初始化**: `rundown init` 为当前配置选择并创建 `0700` token 目录；`rundown auth` 自动检测目录和 token JSON、校验 `access_token` 并收紧文件权限为 `0600`，不创建无效空文件。
- **影响范围**: main.py, providers/huawei.py, tests/test_providers.py
- **关联文档**: [docs/design/12-multi-platform.md](design/12-multi-platform.md), [实现过程](process/2026-07-15-huawei-crewpals-auth.md)

### Fixed
- **外部 Huawei Token 结构不兼容**: 兼容 `expired_at`、`open_id` 和 `user_id` 字段，保留已有绝对过期时间，避免有效 AT 被误判过期并错误进入刷新/授权流程。
- **影响范围**: providers/huawei.py, tests/test_providers.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-15-huawei-external-token-fields.md), [docs/design/12-multi-platform.md](design/12-multi-platform.md)
- **Huawei `rundown sync` 提前退出**: 移除 Huawei 同步硬编码返回，接入 `/activityRecords` 运动列表和 `activityRecordId` 详情查询，并通过统一 Provider 入库路径写入用户 SQLite。
- **影响范围**: main.py, providers/huawei.py, tests/test_providers.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-15-huawei-sync-disabled.md), [docs/design/12-multi-platform.md](design/12-multi-platform.md)
- **`RUNDOWN_HOME` 继承全局用户凭证**: 显式用户目录不再加载 `~/.rundown/.env`，避免 Huawei 用户配置被另一个全局 Garmin 账号污染。
- **影响范围**: config.py, tests/test_config.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-15-rundown-home-global-credentials.md), [docs/design/12-multi-platform.md](design/12-multi-platform.md)

## 2026-07-09

### Added
- **Web Chat 多用户部署**: 新增 Web Chat 模式，用户打开浏览器即可绑定 Garmin 并与 AI 教练对话
  - `src/users.py` — 用户管理器（API Key 生成、注册表 CRUD、多用户路径隔离）
  - `src/web.py` — Web 路由层（/api/setup /api/mfa /api/chat/stream /api/sync），通过 FastMCP custom_route 注册，不引入额外 Web 框架
  - `src/coach.py` — 新增 `chat_stream()` 流式 AI 对话（SSE 逐块推送）
  - `src/main.py` — 新增 `cmd_serve` Web 服务入口，`cmd_mcp` 支持 SSE/HTTP 传输
  - `Dockerfile` + `docker-compose.yml` — 一键容器化部署
- **多用户数据隔离**: 按 API Key 隔离 Token、记忆文件、SQLite 数据库，互不干扰
- **非交互式 MFA 支持**: `AuthManager` 新增 `start_login()` / `complete_mfa()` 两步拆分，Web 模式通过页面输入 MFA 验证码
- **SQLite 备份/恢复**: `Storage` 新增 `backup_to()` / `restore_from()` 方法，容器重启时自动恢复数据
- **影响范围**: config.py, auth.py, storage.py, providers/garmin.py, coach.py, main.py, pyproject.toml
- **关联文档**: [docs/design/13-sae-deployment.md](docs/design/13-sae-deployment.md)

### Changed
- **`rundown mcp` 支持多传输模式**: 新增 `--transport` / `--host` / `--port` 参数，支持 sse/http/streamable-http
- **`Config` 增强**: 新增 `non_interactive`、`data_dir` 字段；`token_dir` 纳入 `RUNDOWN_HOME` 解析；Web 服务模式跳过全局 Garmin 凭证校验；新增 `UserConfig` 和 `for_user()` 工厂方法
- **`GarminAuth` 增强**: 支持 `non_interactive` 参数，SAE/Web 环境下不阻塞等待 stdin 输入

---

## 2026-07-09 (early)

### Changed
- **`daily` 与 `sync` 命令合并重构**: `daily` 成为一站式命令，自动检查并同步数据后生成报告，无需先跑 `sync` 再跑 `daily`
  - `cmd_daily` 增强：自动检查本地数据完整性（支持 Garmin + Coros），缺失时自动拉取
  - `cmd_daily` 新增 `--sync-days`、`--skip-sync`、`--full`、`--force` 参数，灵活控制同步行为
  - `cmd_sync` 简化为纯数据同步工具，移除记忆生成功能和 `--no-memory` 参数
  - `cmd_sync` 完成后提示用户运行 `rundown daily` 生成报告
  - 移除 `_generate_memories()` 函数（功能已整合到 `daily`）
- **影响范围**: main.py, mcp_server.py, README.md
- **关联文档**: [docs/design/04-modules.md](docs/design/04-modules.md)

---

## 2026-06-28

### Changed
- **多目录数据隔离**: 新增 `RUNDOWN_MEMORY_DIR` 和 `RUNDOWN_HOME` 环境变量支持
  - `RUNDOWN_MEMORY_DIR` — 覆盖记忆存储目录（目标、档案、日报等），支持多数据目录共享同一份记忆，或各自独立记忆
  - `RUNDOWN_HOME` — 设后所有相对路径（db_path、memory_dir）及 .env 加载均基于此目录解析，实现一键切换数据工作目录
  - `get_config()` 增强：若设 `RUNDOWN_HOME`，.env 从该目录加载，相对路径自动解析为绝对路径
  - `fastmcp.json` 新增 `RUNDOWN_MEMORY_DIR` 环境变量注入，确保 MCP Server 使用正确的记忆目录

### Fixed
- **多目录下 AI 分析目标/档案缺失**: 从非项目根目录执行命令时，`memory_dir` 依赖 cwd，导致找不到目标、偏好等 AI 上下文文件。现已支持独立配置记忆路径。
- **CorosHealth 重复方法**: 移除重复的 `__init__` 和 `fetch_health_range` 方法定义（Python 仅最后一个生效，前一个为死代码）

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
