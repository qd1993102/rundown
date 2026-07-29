# Changelog

neurun 项目变更日志，按日期倒序。

---

## 2026-07-29

### Added
- **联系我们与内测交流群入口**: 登录后的所有 Web 页面新增统一浮动联系按钮；桌面支持 hover/focus 预览与点击锁定，移动端支持点击、长按或保存二维码，包含外部点击、关闭按钮、Escape、焦点返回和图片不可用状态。群二维码端点受应用会话保护，优先读取持久化覆盖文件并禁用缓存，后续换图无需修改页面代码。
- **影响范围**: src/web.py, web/assets/contact-wechat.jpg, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [联系入口产品方案](product/contact-community.md), [联系入口技术设计](design/contact-community.md), [README](../README.md)
- **Web 异步同步任务与实时阶段进度**: `POST /api/sync` 在任务持久化和调度接纳后立即返回 202 与 `task_id`；同步页通过用户隔离的轮询接口展示排队、认证、指标、活动、备份和终态，支持刷新恢复、网络退避、同用户任务找回及服务重启后的 `interrupted`。任务仍复用 4/100 受控并发，不引入 Redis/Celery，CLI/MCP 合同不变。
- **影响范围**: src/sync_tasks.py, src/sync_coordinator.py, src/main.py, src/web.py, src/users.py, web/templates/sync.html, tests/test_sync_tasks.py, tests/test_sync_coordinator.py, tests/test_main.py, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-29-web-async-sync-tasks.md), [README](../README.md)

### Fixed
- **Garmin 批量同步进度长时间停在 2/4**: 接入 garmy 逐日期/逐指标完成、跳过和失败事件，在四阶段总进度下持久化真实的已处理项数、总项数、日期和指标；同步页使用独立细进度条展示并在刷新后恢复，不再因长阶段缺少更新时间而误判假死。
- **影响范围**: src/storage.py, src/main.py, src/sync_tasks.py, src/web.py, web/templates/sync.html, tests/test_storage.py, tests/test_main.py, tests/test_sync_tasks.py, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-sync-progress-stale-after-refresh.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)
- **Garmin Access Token 到期反复要求输入密码**: 同步恢复 Token 后先判断 `needs_refresh` 并自动换取、持久化新 OAuth2 Token，不再把约一天到期的 Access Token 直接回退为空密码 SSO 登录；profile、刷新和 Provider 初始化保留原始临时异常，只有明确 401/403 或不可再刷新的凭据才把连接标记为 `expired`，其他错误保持 `active` 并允许重试。
- **影响范围**: src/auth.py, src/providers/garmin.py, src/main.py, src/web.py, tests/test_auth.py, tests/test_providers.py, tests/test_main.py, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-garmin-token-refresh-rebind-loop.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)
- **ECS Web 同步阻塞与文件描述符耗尽**: 阻塞式平台同步和 SQLite 写入移入受控工作线程，单进程默认同时执行 4 个、接纳 100 个不同用户；同用户 singleflight 与容量超限分别返回 409/503。同步、MFA 和临时 SQLite/HTTP 客户端显式释放资源，systemd 服务增加 `LimitNOFILE=8192` 防线。
- **影响范围**: src/sync_coordinator.py, src/resource_lifecycle.py, src/web.py, src/main.py, src/storage.py, src/auth.py, src/config.py, scripts/deploy-ecs.sh, tests/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-ecs-sync-event-loop-fd-exhaustion.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-29-web-sync-capacity.md), [README](../README.md)
- **Coros 睡眠重新授权被重置为 Garmin**: 重新授权页面的最终 Provider 初始化改为由 `rebind=coros` 决定，不再被脚本末尾的 Garmin 默认值覆盖；页面保持隐藏 Garmin 区域、使用邮箱或手机号标签，并固定提交当前 Coros 平台。
- **影响范围**: web/templates/setup.html, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-coros-rebind-provider-reset.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)
- **ECS 候选发布失败不再影响在线版本**: 阿里云控制台入口先验证 Git 下载结果再执行仓库发布脚本；候选 release 使用独立虚拟环境，依赖或导入失败时不切换当前软链接、不调用 systemd 且不删除旧 release，新版本健康失败时继续回滚旧版本。
- **影响范围**: scripts/deploy-ecs.sh, tests/test_packaging.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-28-ecs-deploy-preserve-current-release.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-29-ecs-safe-release.md), [README](../README.md)
- **Garmin 中国区同步误报认证失败**: Garmin APIClient 现在显式继承绑定时选择的 `garmin.com` 或 `garmin.cn`，profile、活动、健康同步和记忆补全不再把中国区 Token 发往国际区；真正的 Token 失效仍保持 401 与重新绑定流程。
- **影响范围**: src/auth.py, src/providers/garmin.py, src/main.py, src/fetcher.py, tests/test_auth.py, tests/test_providers.py, tests/test_main.py, tests/test_fetcher.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-garmin-cn-api-client-domain.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)

## 2026-07-28

### Added
- **训练体验产品真相源与配套技术设计**: 新增 `docs/product/` 产品文档层，沉淀训练主界面、今日训练、本周安排、执行反馈、AI 调整提案、用户确认和方案版本的完整产品方案；新增配套目标架构、数据模型、API、MCP、迁移、安全与测试设计，并明确这些能力尚未实现。
- **影响范围**: docs/product/, docs/design/, CONTEXT.md, docs/adr/0011-require-confirmation-for-training-plan-adjustments.md, AGENTS.md, CLAUDE.md, README.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [交互式训练方案系统](design/training-system.md), [产品文档索引](product/index.md), [ADR-0011](adr/0011-require-confirmation-for-training-plan-adjustments.md)
- **阿里云 ARMS Web RUM 与账户归因**: 所有 HTML 页面统一接入 Browser SDK v2，采集 PV/UV、性能、Web Vitals、API、静态资源、JS/Console 错误和用户行为；保留 SDK 生成的访客 ID 作为原生 UV 口径，登录后仅以 API Key 的不可逆摘要设置 `user.name`，支持按业务账户归因且不暴露 Cookie、API Key、邮箱或昵称。
- **影响范围**: src/web.py, tests/test_web.py
- **关联文档**: [依赖清单](design/07-dependencies.md), [Web 部署设计](design/13-sae-deployment.md)
- **Web 日报一键保存图片**: 日报详情可将当前结构化数据和主题直接在浏览器内绘制为高清 PNG；手机优先调用系统文件分享以便保存到相册，不支持时下载 `neurun-daily-YYYY-MM-DD.png`，过程不上传训练数据、不依赖 CDN 或服务端截图组件。
- **影响范围**: web/templates/chat.html, tests/test_web.py
- **关联文档**: [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-28-mobile-daily-image-export.md), [README](../README.md)

### Changed
- **Web 页面移动端优先与统一导航**: 同步、日报详情、日报列表和我的页面统一使用移动端底部三项导航，保留图标、文字、当前态和安全区；主题切换从主导航中分离，桌面端通过 `min-width` 恢复顶部横向导航。日报计划、恢复指标、训练卡和评分区同时按手机宽度重排，hover 只对支持设备启用。
- **影响范围**: web/templates/sync.html, web/templates/chat.html, web/templates/reports.html, web/templates/profile.html, tests/test_web.py
- **关联文档**: [模块设计](design/04-modules.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-28-mobile-daily-image-export.md), [README](../README.md)
- **新邀请码缩短为 6 位**: `invite create` 新生成的邀请码改为 6 位无歧义大写字母与数字；存量 JSON 中已发布的旧版长邀请码继续按原值验证和核销，无需迁移。
- **影响范围**: src/invitations.py, src/main.py, src/mcp_server.py, web/templates/auth.html, src/web.py, tests/test_registration.py
- **关联文档**: [领域语言](../CONTEXT.md), [MVP ADR](adr/0010-cost-first-invitation-registration-mvp.md), [模块设计](design/04-modules.md), [README](../README.md)
- **首次设置精简为可跳过个性化**: 注册后只强制绑定运动平台；个人资料与 PB、训练目标与偏好改为默认收起的选填区域，可直接跳过进入首页，并保留“我的”页面后续补填入口。
- **影响范围**: web/templates/setup.html, tests/test_setup_flow.py
- **关联文档**: [MVP ADR](adr/0010-cost-first-invitation-registration-mvp.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)

### Fixed
- **Coros 睡眠无法同步**: Coros 绑定现在同时获取用户隔离的 Training Hub 与 Mobile token，并将 Mobile API 返回的总睡眠、深睡和 REM 映射到每日健康数据；存量 Coros 用户可从“我的”执行同平台重新授权，普通批量同步会合并补齐已有日期，睡眠接口失败不会阻断活动、RHR 或 HRV。
- **影响范围**: src/providers/coros.py, src/main.py, src/web.py, web/templates/setup.html, web/templates/profile.html, tests/test_providers.py, tests/test_storage.py, tests/test_registration.py, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-28-coros-sleep-mobile-auth.md), [多平台设计](design/12-multi-platform.md), [开发过程](process/2026-07-28-coros-sleep-mobile-auth.md), [README](../README.md)
- **ECS Git 下载失败保留在线版本**: 原生部署将平台下载目录与 systemd 运行目录分离；新代码先复制到独立 release，在独立虚拟环境完成安装和导入检查后才原子切换并重启。Git 未拉取或构建失败时不删除、停止或覆盖当前应用，新版本健康检查失败时自动恢复旧 release。
- **影响范围**: scripts/deploy-ecs.sh, tests/test_packaging.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-28-ecs-deploy-preserve-current-release.md), [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)
- **移动端日报评分被挤到第二行**: 日报列表卡片改为“日期 / 可收缩摘要 / 固定评分”单行 Grid，训练摘要超长时使用省略号，睡眠和恢复评分在 320px 起保持稳定宽度且不换行。
- **影响范围**: web/templates/reports.html, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-28-report-scores-mobile-wrap.md), [模块设计](design/04-modules.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-28-mobile-daily-image-export.md), [README](../README.md)
- **Coros 绑定缺少运行模块**: `coros-mcp` 从可选 extras 调整为默认运行依赖，Docker 镜像补充 Git 安装能力；默认安装和部署不再在 Coros 绑定时触发 `No module named 'coros_mcp'`，缺失依赖时也会返回可操作提示。
- **影响范围**: pyproject.toml, Dockerfile, src/providers/coros.py, tests/test_packaging.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-28-coros-runtime-dependency.md), [依赖清单](design/07-dependencies.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)

## 2026-07-27

### Changed
- **今日日期主题色光环**: 同步日历的今日标识由深灰外框调整为当前主题的浅色强调环与柔和光晕，保留不占用格内文字空间和 `aria-current="date"` 的可访问性契约。
- **影响范围**: web/templates/sync.html, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-27-sync-calendar-running-priority.md), [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)

### Fixed
- **同步日历跑步完成判定与今日样式**: 当天存在跑步记录时优先显示为“已同步”，不再被 Garmin 遗留的活动待处理状态或健康指标失败降级为“部分完成”；三平台新同步活动持久化标准化 `activity_type`，旧数据兼容按活动名称识别。今日标识从日期下划线改为不占用格内空间的外轮廓，并补充 `aria-current="date"`。
- **影响范围**: src/main.py, src/storage.py, web/templates/sync.html, tests/test_storage.py, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-27-sync-calendar-running-priority.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-26-sync-calendar.md), [README](../README.md)

## 2026-07-26

### Added
- **Web 逐日同步日历**: 同步页新增按月切换的七列日历，展示已同步、部分完成、失败、同步中、未同步和未来日期；点击历史日期可回填单日同步，单日或批量同步完成后自动刷新对应月份。核心同步流程在现有 `sync_status` 中按日记录 Provider 范围状态，空数据休息日也能准确显示为已覆盖；新增只读 `GET /api/sync/calendar?month=YYYY-MM` 聚合旧指标状态、本地活动及健康数据。
- **影响范围**: src/storage.py, src/main.py, src/web.py, web/templates/sync.html, tests/test_storage.py, tests/test_main.py, tests/test_web.py
- **关联文档**: [项目目标](design/01-project-goals.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-26-sync-calendar.md), [README](../README.md)

### Fixed
- **三平台同步认证顺序与 Huawei 用户凭据**: Garmin、Coros、Huawei 统一先恢复并验证凭据，再读取正整数平台用户 ID 和打开 SQLite；认证失效统一返回 401、标记连接 `expired`，不会继续本地写入。Huawei Web 绑定仅在认证成功后把 CrewPals token 保存到当前用户目录。
- **影响范围**: src/main.py, src/web.py, src/config.py, tests/test_main.py, tests/test_web.py, tests/test_config.py, tests/test_registration.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-26-provider-auth-before-user-id.md), [多平台设计](design/12-multi-platform.md), [数据流](design/05-data-flow.md), [开发过程](process/2026-07-26-auth-and-private-persistence-audit.md), [README](../README.md)
- **本地敏感数据权限与原子写入**: Web 数据目录统一为 `0700`，账号、邀请码、平台凭据、SQLite、备份、记忆、配置和导出文件统一为 `0600`；文本改用同目录原子替换，目录不可写时返回实际路径及 `chown` 建议，ECS 文档明确管理命令使用 systemd 服务用户执行。
- **影响范围**: src/local_files.py, src/users.py, src/invitations.py, src/storage.py, src/memory.py, src/providers/coros.py, src/providers/huawei.py, src/main.py, src/web.py, src/mcp_server.py, src/coach.py, src/render.py, src/image.py, src/exporter.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-26-private-local-persistence.md), [模块设计](design/04-modules.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-26-auth-and-private-persistence-audit.md), [README](../README.md)
- **CLB 专用存活检查端点**: 新增无鉴权的 `GET|HEAD /healthz`，固定返回 HTTP 200，且不读取 Cookie、用户数据或外部平台，避免负载均衡健康检查耦合登录业务；部署文档同时明确 ECS 后端必须以 `MCP_HOST=0.0.0.0` 监听私网网卡。
- **影响范围**: src/web.py, tests/test_web.py, README.md, docs/design/01-project-goals.md, docs/design/13-sae-deployment.md
- **关联文档**: [Bug 记录](bugfixes/2026-07-26-clb-health-check.md), [项目目标](design/01-project-goals.md), [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)

## 2026-07-22

### Changed
- **Web 同步与日报生成解耦**: `POST /api/sync` 现在只拉取并持久化单日或批量数据，不再自动创建或覆盖日报；日报页通过独立的 `POST /api/reports` 由用户选择日期显式生成。显式生成只读取用户 SQLite，并在 `prompts/coach.md` 产出的在线 AI 洞察成功后重新渲染正文，确保 Front Matter、Markdown 与 Web 展示一致；在线 AI 不可用时保留本地规则兜底。
- **影响范围**: src/main.py, src/web.py, src/storage.py, src/memory.py, web/templates/sync.html, web/templates/reports.html, web/templates/chat.html, tests/test_main.py, tests/test_web.py, tests/test_storage.py
- **关联文档**: [模块设计](design/04-modules.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-22-web-sync-report-separation.md), [README](../README.md)

## 2026-07-21

### Added
- **Web 邀请码注册与应用账号登录**: 首次访问改为邀请码两步注册，基本信息包含昵称、邮箱和密码；每份 Invitation 由系统随机生成，且定义为不预绑定邮箱、由首个成功使用者获得、仅能创建一个账号、使用或停用前不过期的一次性注册资格；JSON 保留完整邀请码并按敏感凭证保护；新增邀请码核销、邮箱唯一性、`scrypt` 密码哈希、登录页与 30 天 Cookie。数据源绑定接口现在要求已登录应用账号，不再隐式创建用户。
- **影响范围**: src/invitations.py, src/users.py, src/config.py, src/web.py, src/main.py, src/mcp_server.py, web/templates/auth.html, web/templates/profile.html, docker-compose.yml, tests/test_registration.py
- **关联文档**: [领域语言](../CONTEXT.md), [MVP ADR](adr/0010-cost-first-invitation-registration-mvp.md), [平台连接 ADR](adr/0001-platform-connection-cardinality.md), [邮箱 ADR](adr/0003-login-email-is-not-verified.md), [管理员工具 ADR](adr/0006-gate-invitation-administration-tools.md), [旧数据 ADR](adr/0009-do-not-migrate-legacy-accounts-or-cookies.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-21-invite-registration.md), [README](../README.md)

## 2026-07-19

### Added
- **Web 单日/批量同步拆分**: `/sync` 页面拆为单日同步和批量日期范围同步两个入口；`POST /api/sync` 新增 `mode=single|batch`、`from_date`、`to_date`，返回标准化同步范围并保留旧请求格式兼容。
- **影响范围**: web/templates/sync.html, src/web.py, tests/test_web.py
- **关联文档**: [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)

### Fixed
- **Coros 活动时长包含暂停时间**: 活动列表改为读取原始 `workoutTime`，统一时长排除暂停并在缺失时回退 `totalTime`；普通重同步会更新已有活动时长，Coros API/token 错误不再被吞掉后误报成功，失效 token 会转为 `expired` 并提供重新绑定入口。
- **影响范围**: providers/coros.py, main.py, web.py, web/templates/sync.html, tests/test_providers.py, tests/test_storage.py, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-19-coros-active-duration.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)
- **批量同步后报告列表只有一天**: 批量模式在一次范围数据拉取后为首尾区间逐日补建日报，响应新增 `reports_generated`；结束日期保留在线 AI 洞察，历史日期使用本地规则，报告列表现在可展示完整批量范围。
- **影响范围**: src/web.py, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-19-batch-sync-report-list.md), [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)
- **Coros Web 同步误入 Garmin 链路**: Web 同步现在将用户注册表中的 provider 注入用户配置，并按用户目录恢复 Coros token；单一 active Coros 用户可自动迁移旧版全局 token，多用户时拒绝猜测归属；同时兼容缺少 `warning()` 的 garmy `ProgressReporter`，避免底层活动拉取异常被 `AttributeError` 覆盖。
- **影响范围**: config.py, web.py, providers/coros.py, main.py, storage.py, tests/test_config.py, tests/test_providers.py, tests/test_storage.py, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-19-coros-sync-progress-reporter.md), [多平台设计](design/12-multi-platform.md), [Web 部署设计](design/13-sae-deployment.md)

## 2026-07-18

### Changed
- **Web 首页改为日报仪表盘**: `chat.html` 从聊天对话界面改为日报仪表盘，展示训练概览、睡眠评分、身体状态（RHR/HRV/电量/训练准备）、训练负荷/恢复和 AI 教练洞察卡片，不再有消息输入框
- **Web 初始化改为多步向导**: `setup.html` 从单一 Garmin 绑定页改为 3 步向导（数据源选择与绑定 → 个人资料与最佳成绩 → 训练目标与偏好），匹配 CLI `init` + `setup` 流程
- **Web API 扩展**: 新增 `GET /api/dashboard`（仪表盘 JSON）、`POST /api/profile`（个人资料）、`POST /api/goals`（训练目标）、`POST /api/preferences`（训练偏好）；`POST /api/setup` 扩展支持 Coros 和 Huawei 数据源绑定
- **影响范围**: web/templates/chat.html, web/templates/setup.html, src/web.py
- **关联文档**: [docs/design/13-sae-deployment.md](design/13-sae-deployment.md)

## 2026-07-15

### Added
- **Huawei CrewPals 认证**: 新增 Huawei Provider，通过每用户 `GROUP_PALS_TOKEN` 从 CrewPals 预发布 HTTPS 接口获取并缓存 Huawei AT；Authorization 使用原始 JWT，不添加 Bearer 前缀；新增 `neurun auth` 与 MCP 认证入口。
- **Huawei 配置**: 新增 `GROUP_PALS_TOKEN` 和 `HUAWEI_TOKEN_DIR`，Huawei 模式不要求账号密码或开发者 OAuth 凭证；默认以 token 摘要派生隔离目录。
- **CrewPals 响应兼容**: 支持预发布接口的 `data` wrapper 与 `accessToken/refreshToken/expiredAt/openId` camelCase 字段，并统一转换为本地 snake_case token。
- **影响范围**: config.py, main.py, mcp_server.py, providers/huawei.py, providers/__init__.py
- **关联文档**: [docs/design/12-multi-platform.md](design/12-multi-platform.md), [实现过程](process/2026-07-15-huawei-crewpals-auth.md)

### Changed
- **三平台数据标准对齐**: 按 `ActivityData` / `DailyHealth` 逐字段列出 Garmin、Coros、Huawei 的实际接入差异，并明确单位、缺失值、近似语义、平台分数和时区处理规范。
- **影响范围**: docs/design/12-multi-platform.md
- **关联文档**: [docs/design/12-multi-platform.md](design/12-multi-platform.md)
- **Huawei Token store 初始化**: `neurun init` 为当前配置选择并创建 `0700` token 目录；`neurun auth` 自动检测目录和 token JSON、校验 `access_token` 并收紧文件权限为 `0600`，不创建无效空文件。
- **影响范围**: main.py, providers/huawei.py, tests/test_providers.py
- **关联文档**: [docs/design/12-multi-platform.md](design/12-multi-platform.md), [实现过程](process/2026-07-15-huawei-crewpals-auth.md)

### Fixed
- **外部 Huawei Token 结构不兼容**: 兼容 `expired_at`、`open_id` 和 `user_id` 字段，保留已有绝对过期时间，避免有效 AT 被误判过期并错误进入刷新/授权流程。
- **影响范围**: providers/huawei.py, tests/test_providers.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-15-huawei-external-token-fields.md), [docs/design/12-multi-platform.md](design/12-multi-platform.md)
- **Huawei `neurun sync` 提前退出**: 移除 Huawei 同步硬编码返回，接入 `/activityRecords` 运动列表和 `activityRecordId` 详情查询，并通过统一 Provider 入库路径写入用户 SQLite。
- **影响范围**: main.py, providers/huawei.py, tests/test_providers.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-15-huawei-sync-disabled.md), [docs/design/12-multi-platform.md](design/12-multi-platform.md)
- **`NEURUN_HOME` 继承全局用户凭证**: 显式用户目录不再加载 `~/.neurun/.env`，避免 Huawei 用户配置被另一个全局 Garmin 账号污染。
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
- **`neurun mcp` 支持多传输模式**: 新增 `--transport` / `--host` / `--port` 参数，支持 sse/http/streamable-http
- **`Config` 增强**: 新增 `non_interactive`、`data_dir` 字段；`token_dir` 纳入 `NEURUN_HOME` 解析；Web 服务模式跳过全局 Garmin 凭证校验；新增 `UserConfig` 和 `for_user()` 工厂方法
- **`GarminAuth` 增强**: 支持 `non_interactive` 参数，SAE/Web 环境下不阻塞等待 stdin 输入

---

## 2026-07-09 (early)

### Changed
- **`daily` 与 `sync` 命令合并重构**: `daily` 成为一站式命令，自动检查并同步数据后生成报告，无需先跑 `sync` 再跑 `daily`
  - `cmd_daily` 增强：自动检查本地数据完整性（支持 Garmin + Coros），缺失时自动拉取
  - `cmd_daily` 新增 `--sync-days`、`--skip-sync`、`--full`、`--force` 参数，灵活控制同步行为
  - `cmd_sync` 简化为纯数据同步工具，移除记忆生成功能和 `--no-memory` 参数
  - `cmd_sync` 完成后提示用户运行 `neurun daily` 生成报告
  - 移除 `_generate_memories()` 函数（功能已整合到 `daily`）
- **影响范围**: main.py, mcp_server.py, README.md
- **关联文档**: [docs/design/04-modules.md](docs/design/04-modules.md)

---

## 2026-06-28

### Changed
- **多目录数据隔离**: 新增 `NEURUN_MEMORY_DIR` 和 `NEURUN_HOME` 环境变量支持
  - `NEURUN_MEMORY_DIR` — 覆盖记忆存储目录（目标、档案、日报等），支持多数据目录共享同一份记忆，或各自独立记忆
  - `NEURUN_HOME` — 设后所有相对路径（db_path、memory_dir）及 .env 加载均基于此目录解析，实现一键切换数据工作目录
  - `get_config()` 增强：若设 `NEURUN_HOME`，.env 从该目录加载，相对路径自动解析为绝对路径
  - `fastmcp.json` 新增 `NEURUN_MEMORY_DIR` 环境变量注入，确保 MCP Server 使用正确的记忆目录

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
- **CLI 收敛**: `neurun daily` 移除 `--ai`/`--html`/`--image`/`--no-ai` 参数。每次执行自动完成：同步数据 → md → HTML → PNG → AI 洞察。保留 `--date`/`--theme`/`--format`

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
