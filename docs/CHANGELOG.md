# Changelog

## 2026-08-17

### Changed

- **改动描述**: Huawei 健康数据新用户注册入口暂时下线：Web 绑定 API（`POST /api/setup` provider=huawei）返回 503 并提示"恢复时间另行通知"；CLI `init` 命令不再列出 Huawei 选项；Web 绑定页面隐藏 Huawei 选择器。现有已绑定华为账号的同步、日历、报告等能力不受影响。
- **影响范围**: `src/web.py`、`src/main.py`、`web/templates/setup.html`
- **关联文档**: [产品方案 — 数据源绑定与同步](product/data-source-sync.md)

## 2026-08-16

### Changed

- **改动描述**: `get_config()` 新增三条 INFO 诊断日志：`配置探测[原始环境]`（进程实际收到的 `NEURUN_DATA_DIR` / `NEURUN_INVITE_CODES_FILE` 等，在加载 .env 前捕获）、`配置探测[部署环境文件]`（`/etc/neurun/neurun.env` 是否存在）、`配置探测[解析结果]`（叠加 .env / 部署环境文件后的最终 `invite_codes_path`），用于 ECS 上排查邀请码写入路径与预期不符（命令折行导致 env 丢失、cwd `.env` 覆盖、部署环境文件缺失）。
- **影响范围**: `src/config.py`、`tests/test_config.py`
- **关联文档**: [Bug 记录：ECS 发布后邀请码读写路径漂移](bugfixes/2026-08-16-invite-code-path-drift.md)

- **改动描述**: CLI 管理员命令与 Web 共享同一配置源：`get_config()` 自动读取部署环境文件 `/etc/neurun/neurun.env`（只补缺失项，系统环境变量优先），ECS/systemd 部署下 `neurun invite create` 等命令无需手工传 `NEURUN_DATA_DIR` / `NEURUN_INVITE_CODES_FILE` 即与 Web 读写同一邀请码文件；`scripts/deploy-ecs.sh` 发布时把这两个变量幂等写入部署环境文件（已存在则跳过）。
- **影响范围**: `src/config.py`、`scripts/deploy-ecs.sh`、`tests/test_config.py`
- **关联文档**: [Bug 记录：ECS 发布后邀请码读写路径漂移](bugfixes/2026-08-16-invite-code-path-drift.md)

### Fixed

- **改动描述**: 修复 ECS 部署后服务启动崩溃（`PermissionError: /etc/neurun/neurun.env`）：部署脚本创建的 `/etc/neurun`（0750 root:root）与 `neurun.env`（0640 root:root）对 `neurun` 进程不可读，而 Python 3.12 的 `Path.exists()` 对 EACCES 直接传播（不返回 False），Web/CLI 首次启动即崩溃（发布后自动回滚）。修复分两处：① `get_config()` 对部署环境文件的存在/可读检查与读取全部 try/except 容错，不可用时 warning 并跳过（Web 仍由 systemd `EnvironmentFile` 注入）；② `scripts/deploy-ecs.sh` 将目录与文件属组改为 `root:运行组`（0750/0640），保证 neurun 进程可读，并对历史遗留 root:root 属主幂等修正。
- **影响范围**: `src/config.py`、`scripts/deploy-ecs.sh`、`tests/test_config.py`
- **关联文档**: [Bug 记录：部署环境文件权限导致服务启动崩溃](bugfixes/2026-08-16-deploy-env-permission-crash.md)

- **改动描述**: 修复 ECS release 切换后邀请码生成与读取可能落在不同文件的问题：`config.invite_codes_path` 兜底逻辑改为相对 `data_dir` 固定基于项目根目录（`src/` 上级）解析，不再随进程 cwd（release 目录）漂移；`invite create --output json` 在 stderr 打印实际写入文件路径（stdout JSON 结构不变，AI/MCP 兼容）。ECS/systemd 部署仍以 `NEURUN_INVITE_CODES_FILE=/var/lib/neurun/invite-codes.json` 绝对路径固定读写位置。
- **影响范围**: `src/config.py`、`src/main.py`、`tests/test_config.py`、`tests/test_main.py`
- **关联文档**: [Bug 记录：ECS 发布后邀请码读写路径漂移](bugfixes/2026-08-16-invite-code-path-drift.md)

## 2026-08-15

### Added

- **改动描述**: 补全两个能力入口：① 重规划支持赛事延期——表单选“赛事延期”显示新赛事日期输入（默认当前目标日期、须晚于今天），候选按新日期重排周期，确认提案后同步目标记录（避免下次重规划回旧日期）；② 作废方案——新增 `POST /api/training/scheme/close` 与长期方案卡“作废方案”按钮（确认后归档当前方案，回到制定方案引导）。
- **影响范围**: `src/training.py`、`src/web.py`、`web/templates/training.html`、`tests/test_training.py`、`tests/test_web.py`
- **关联文档**: [训练体验产品方案 §8.6](product/training-experience.md)、[训练系统设计](design/training-system.md)

- **改动描述**: 同一调整范围只保留一个待确认提案：连续重规划/同日局部调整生成新提案时，旧 pending 原子标记 superseded（today 级按 target_date 区分、scheme 级全局互斥）；作废方案当天即结束（effective_to=昨天，resolve 不再选中）并清理该方案未决提案与历史 active 版本。存量堆积的 4 个待确认提案与已作废方案已一并修复（备份 .bak-20260815）。
- **影响范围**: `src/training.py`、`tests/test_training.py`
- **关联文档**: [训练体验产品方案 §8.6](product/training-experience.md)、[训练系统设计](design/training-system.md)

### Changed

- **改动描述**: 训练结构识别按距离口径输出：`composition` 增加 `basis` 字段（`quantity_reliable` 且分段携带距离时按各角色距离占比，否则回退段数占比），消除“快段占比”被误读为距离占比的语义歧义；`work_recovery_groups` 每组新增 `work_distance_m` / `work_duration_s` / `recovery_distance_m` / `recovery_duration_s`，日报与详情“每组配速”在距离可信时展示每组距离（如 `快 3'04"/km · 1.0km(hr148)`）。
- **影响范围**: `src/summary_extraction.py`、`src/memory.py`、`src/render.py`、`tests/test_summary_extraction.py`、`tests/test_render.py`
- **关联文档**: [摘要提取设计 §10.5](design/summary-extraction.md)

- **改动描述**: 安全规范化信息分级展示：`adjustments` 按“安排变化 / 技术性对齐”分类——课程被改、距离/周数被调整等安排变化直接展示；逐周“跑量对齐/负荷结算”等内部技术条目计数合并（不再逐条平铺），全为技术性对齐时收敛为一句“方案已通过确定性安全校验”；草稿预览、长期方案卡、重规划候选四处展示统一走 `adjustmentKind` / `adjustmentsMarkup` / `adjustmentsInlineMarkup`。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统设计](design/training-system.md)

- **改动描述**: 定时跑距离换算从“安全调整”列表移出（换算属课程属性补全而非安全规范化，以 `distance_estimated` 标记承载，不再进入 `adjustments`），避免脱离课程上下文的“为什么要跑 40 分钟/什么是我的配速”困惑；日历课表格子改为时长优先（定时跑显示“40分钟”而非换算距离），legacy 课程格子显示“40 分钟 · 约 7.2 km”。
- **影响范围**: `src/training_planning.py`、`web/templates/training.html`、`tests/test_training_planning.py`、`tests/test_web.py`
- **关联文档**: [训练系统设计 §10](design/training-system.md)

### Fixed

- **改动描述**: 修复草稿生成“goal_demand_summary 必须是对象”失败：`validate_training_framework` 对可选映射字段（`ability_summary` / `goal_demand_summary` / `weekly_principles`）由严格 `_mapping` 改为宽容降级（非对象 → 空对象，与已有降级模式一致），模型偶发类型瑕疵不再让整份草稿失败；核心结构仍严格校验。
- **影响范围**: `src/coach_runtime/schemas.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录：草稿可选映射字段过度严格](bugfixes/2026-08-15-framework-optional-mapping-strict.md)

- **改动描述**: 修复方案重规划“长距离最长仅 20km”结论与实时能力事实不符：`load_setup` 的长距离能力指标改用近 28 天窗口（周量仍取上一完整自然周）；重规划注入当前方案时只保留结构字段、剔除旧 data_basis/feasibility 等结论（AI 不再复述旧结论，载荷从 180KB 大幅减小）。真实重规划结论由“长距离仅 20km、远低于专项需求”变为“长距离 30km、具备较高有氧基础”。
- **影响范围**: `src/training_service_factory.py`、`src/training.py`、`tests/test_training_service_factory.py`、`tests/test_training.py`
- **关联文档**: [Bug 记录：重规划旧长距离结论](bugfixes/2026-08-15-revision-stale-long-run-conclusion.md)

- **改动描述**: 修复方案重规划 503：`revise-training-scheme` 输出完整候选（阶段 + 首四周 + 逐段处方）约 7.4KB/2000+ tokens，但 `max_tokens` 仅 1800 被截断导致 JSON 解析失败；已纳入 4000 放宽名单，与框架/课表两阶段一致。
- **影响范围**: `src/coach_runtime/runner.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录：重规划 503 输出截断](bugfixes/2026-08-15-scheme-revision-503-max-tokens.md)

- **改动描述**: 修复报告中心生成日报后按钮仍显示“生成日报”的问题：`genReport` 成功路径存在异步竞态（未 `await` 的 `load()` 与末尾 `renderDailyStatus()` 触发的 `checkReadiness()` 竞争，后者晚到把“查看日报”覆盖回“生成日报”）；改为成功路径 `await load()` 后由列表刷新渲染状态，失败路径保留刷新。
- **影响范围**: `web/templates/reports.html`、`tests/test_web.py`
- **关联文档**: [Bug 记录：生成日报后按钮状态未切换](bugfixes/2026-08-15-report-gen-btn-stale-state.md)

- **改动描述**: 修复训练页“训练前说明”等异步按钮在 `await` 后访问 `event.currentTarget` 被重置为 `null` 导致的 `Cannot set properties of null (setting 'disabled')` 报错：6 个异步回调（训练前说明、比赛策略、方案修订/反馈提交、提案确认/拒绝）改为同步阶段捕获按钮引用。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`
- **关联文档**: [Bug 记录：异步事件 currentTarget 为空](bugfixes/2026-08-15-async-event-currenttarget-null.md)

## 2026-08-14

### Changed

- **改动描述**: 训练页信息结构从“今日怎么跑 → 接下来三天 → 本周节奏 → 计划与数据”调整为“今日怎么跑 → 本周怎么跑/进度如何 → 后面几周安排如何”，与草稿预览的渐进披露对齐：本周区域合并了近期安排与进度概览，后面几周采用阶段→周→课表三层下钻的渐进展示。
- **影响范围**: 训练主界面信息架构、首屏渲染、本周安排展示、方案详情展示
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: 训练页“本周怎么跑 / 进度如何”卡并列展示两套口径：计划执行（完成 X 次 / 已跑 X km，仅 `completed` 计划课）与实际运动事实（实际 X 次 / 跑步 X km，周内全部已同步活动，含计划外/部分完成，accent 色标识），口径与报告页周进度 `actual_summary` 一致；`week.progress` 新增 `actual_sessions` / `actual_running_km`，两套数字差异一目了然。
- **影响范围**: `src/training.py`、`web/templates/training.html`、`tests/test_training.py`、`tests/test_web.py`
- **关联文档**: [训练系统技术设计 §7.2](design/training-system.md)、[训练体验产品方案](product/training-experience.md)

- **改动描述**: 本周尚无完全完成的计划课时，训练页收敛“完成 0 次 / 已跑 0 km”两个计划执行数字（仅展示目标与实际事实），避免用户误以为“没练”。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计 §7.2](design/training-system.md)


### Fixed

- **改动描述**: 修复训练页“查看本周进度”按钮字体偏大且在手机窄屏与“本周节奏”日期标题互相挤压的问题：通用 `.btn` 补 13px 字号与 `white-space:nowrap`（与报告页按钮一致），窄屏（≤600px）下卡片头部与训练详情头部自动换行，长标题与操作按钮不再并排挤压。
- **影响范围**: `web/templates/training.html`、训练页移动端布局
- **关联文档**: [Bug 记录：训练页按钮挤压](bugfixes/2026-08-14-training-week-progress-btn-overflow.md)、[训练体验产品方案](product/training-experience.md)

- **改动描述**: 修复训练页点击每日课程时 `blockLabel` 未定义导致详情无**法**渲染的问题；补充 `blockLabel` 和 `stimulusLabel` 两个缺失的查找表常量定义。
- **影响范围**: `web/templates/training.html`
- **关联文档**: [Bug 记录](bugfixes/2026-08-14-training-blockLabel-undefined.md)


### Fixed

- **改动描述**: 修复急性/慢性负荷展示的小数点溢出：`_calc_training_load` 的急性负荷不再原样写入浮点累加结果（历史数据曾出现 `acute_load_7d: 1000.6190490722656` 类二进制精度噪声），统一 round 1 位小数入库；日报 HTML 急性/慢性统一 `:.0f` 取整展示，Web 仪表盘显示前 `Math.round`，新旧数据都不再出现长小数，两个展示点与观察块规则一致。
- **影响范围**: `src/memory.py`、`src/render.py`、`web/templates/dashboard.html`、`tests/test_memory.py`、`tests/test_render.py`
- **关联文档**: [Bug 记录：负荷小数点溢出](bugfixes/2026-08-14-load-decimal-overflow.md)、[记忆系统设计](design/memory-system.md)、[日报产品方案](product/daily-report.md)
- **改动描述**: 修复报告中心无任务状态和收起周复盘详情仍占据空白布局的问题；隐藏元素和关闭详情正文现在完全退出布局。
- **影响范围**: `web/templates/reports.html`、报告中心日报与周复盘页面
- **关联文档**: [Bug 记录：报告页空白布局块](bugfixes/2026-08-14-report-empty-layout-blocks.md)、[日报产品方案](product/daily-report.md)

- **改动描述**: 周复盘在周级解释前自动准备统一逐日事实与确定性日分析并封口版本清单，按统一证据门槛提取和总结质量课；不要求手工日报、不创建七份日报归档，整个周任务在线 AI 仍最多一次。
- **影响范围**: `src/training_day_summary.py`、`src/training.py`、`src/memory.py`、`web/templates/reports.html` 及对应测试
- **关联文档**: [Bug 记录：周复盘前置与质量课](bugfixes/2026-08-14-weekly-prerequisites-quality-sessions.md)、[过程记录](process/2026-08-14-weekly-prerequisite-pipeline.md)、[训练系统设计](design/training-system.md)

- **改动描述**: 修复 Coros 活动详情百分之一米/秒、千分之一 kcal 单位、字段别名和多层 lap 未归一化的问题；原始详情保持可回放，统一摘要现在正确生成距离、时长、热量、平均/分段配速、心率、步频、步幅及 `frequencyList` L2 聚合，存量坏摘要在重同步时从本地详情幂等重建。今日摘要、周报和草稿最近两日按同一 summary_version 保留可用六项事实或显式 gap，活动重同步同步刷新平均心率与训练负荷，Garmin 行为不变。
- **影响范围**: `src/providers/normalization.py`、`src/activity.py`、`src/summary_extraction.py`、`src/main.py`、`src/training_day_summary.py`、`src/training_planning.py` 及对应测试
- **关联文档**: [Bug 记录：Coros 活动事实归一化](bugfixes/2026-08-14-coros-activity-fact-normalization.md)、[摘要提取设计](design/summary-extraction.md)、[数据源同步产品方案](product/data-source-sync.md)、[日报产品方案](product/daily-report.md)、[训练体验产品方案](product/training-experience.md)

- **改动描述**: 修复旧同步活动缺 `activity_summary_facts` 导致日报【今天对计划意味着什么】拿不到分段配速分析的问题：`_sync_activity_details` 新增 `needs_summary` 分支，有 detail 但缺分段摘要时用已有 detail 重建（intensity 配速带 / pace_profile 分位数 / structure 步频步幅），不强制重拉；`needs_relap` 收敛为仅在“有摘要但旧 schema 需升级 lapDTOs”时触发，两分支互斥。
- **影响范围**: `src/main.py`、`tests/test_main.py`
- **关联文档**: [Bug 记录：旧同步活动缺分段摘要](../bugfixes/2026-08-14-missing-summary-facts-segment-pace-analysis.md)、[摘要提取设计](../design/summary-extraction.md)

## 2026-08-14

### Added

- **改动描述**: 训练页与报告页周进度新增“手动刷新 + 数据新鲜度引导”：报告页 checkpoint 结果区新增三态新鲜度徽标（已是最新 / 检测到新训练数据 / 仍有未同步日期），按钮按渐进披露切换（有新数据时高亮“更新进度”、无变化时弱化“重新生成进度”、缺同步时引导“补齐同步数据”）；已归档自然周重新生成前提示将覆盖归档版本；训练页“本周节奏”卡新增“刷新进度”按钮与数据状态行（统计至 / 已覆盖天数 / 未同步警示），刷新只重新拉取首页数据不触发周复盘；`/api/reports/weekly` POST 响应附带 `last_sync` 供前端判定。同步完成后周进度不自动重算，由用户决定是否更新。
- **影响范围**: `src/web.py`、`web/templates/reports.html`、`web/templates/training.html`、`tests/test_web.py`
- **关联文档**: [训练系统设计 §7.2](design/training-system.md)、[报告中心产品方案](product/daily-report.md)、[训练体验产品方案](product/training-experience.md)

- **改动描述**: 个人档案（“我的”→身体数据）新增选填字段“静息心率”“最大心率”（`/api/profile` 的 `personal_info` 存储 `resting_heart_rate` / `max_heart_rate`），用于个人阈值心率锚定。
- **影响范围**: `src/web.py`、`web/templates/profile.html`
- **关联文档**: [训练体验产品方案 §8.3.2](../product/training-experience.md)、[过程记录：强度锚定改为档案推导](../process/2026-08-14-intensity-anchoring.md)

### Changed

- **改动描述**: 个人阈值心率/配速不再用近期训练平均心率/配速分位猜测：`AthleteBaselineBuilder` 改为档案锚定——显式阈值优先，其次由档案最大心率推导（Karvonen，阈值 ≈ 静息 + 0.88×(最大−静息)，仅最大心率时 ×0.88），再其次用档案 PB 推导阈值配速作为配速侧锚点（半马最接近阈值，10K/5K 按经验差折算，来源标记 `personal_bests`）；档案全无时阈值为空，训练内容识别输出“缺少个人阈值心率/配速，强度评估不可用”，不再给出“为个人阈值心率的 X%”这类会随训练构成漂移的高估数字。修复 `MemoryWriter._load_profile(self)` 误传 writer 导致档案从未加载的问题（改为 `self._memory_store`）。
- **影响范围**: `src/training_analysis.py`、`src/memory.py`、`src/training.py`、`tests/test_training_analysis.py`
- **关联文档**: [过程记录：强度锚定改为档案推导](../process/2026-08-14-intensity-anchoring.md)、[摘要提取设计 §9.4](../design/summary-extraction.md)、[训练系统设计](../design/training-system.md)、[日报产品方案](../product/daily-report.md)、[训练体验产品方案 §8.3.2](../product/training-experience.md)

- **改动描述**: Coros 活动详情取数自建请求保留高频时序字段：绕过 coros-mcp 对 `graphList` / `frequencyList` / `gpsLightDuration` 的丢弃，原始 Hz 级逐点数据随 `detail_json` 入库并对字段结构记录日志；活动项 `extra` 携带原始 `sportType`，拉详情时按 Provider 传入。
- **影响范围**: `src/providers/coros.py`、`src/main.py`、`tests/test_providers.py`
- **关联文档**: [过程记录：探索与接入 Coros Hz 级分段配速数据](../process/2026-08-14-coros-high-frequency-data.md)、[数据源同步产品方案 §5.1](../product/data-source-sync.md)

- **改动描述**: Coros 分段（`lapList`/`lapItemList`）接入统一分段序列：`_extract_splits` 与 `build_session_summary` 分段候选增加 `lapList`，`lapItemList` 子项优先展开；Coros 用户 `activity_splits` 不再恒空，日报“强度分布/配速节奏”可获得分段数据。
- **影响范围**: `src/activity.py`、`src/summary_extraction.py`、`tests/test_summary_extraction.py`
- **关联文档**: [摘要提取设计 §9.3](../design/summary-extraction.md)、[数据源同步产品方案 §5.1](../product/data-source-sync.md)

- **改动描述**: Coros 平台自算乳酸阈值接入强度锚定：`/analyse/query` 的 `lthr`（阈值心率）与 `ltsp`（阈值配速）随每日健康同步写入 `daily_health_metrics.lthr/ltsp`（加列迁移）；训练内容识别按“用户档案显式阈值 > 平台 lthr/ltsp > 档案推导（Karvonen/PB）”自动采用（取目标日及之前最近一条），实测该用户 lthr=169 后 avg 161 的课由推导 93% 变为平台值 95%。
- **影响范围**: `src/main.py`、`src/memory.py`、`src/training.py`、`src/training_service_factory.py`、`tests/test_training_analysis.py`
- **关联文档**: [过程记录：探索与接入 Coros Hz 级分段配速数据](../process/2026-08-14-coros-high-frequency-data.md)、[摘要提取设计 §9.4](../design/summary-extraction.md)、[日报产品方案](../product/daily-report.md)、[训练体验产品方案 §8.3.2](../product/training-experience.md)

- **改动描述**: Coros 健康同步解析平台自算乳酸阈值：`/analyse/query` 的 `lthr`（阈值心率 bpm）与 `ltsp`（阈值配速 s/km）进入 `DailyHealth.extra`；落库与强度锚定消费待后续迭代接入。
- **影响范围**: `src/providers/coros.py`、`tests/test_providers.py`
- **关联文档**: [过程记录：探索与接入 Coros Hz 级分段配速数据](../process/2026-08-14-coros-high-frequency-data.md)

## 2026-08-13

### Fixed

- **改动描述**: 修复受限版日报整块跳过 AI 洞察的问题：此前 `omitted_sections` 非空（如高驰未授权睡眠 → `omitted=["sleep"]`）时 `_do_daily_sync` 不调用在线 `review-daily-training`，`memory.py` 的 `ai_allowed` 门禁又强制 `ai_insight={}`，导致用户有跑步时【今天对计划意味着什么】没有任何跑步分析。现在受限版同样执行一次在线 Skill 调用（缺失维度标记 `status=unavailable` 并随 `omitted_sections` 传入，模型契约要求保留未知）；确定性兜底按 `omitted_sections` 跳过睡眠/恢复评分/ACWR/趋势结论，只保留运动概要、强度分布与当日训练分析，不伪造依赖缺失数据的结论。
- **影响范围**: `src/main.py`、`src/memory.py`、`tests/test_report_readiness.py`
- **关联文档**: [Bug 记录：受限版日报不生成 AI 洞察](../bugfixes/2026-08-13-limited-report-ai-insight-skipped.md)、[日报产品方案 §7](../product/daily-report.md)

- **改动描述**: 修复高驰日报“查看数据依据：恢复、负荷与教练观察”在仅返回部分恢复字段时的卡片错位：隐藏缺失指标后，恢复 Hero 与健康指标网格现在按实际可见数量重算列数，移动端连续回填并均匀分列，桌面端同步保持均分，不再留下固定列宽空位或孤立左对齐。
- **影响范围**: `web/templates/dashboard.html`、`tests/test_web.py`
- **关联文档**: [Bug 记录：高驰日报部分恢复指标错位](../bugfixes/2026-08-13-coros-dashboard-evidence-alignment.md)、[日报产品方案 §6](../product/daily-report.md)、[Web 模块设计](design/04-modules.md)

- **改动描述**: 修复训练页脚本被游离 `async` 语句中断（`ReferenceError: async is not defined`），页面永远停留在“正在读取实时训练方案…”，草稿向导与轮询无法初始化的问题；删除模板中残留的孤立 `async` 行，同步修正过时的模板断言（“跳过并生成/提交并生成”已移除）。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`
- **关联文档**: [Bug 记录：训练页脚本被游离 async 中断](../bugfixes/2026-08-13-training-draft-stuck-loading.md)

- **改动描述**: 修复日报详情在睡眠/恢复/负荷等维度缺失时渲染空白“—”卡片（白块）的问题：无数据模块直接隐藏、整组无数据隐藏整块区域、全部缺失时“数据依据”折叠区收拢为一行说明；同时修正负荷字段名读取（`acute_load_7d`/`chronic_load_28d`），此前慢性/急性负荷恒为空白，并隐藏急性/慢性均为 0 的无效 ACWR。
- **影响范围**: `web/templates/dashboard.html`、`docs/product/daily-report.md`
- **关联文档**: [Bug 记录：日报详情空白卡片](../bugfixes/2026-08-13-dashboard-report-empty-blocks.md)、[日报产品方案 §6.6](../product/daily-report.md)

## 2026-08-12

### Removed

- **改动描述**: 移除“模式转换待确认”模块：`coaching_mode_transition` 提案（propose/confirm/reject/pending）、`mode-transitions` 存储、`_ensure_race_transition`、激活流程的模式转换拦截与 `_apply_mode_transition` 全部删除；Web 3 个 mode-transitions API 与 MCP 3 个模式转换 tools 移除；训练页“模式转换待确认”区块与“恢复过渡”UI（recovery_transition 模式）移除。`coaching_mode` 改为由生效方案派生（有赛事备赛方案 → `race_preparation`，否则 `continuous_running`），不再持久化 `coaching-context.md`（旧文件仅读取兼容）。
- **影响范围**: `src/training.py`、`src/web.py`、`src/mcp_server.py`、`web/templates/training.html`、`tests/test_training.py`、`tests/test_web.py`、`tests/test_registration.py`
- **关联文档**: [过程记录：移除教练模式转换提案模块](../process/2026-08-12-remove-mode-transition.md)、[训练系统设计](design/training-system.md)、[教练 AI 设计](design/ai-coaching.md)、[训练体验产品方案](../product/training-experience.md)

### Changed

- **改动描述**: 日报页信息整合——教练观察不再单独占区块，四块话语与结论一起渲染在顶部"今日结论"区（结论一句话 + 观察段落），移除独立 AI 观察区块，顶部不再重复强调同一事实；页面顺序为：今日结论（含观察）→ 计划执行 → 当日训练事实 → 恢复 Hero → 训练负荷。
- **影响范围**: `web/templates/dashboard.html`、`tests/test_web.py`
- **关联文档**: [报告中心与日报产品方案 §3.3.4](../product/daily-report.md#334-教练观察内容规范已确认)

- **改动描述**: 日报“教练观察”运动概要补充训练效果解释：引用 `session_summary.effect` 输出有氧/无氧 TE 值与一句话强度含义（如“训练效果有氧 4.4，高强度刺激对体能提升作用明显”），`estimated=true` 必须标注“估算，依据心率/配速”，TE 不可得时如实说明；本地降级（`memory._effect_explanation`）与 AI Skill（`review-daily-training` 1.2.0）同步实现。
- **影响范围**: `src/memory.py`、`prompts/skills/review-daily-training/SKILL.md`、`tests/test_memory.py`
- **关联文档**: [报告中心与日报产品方案 §3.3.4](../product/daily-report.md#334-教练观察内容规范已确认)

- **改动描述**: 日报“教练观察”运动概要按照课型结构逐组展示每组快/慢配速与心率（间歇/变速/混合课且 `work_recovery_groups` 非空时，按组序号升序列出全部组，格式“第N组 快 X'/km(hrNN)→慢 Y'/km(hrNN)”）；`review-daily-training` SKILL 升级 1.2.0 强制该输出并写入完成条件，本地降级观察同步补充每组心率。
- **影响范围**: `prompts/skills/review-daily-training/SKILL.md`、`src/coach_runtime/registry.py`、`src/memory.py`、`tests/test_memory.py`
- **关联文档**: [报告中心与日报产品方案 §3.3.4](../product/daily-report.md#334-教练观察内容规范已确认)

- **改动描述**: 修复同步覆盖 lapDTOs 问题：`_sync_activity_details` 在 `needs_relap` 重拉时若 `fetch_activity_splits` 失败，不再用无 lapDTOs 的 detail 覆盖已有数据——原有 lapDTOs 被保留并重建新 schema summary，原有没有 lapDTOs 时跳过等待下次同步，避免已修复的分段数据被降级回旧 schema（如间歇 4 组被降成混合 1 组）。
- **影响范围**: `src/main.py`、`tests/test_main.py`
- **关联文档**: [Bug 记录：同步重拉失败覆盖 lapDTOs](../bugfixes/2026-08-12-sync-relap-failure-overwrites-lapdto.md)

- **改动描述**: 存量活动详情重拉兼容修复：`store_activity_detail` 按 `PRAGMA table_info` 的 name 列判断旧表是否仍有 `summary_version NOT NULL` 列并兼容 INSERT（此前取错列索引导致恒走新结构、旧表重建摘要必现 IntegrityError）；`ensure_tables` 的 DROP COLUMN 迁移失败不再静默。修复后 Garmin lapDTOs 重拉（`needs_relap`）能真正重建 `activity_summary_facts`：08-11 由 ×2 异常 splitSummaries 的 unknown 降级，恢复为“间歇结构（4 组快慢交替）· 置信 100% · 成分 fast 16%/slow 16%/body 68%”。
- **影响范围**: `src/activity.py`、`tests/test_storage.py`
- **关联文档**: [Bug 记录：lapDTOs 重拉后 session-summary 重建失败](../bugfixes/2026-08-12-lapdto-relap-summary-rebuild-failure.md)

- **改动描述**: 存量活动结构判定兜底：旧 schema summary 无 `segment_sequence`/`quantity_gate` 时，日报分析层从 `activity_splits` 重建分段特征序列并按分段距离合计 vs summary 距离重算量门禁，再做加权结构判定（如 08-11 单次跑步融合分段变速 → "混合 · 置信 100% · ×2 异常标记仅强度模式"，不再显示 unknown）；新 schema 数据仍优先用 summary 的 `segment_sequence`。新增 `memory._segment_sequence_fallback` 静态方法与对应测试。
- **影响范围**: `src/memory.py`、`tests/test_summary_extraction.py`
- **关联文档**: [运动摘要提炼技术设计 §10.5](design/summary-extraction.md#105-数据门禁与降级)、[报告中心与日报产品方案 §3.3.3](product/daily-report.md#333-训练结构识别加权确定性判定已确认)

- **改动描述**: 日报“教练观察”内容重排为四块自然语言解读并消除重复：运动概要（距离/用时/课型结构/平均配速）→ 强度分布解释与分析（配速带/心率带占比或分位数 + 平均步频/步幅，无心率标注 `basis=pace`、缺失维度如实声明）→ 恢复分析（睡眠/HRV/静息心率/身体电量/恢复评分合并为一段）→ 近 7 天负荷与恢复分析（ACWR 急性/慢性对比 + 近一周睡眠/HRV 趋势，缺 28 天基准只报急性负荷并说明无法判断相对位置，无证据不编造 ACWR）。同一事实只在四块中出现一次，不与结论/建议/警告重复；无结构判定时不再编造“稳定配速”；步幅单位由 cm 转米展示（如 1.27 m）。AI Skill（`review-daily-training`）与本地降级洞察（`memory._generate_ai_insight`）同形态输出。
- **影响范围**: `src/memory.py`、`prompts/skills/review-daily-training/SKILL.md`、`tests/test_memory.py`
- **关联文档**: [报告中心与日报产品方案 §3.3.4](../product/daily-report.md#334-教练观察内容规范已确认)、[记忆系统设计](design/memory-system.md#47-日报-ai-工作流-coachpy)、[运动摘要提炼技术设计](design/summary-extraction.md#96-展示)

### Added

- **改动描述**: 日报新增 `athlete_context` 能力与近期负荷背景：生成时从训练域只读能力画像（与方案草稿同口径 `capacity_profile(D)`，即 Web 已暴露的 `GET /api/training/capacity`）投影可持续周跑量、长距离、近期配速、历史已证能力与中断背景、负荷边界与 `facts_cutoff`；正文“负荷状态”章节展示一行紧凑能力参考，`review-daily-training` 在同一调用中引用背景解释当日/近期负荷相对个人可持续容量的位置。`training_load` 被省略的受限版或训练域读取失败时 `status=unavailable` 并省略结论，不编造能力数值；快照语义不反写历史日报。训练服务装配从 Web 路由固化为 `training_service_factory` 共享实现（Web/CLI/MCP 三入口同口径）。
- **影响范围**: `src/training_service_factory.py`（新增）、`src/web.py`、`src/main.py`、`src/memory.py`、`src/coach.py`、`src/mcp_server.py`、`src/render.py`、`prompts/skills/review-daily-training/SKILL.md`、`tests/test_training_service_factory.py`（新增）、`tests/test_main.py`、`tests/test_report_readiness.py`、`tests/test_render.py`、`tests/test_coach_provider.py`
- **关联文档**: [报告中心与日报产品方案](../product/daily-report.md#311-能力与近期负荷背景引用已确认)、[记忆系统设计](design/memory-system.md#日报能力与近期负荷背景引用已实现)、[训练系统设计](design/training-system.md)、[模块设计](design/04-modules.md)

- **改动描述**: 结构判定呈现每组配速明细：`classify_training_structure` 输出 `work_recovery_groups`（每组快段/恢复段配速与心率，恢复段按“比快段慢 ≥20% 且非微型段”配对，`segment_sequence` 增 `duration_s` 支撑）；日报正文/HTML 展示“每组配速：第 N 组 快 X'/km(hr..) → 慢 Y'/km(hr..)”；草稿 SKILL 引导引用结构判定与每组配速（识别质量课结构与衔接负荷）。
- **影响范围**: `src/summary_extraction.py`、`src/memory.py`、`src/render.py`、`prompts/skills/draft-training-scheme/SKILL.md`、`tests/test_summary_extraction.py`、`tests/test_render.py`
- **关联文档**: [运动摘要提炼技术设计](design/summary-extraction.md)

- **改动描述**: 训练结构判定进入教练观察与特点层：`review-daily-training` SKILL 引导 AI 引用 `structure_classification`（变速/间歇/节奏/有氧/混合 + 交替组数 + 置信度），教练观察不再缺失“变速跑/间歇结构”信息；`TrainingDaySummaryBuilder` 把结构判定并入 `observed_features`（`structure_*` feature），草稿/周报的 feature 聚合同样可见。
- **影响范围**: `prompts/skills/review-daily-training/SKILL.md`、`src/training_day_summary.py`、`src/training.py`、`tests/test_training.py`
- **关联文档**: [运动摘要提炼技术设计](design/summary-extraction.md)

- **改动描述**: 草稿/周报消费训练结构判定：`compact_session_summary_for_planning` 透传 `segment_sequence`，草稿 `_training_summary_context` 与周报 `review_week` 在组装事实包时用 AthleteBaseline 对每课做与日报同口径的确定性结构判定（`structure_classification`），SKILL 可引用训练结构（变速/间歇/节奏/有氧/混合）。
- **影响范围**: `src/summary_extraction.py`、`src/training.py`、`tests/test_training.py`
- **关联文档**: [运动摘要提炼技术设计](design/summary-extraction.md)

- **改动描述**: 同步层接入 Garmin `/splits` 官方分段（`lapDTOs`）：仅跑步活动拉取并注入 detail，`activity_splits` 与 `session-summary` 均优先使用（0811 验证 25 段、距离/时长比值 1.0000、四维度齐全 + `intensityType`），替代 `splitSummaries` 的 ×2 异常数据；4×1000+200 变速课识别为“间歇结构（4 组快慢交替）· 置信 100%”；存量旧活动由 `quantity_gate` 兜底为仅强度模式。
- **影响范围**: `src/activity.py`、`src/summary_extraction.py`、`tests/test_storage.py`、`tests/test_summary_extraction.py`
- **关联文档**: [运动摘要提炼技术设计](design/summary-extraction.md#104-数据源garmin-splits-lapdtos阶段-2-已探测确认)

- **改动描述**: 日报“训练结构”升级为加权确定性判定（不用 AI）：六类（变速/间歇/节奏/有氧/混合/未知）+ 加权证据（配速 0.40/心率 0.35/步频 0.15/步幅 0.10）+ 快慢交替检测 + 强度带分档 + 疲劳复合信号（心率漂移+步幅下降）与步频一致性。同步层 `segment_sequence`（逐段配速/心率/步频/步幅，Garmin averageSpeed 换算配速）+ `quantity_gate`（段距合计 vs summary 比值门禁，×2 等异常自动标记仅强度模式有效）。判定在分析层实时算（需个人阈值），不固化入库；四维度独立降级。
- **影响范围**: `src/summary_extraction.py`、`src/memory.py`、`src/render.py`、`tests/test_summary_extraction.py`、`tests/test_render.py`
- **关联文档**: [运动摘要提炼技术设计](design/summary-extraction.md#10-确定性特征提取与训练结构判定阶段-12026-08-12-已确认)、[报告中心与日报产品方案](../product/daily-report.md#333-训练结构识别加权确定性判定已确认)

- **改动描述**: 日报“训练细节分析”升级为确定性深度分析（统一 `session-summary` schema，L1 分段粒度）：整体水平（距离/用时含无暂停对比、平均配速折全马、最快配速、爬升/下降/海拔范围、平均/最大步频、热量、训练效果与强度分钟）+ 强度分布（分段粒度配速/心率带占比、配速分位数 P5–P95）+ 配速节奏（前后半程秒差与正负分段、段间 CV、结构性课型识别）。TE 缺失且个人阈值可用时本地估算并显式标记“估算、依据心率/配速”，阈值不可用显示“不可得”。`render._detail` 从字符串解析重写为 dict 结构卡片（修复与 session_analyses 结构脱节问题）。
- **影响范围**: `src/summary_extraction.py`、`src/memory.py`、`src/render.py`、`prompts/skills/review-daily-training/SKILL.md`、`tests/test_summary_extraction.py`、`tests/test_render.py`、`tests/test_storage.py`
- **关联文档**: [运动摘要提炼技术设计](design/summary-extraction.md)、[报告中心与日报产品方案](../product/daily-report.md#332-训练深度分析分段粒度已确认)

- **改动描述**: session-summary 统一收拢为单一 schema（不再有 v1/v2 并行版本，也移除持久化版本号）：深度分析字段直接并入，存量旧记录按默认值兼容读取，重算以 `detail_hash` 为准；`training_service_factory.load_week` 把 `activity_summary_facts` 统一附加到活动行，草稿、周报、训练首页与日报从同一来源消费；草稿/周报在组装事实包时用 `compact_session_summary_for_planning` 投影概要子集（消费侧压缩），日报保持全量。
- **影响范围**: `src/summary_extraction.py`、`src/training_service_factory.py`、`src/training.py`、`tests/test_summary_extraction.py`、`tests/test_training.py`、`tests/test_storage.py`
- **关联文档**: [运动摘要提炼技术设计](design/summary-extraction.md#9-统一收拢训练深度分析并入单一-session-summary2026-08-12-已确认)、[报告中心与日报产品方案](../product/daily-report.md#332-训练深度分析分段粒度已确认)

- **改动描述**: 新增草稿可执行性审计 Skill `audit-training-scheme`（独立 AI 调用，第 5 阶段）：审计长距离上限、质量课连排/恢复不足、周中长时课程（周一至周五按个人配速超 120 分钟判 fail，用户补充信息明确允许时豁免）；`verdict=fail` 自动带审计意见打回重生成近两周课表（最多 2 轮），仍 fail 则草稿标记 `audit_failed` 并展示原因；审计调用失败标记 `audit_unavailable` 不阻断草稿；单课时长超 `max_session` 仅 warning 不阻断。草稿返回新增 `audit` 字段，前端展示审计状态条。
- **影响范围**: `prompts/skills/audit-training-scheme/SKILL.md`、`src/coach_runtime/registry.py`、`src/coach_runtime/schemas.py`、`src/coach_runtime/runner.py`、`src/training_planning.py`、`src/training.py`、`web/templates/training.html`、`tests/test_training_planning.py`、`tests/test_coach_runtime.py`
- **关联文档**: [草稿可执行性审计技术设计](design/audit-training-scheme.md)、[训练体验产品方案](product/training-experience.md)

- **改动描述**: 确定性审计升级为“超集”把关：AI 审计漏报时确定性规则主动补抓 critical（长距离超限、周中长时、质量课连排/恢复不足），并新增周中连续休息规则（周一至周五可训练日内连续两天无训练 → warning）；`marathon_pace` 纳入质量课判定（M 配速课后次日长距离连排可被抓）。AI 报的 critical 仍按确定性复核降级误判。
- **影响范围**: `src/training_planning.py`、`tests/test_training_planning.py`
- **关联文档**: [草稿可执行性审计技术设计](design/audit-training-scheme.md)

- **改动描述**: 修复质量课工作段配速缺失：节奏/阈值课按课型生成 Z4 分段（不再误判为 Z5 repeat）、间歇课工作段 Z5 配速由 PB 推算兜底（`pb_derived`）；轻松跑热身段配速改为区间慢半段（渐进到主体），不再与主体完全一致。
- **影响范围**: `src/training_planning.py`、`src/training_pace.py`、`tests/test_training_pace.py`、`tests/test_training_planning.py`
- **关联文档**: [训练系统技术设计](design/training-system.md)、[配速校准增强技术设计](design/pace-calibration-enhancement.md)

- **改动描述**: 修复可训练日（`available_days`）外的不可训练日仍被排课：草稿投影时确定性校验 weekday，不可训练日（available_days 外或 fixed_unavailable_days）的课程强制转为休息；`build-near-term-schedule` SKILL 明确 weekday 必须在 available_days 内。
- **影响范围**: `src/training.py`、`prompts/skills/build-near-term-schedule/SKILL.md`、`tests/test_training.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-12-training-draft-untrainable-day-schedule.md)

- **改动描述**: 配速校准增强——天气归一化（夏季/高温活动配速折算回标准温度，用户补充信息“高温/炎热”更强修正）+ PB 交叉验证（样本不足时用 PB 推算兜底 Z 区间并标 `source=pb_derived`、Z4 上限封顶防虚快、近期样本显著慢于 PB 时标 `state_gap=current_below_pb`）；数据源为 `fitness-assessment` 的 PB 与目标。
- **影响范围**: `src/training_pace.py`、`src/training.py`、`tests/test_training_pace.py`
- **关联文档**: [配速校准增强技术设计](design/pace-calibration-enhancement.md)

- **改动描述**: 审计 critical 打回前增加确定性复核：长距离上限、周中长时课程、质量课连排三条数值规则用确定性代码复核（个人配速/全马比例/恢复阈值），AI 误判的 critical 自动降级为 warning 不触发打回（如 21km < 全马 50% 阈值 21.1km 被判超限的误判）；AI 审计只做定性判断，数值边界由确定性层把关。
- **影响范围**: `src/training_planning.py`、`tests/test_training_planning.py`
- **关联文档**: [草稿可执行性审计技术设计](design/audit-training-scheme.md)

- **改动描述**: 训练草稿课表新增配速安排：AI 每堂非休息课输出课型配速意图 `pace_intent`（easy/long_run/marathon_pace/threshold/interval/repetition/tempo/recovery/race_pace，只标锚点不编造数值）；确定性层按个人配速校准回填并持久化每课 `pace_targets`（状态/Z 区/区间/展示文本/依据/体感降级）；极端天气（补充信息文本探测）触发配速保守放慢，无个人事实或强度-距离矛盾时诚实降级为体感。
- **影响范围**: `src/coach_runtime/schemas.py`、`prompts/skills/build-near-term-schedule/SKILL.md`、`src/coach_runtime/runner.py`、`src/training.py`、`src/training_planning.py`、`src/training_pace.py`、`tests/test_coach_runtime.py`、`tests/test_training.py`、`tests/test_training_pace.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

### Changed

- **改动描述**: 草稿预览改为**阶段→周→课表三层下钻**：最上层为大阶段 chips（基础期/专项期等，含周数与阶段边界），点击后展示该阶段每周概要（第 x 周 · 日期范围 · 跑量 · 重点 · 恢复周）；点击周卡片展示该周课表（有详细课表时用日历式模块：质量课显示间歇/变速/节奏/LSD 等标志+距离，普通课只显示距离，休息日显示休息；无课表的周/阶段提示“根据执行情况继续安排”）；点击课表格子后细则模块展示配速、分段、完成标准、调整边界。桌面端课表与细则双栏且细则 sticky 可见，窄屏课表自动切换为逐日列表。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`、`docs/product/training-experience.md`、`docs/design/training-system.md`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: AI 配置切换为 DeepSeek 官方 API（`NEURUN_AI_BASE_URL=https://api.deepseek.com` + `NEURUN_AI_MODEL=deepseek-chat` + 官方 sk- key）：推理模型（krill-ai `deepseek-v4-flash-0731`、官方 `deepseek-v4-pro`）在草稿级任务上要么间歇性返回空 content，要么 reasoning 吃光 `max_tokens` 导致 `content=""`（`finish_reason=length`、`completion_tokens` 全为 `reasoning_tokens`）；非推理的 `deepseek-chat` 草稿两阶段完整成功且无 reasoning。
- **影响范围**: `.env`、`docs/design/ai-coaching.md`
- **关联文档**: [Bug 记录](bugfixes/2026-08-12-training-draft-reasoning-empty-content.md)

### Fixed

- **改动描述**: 修复草稿周量对齐把定时跑（无距离）算作 0 导致周目标全摊给距离型课程、长距离被放大到 40+km 的问题：`TrainingPlanReconciler.reconcile` 现在按个人配速 + 强度区间比例估算定时跑距离（标记 `distance_estimated`）后再统一对齐周目标。

- **改动描述**: 新草稿生成时自动归档旧 draft（标记 `superseded` 并记录 `superseded_by`，文件保留可回溯），避免草稿无限堆积；现有历史草稿已批量归档。
- **影响范围**: `src/training.py`、`tests/test_training.py`、`docs/design/training-system.md`
- **关联文档**: [训练系统技术设计](design/training-system.md)

- **改动描述**: 修复固定不可训练时间（`fixed_unavailable`）不参与课表排期的问题：新增 `_parse_fixed_unavailable` 把自由文本解析为排除的星期几并注入 `constraints.fixed_unavailable_days`，确定性排期与 AI 排课都避开这些日子（全部排除时退回原可训练日避免空排期）；`build-near-term-schedule` SKILL 增加必须避开规则。
- **影响范围**: `src/training_planning.py`、`src/training.py`、`prompts/skills/build-near-term-schedule/SKILL.md`、`tests/test_training_planning.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-12-training-draft-fixed-unavailable-day.md)
- **影响范围**: `src/training_planning.py`、`tests/test_training_planning.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-12-training-draft-duration-workouts-distance.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: 修复训练方案草稿 AI 调用必现失败：推理模型流式中间态 `content=""` 的 envelope 被误判为完整响应，提前 break 后 `json.loads("")` 抛错；完成判定现在要求 content 非空且为合法 JSON 对象，空/非法 JSON 内容自动重试一次（超时、4xx 不重试）。
- **影响范围**: `src/coach_runtime/runner.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-12-training-draft-reasoning-empty-content.md)、[AI 教练技术设计](design/ai-coaching.md)

- **改动描述**: 适配 DeepSeek 官方 API 流式时序与长输出：`build-training-framework` 的 `read_timeout` 由 35s 提至 120s（官方 API 先回 headers、随后静默推理数十秒再一次性发 body）；草稿两阶段 `max_tokens` 由 2200/1800 统一提至 4000（避免近期课表长输出被 `finish_reason=length` 截断为不完整 JSON）。
- **影响范围**: `src/coach_runtime/runner.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-12-training-draft-reasoning-empty-content.md)

## 2026-08-11

### Changed

- **改动描述**: 压缩训练草稿两阶段 AI 用户事实载荷：框架和近期课表调用不再发送请求/调用 trace 等运行诊断元数据，近期课表仅保留最近两个已结束自然日的会话摘要、恢复窗口和安全边界，去除重复活动列表。
- **影响范围**: `src/coach_runtime/context.py`、`src/coach_runtime/runner.py`、`src/training_planning.py`、`tests/test_coach_runtime.py`、`tests/test_training_planning.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: 压缩草稿 AI 两阶段载荷：参考周 `gaps` 按 `(field, reason, affects)` 去重（消除按天聚合的重复缺口条目）；近期两日数据只经 `AthleteCurrentState.short_term_training` 传入一次，移除顶层 `recent_two_days` 冗余副本。不改变模型可读信息量与草稿/审阅语义，仅降低 token 开销。
- **影响范围**: `src/training_planning.py`、`tests/test_training_planning.py`、`docs/design/training-system.md`
- **关联文档**: [训练系统技术设计](design/training-system.md)

### Fixed

- **改动描述**: 放大草稿预览近期课程的卡片字号与间距，并将配速、分段和调整边界保留在课程内的独立详情折叠中，避免复用七日周视图造成信息过度压缩。
- **影响范围**: `web/templates/training.html`、`docs/product/training-experience.md`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 新增仅本地调试可用的草稿 AI 全量调用日志：打印两阶段执行步骤、调用序号、完整 Prompt 与事实载荷；默认关闭，不写入数据库或 API，严禁线上启用。
- **影响范围**: `src/coach_runtime/runner.py`、`src/training_planning.py`、`.env.example`、`README.md`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: 修复训练草稿当前周与下一周只显示课程骨架、隐藏确定性处方的问题；草稿投影现在复用个人配速校准和当天安全边界，并允许展开查看配速、分段、完成标准及调整边界。无可靠事实时继续显示体感降级，不生成固定配速。
- **影响范围**: `src/training.py`、`web/templates/training.html`、`tests/test_training.py`、`tests/test_web.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)、[训练体验产品方案](product/training-experience.md)

- **改动描述**: 将训练框架的 Runner JSON 示例改为最小非空周期与负荷项，避免 Provider 复用空数组示例而遗漏必需的 `load_progression`；空列表仍由 Schema 严格拒绝。
- **影响范围**: `src/coach_runtime/runner.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 移除训练页“当前教练定位”状态横幅，避免重复展示模式说明；训练方案、目标与恢复过渡的现有交互不变。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`
- **关联文档**: [训练系统技术设计](design/training-system.md)

- **改动描述**: 提高训练框架 AI 输出上限并记录脱敏的阶段/Schema 失败原因，便于区分 JSON 截断、字段缺失和本地结构校验失败。
- **影响范围**: `src/coach_runtime/runner.py`、`src/training_planning.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 修复训练框架成功后进入近期课表阶段时漏传 `training_load_envelope`，导致必需事实校验失败。
- **影响范围**: `src/training_planning.py`、`tests/test_training_planning.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 按确认边界将近期课表模型输出的中文/英文星期名称及数字字符串规范化为 `0–6`，并保留非法值严格拒绝。
- **影响范围**: `src/coach_runtime/schemas.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 强化近期课表提示词的逐堂字段自检和 `intensity_intent` 最小 JSON 示例，保持严格 Schema 合同不变。
- **影响范围**: `prompts/skills/build-near-term-schedule/SKILL.md`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 按确认边界将严格 ISO 课程日期转换为星期数字，并为已有课程类型与体感描述、但缺少强度意图的非休息课补齐保守体感型结构，不生成配速或心率目标。
- **影响范围**: `src/coach_runtime/schemas.py`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 严格校验训练框架每个周期阶段必须有名称、正周数和训练目的，避免“后续周期”出现空白阶段卡片。
- **影响范围**: `src/coach_runtime/schemas.py`、`prompts/skills/build-training-framework/SKILL.md`、`tests/test_coach_runtime.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-framework-schema-diagnostics.md)

- **改动描述**: 修复训练页历史活动查询异常路径未释放数据库 session，导致连接池耗尽和页面持续加载。
- **影响范围**: `src/memory.py`
- **关联文档**: [Bug 记录](bugfixes/2026-08-11-training-page-connection-pool-exhaustion.md)

## 2026-08-10

### Added

- **改动描述**: 新增 Provider 运动详情的确定性 `SessionSummaryFacts` 提炼，按 L0/L1/L2 自动降级并持久化版本化聚合事实；缺少心率时显式使用 `basis=pace`，摘要失败不阻断活动详情入库。
- **影响范围**: `src/summary_extraction.py`、`src/activity.py`、`tests/test_summary_extraction.py`、`tests/test_storage.py`
- **关联文档**: [摘要提炼技术设计](design/summary-extraction.md)、[日报产品方案](product/daily-report.md)、[上线验收口径](operations/summary-extraction-launch-acceptance.md)

### Fixed

- **改动描述**: 修复摘要无强度数据时错误声明 pace 代理、偶数分段半程差计算，并将版本化聚合事实接入训练分析、单日摘要和报告文本读取边界。
- **影响范围**: `src/summary_extraction.py`、`src/activity.py`、`src/memory.py`、`src/training_day_summary.py`、相关回归测试
- **关联文档**: [摘要提炼技术设计](design/summary-extraction.md)、[日报产品方案](product/daily-report.md)、[E2E 验收报告](testing/2026-08-11-summary-extraction-e2e.md)

### Changed

- **改动描述**: 收敛训练草稿两阶段 AI 输入：框架阶段与近期课表阶段改用按职责裁剪的上下文，移除完整事实包、恢复快照和历史摘要的重复嵌套，同时保留目标、能力、最近两天、配速、负荷边界及数据缺口。
- **影响范围**: `src/training_planning.py`、`src/coach_runtime/registry.py`、`tests/test_training_planning.py`、`tests/test_coach_runtime.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: 将案例驱动的训练草稿从单次长 AI 请求收敛为同步/摘要、能力与目标并行准备、训练框架、近期课表和本地校验的分阶段 DAG；缺失数据改为可见降级生成，错误主操作统一为“重新生成”并按失败阶段断点续跑。
- **影响范围**: `src/training_planning.py`、`src/coach_runtime/`、`src/ai_inference_coordinator.py`、`prompts/skills/`、`web/templates/training.html`、相关测试
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[实现过程记录](process/2026-08-10-case-driven-training-dag.md)

## 2026-08-10

### Added

- **改动描述**: 在训练方案建立向导提交草稿前增加一次可跳过的 `additional_context` 补充窗口；短文本只作为本次 AI 草稿请求的未验证用户自述，空值/跳过不阻断生成，草稿仅保留脱敏元数据而不保存原文。
- **影响范围**: `web/templates/training.html`、`src/training.py`、`src/training_planning.py`、`prompts/skills/draft-training-scheme/SKILL.md`、相关测试
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[上线验收口径](operations/review-training-plan-launch-acceptance.md)、[实现过程记录](process/2026-08-10-training-draft-additional-context.md)

## 2026-08-09

### Fixed

- **改动描述**: 训练草稿不再只展示“周一/周二”等相对星期；AI 继续输出 `weekday` 课程槽位，确定性排期层按推荐开始日期生成当前周与下一周的具体日期投影，预览只读展示日期且不增加课程编辑能力。
- **影响范围**: `src/training.py`、`web/templates/training.html`、`prompts/skills/draft-training-scheme/SKILL.md`、`prompts/skills/revise-training-scheme/SKILL.md`、相关测试
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[Bug 记录](bugfixes/2026-08-09-draft-date-projection.md)

### Changed

- **改动描述**: 新增共享单日训练事实摘要：确定性层按自然日归一化活动类型、跑量、时长、恢复、睡眠、负荷、覆盖状态和结构化 evidence；训练草稿只参考上一完整自然周的 4–8 周聚合及最近两天衔接事实，日报和周报复用同一摘要。可选 `summarize-training-day` 仅补充语义特点，AI 不可用不阻断事实链路，也不新增页面或设置。
- **影响范围**: `src/training_day_summary.py`、`src/memory.py`、`src/training.py`、`src/training_planning.py`、`src/coach.py`、`src/coach_runtime/`、`prompts/skills/`、相关测试
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[日报产品方案](product/daily-report.md)、[训练系统技术设计](design/training-system.md)、[AI 教练设计](design/ai-coaching.md)、[上线验收口径](operations/shared-daily-summary-acceptance.md)、[实现过程记录](process/2026-08-09-shared-training-day-summary.md)

- **改动描述**: 训练排课增加最近两个已结束自然日的衔接约束：长跑或质量课次日不安排强度，长距离优先安排在可训练周末；同时对基础/适应→专项强度→高峰→减量的周期顺序增加非阻断审阅。
- **影响范围**: `src/training.py`、`src/training_planning.py`、`prompts/skills/draft-training-scheme/SKILL.md`、`prompts/skills/revise-training-scheme/SKILL.md`、`prompts/skills/review-training-plan/SKILL.md`、相关测试
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[Bug 记录](bugfixes/2026-08-09-training-schedule-transition-and-periodization.md)

- **改动描述**: 训练草稿的周量/负荷基线改为上一完整自然周，排除当前动态周和滚动 7 天数据；上一周仅作为事实参考，草稿先由 entry review 判断是否具备缩短适应周期或提前进入更高阶段的条件，再决定是否小幅拔高。
- **影响范围**: `src/web.py`、`src/training.py`、`src/training_planning.py`、`prompts/skills/draft-training-scheme/SKILL.md`、相关测试
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[Bug 记录](bugfixes/2026-08-09-draft-uses-dynamic-week.md)

## 2026-08-08

### Changed

- **改动描述**: 收敛训练草稿 Validator：要求 AI 在一次输出中遵守完整处方合同，但对缺少完成分类的半成品 Workout Steps v2 由 Normalizer 确定性补齐，不再因可修复字段缺失直接判定草稿失败。
- **影响范围**: `src/training_planning.py`、`prompts/skills/draft-training-scheme/SKILL.md`、`tests/test_training_planning.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[Bug 记录](bugfixes/2026-08-08-draft-validator-too-strict.md)

- **改动描述**: 重组训练草稿预览的信息层级，默认依次展示能力/负荷/周期逻辑、建议切入阶段、当前周与下一周逐日课程明细，以及后续周期摘要；数据依据、系统规范化和不确定性收纳到只读折叠区，安全提示和确认入口保持可见。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: 收敛训练方案审阅链路：新增统一 `review-training-plan` Skill；草稿在同一次 AI 请求中完成生成、自审和可安全应用的 entry 修正；完整自然周且存在生效方案时由 execution review 一次输出周解释和待确认方案决策；`review-training-week` 不再独立产出 `plan_review`。
- **影响范围**: `prompts/skills/review-training-plan/`、`prompts/skills/draft-training-scheme/SKILL.md`、`src/coach_runtime/`、`src/training.py`、`tests/test_coach_runtime.py`、`tests/test_training.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[实现过程记录](process/2026-08-08-plan-reconciliation-review.md)

- **改动描述**: 草稿预览补充建议起始阶段、阶段依据和每节课的配速/体感执行目标；草稿课程复用个人配速校准与当天安全调整逻辑，Provider 的英文课程短语由中文 Prompt 和训练页词典共同收敛。
- **影响范围**: `src/training.py`、`web/templates/training.html`、`prompts/skills/draft-training-scheme/SKILL.md`、`prompts/skills/revise-training-scheme/SKILL.md`、`tests/test_training.py`、`tests/test_web.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[实现过程记录](process/2026-08-08-draft-entry-phase-pace.md)

- **改动描述**: 将周目标与课程累计结算、草稿 `entry_review` 和完整自然周后的 `plan_review` 纳入同一轻量审阅链路；课程算术差异由本地 reconciler 对齐或结算并记录调整，不再阻断草稿，审阅结果必须经过用户确认才会影响后续安排。
- **影响范围**: `src/training_planning.py`、`src/training.py`、`src/coach_runtime/schemas.py`、`prompts/skills/draft-training-scheme/SKILL.md`、`prompts/skills/revise-training-scheme/SKILL.md`、`prompts/skills/review-training-week/SKILL.md`、`tests/test_training.py`、`tests/test_training_planning.py`、`tests/test_coach_runtime.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[上线验收口径](operations/review-training-plan-launch-acceptance.md)、[实现过程记录](process/2026-08-08-plan-reconciliation-review.md)

- **改动描述**: 将训练方案周期、负荷、恢复和配速合理性沉淀为同次 AI `review` 约束，`TrainingSchemeValidator` 仅保留结构、字段和 Workout Steps v2 的最小执行底线；一般审阅项不再阻断草稿，`safety_hold` 由用户确认。
- **影响范围**: `src/training_planning.py`、`src/training.py`、`src/coach_runtime/schemas.py`、`prompts/skills/draft-training-scheme/SKILL.md`、`prompts/skills/revise-training-scheme/SKILL.md`、`tests/test_training_planning.py`、`tests/test_coach_runtime.py`
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)、[实现过程记录](process/2026-08-08-ai-review-minimal-validator.md)

## 2026-08-07

### Changed

- **改动描述**: 任务查询返回 404 时立即清理训练页本地旧 task ID 并停止轮询，避免服务重启后持续请求已不存在的旧任务。
- **影响范围**: `web/templates/training.html`、`tests/test_web.py`。
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

- **改动描述**: 增加草稿 Provider 与本地确定性阶段的分段耗时日志，区分响应头、首字节、完整 JSON、连接收尾、JSON 解码、Normalizer 和 Validator；日志不记录 Prompt、训练事实、响应正文或密钥。
- **影响范围**: `src/coach_runtime/runner.py`、`src/training_planning.py`、`tests/test_coach_runtime.py`。
- **关联文档**: [训练系统技术设计](design/training-system.md)

- **改动描述**: 精简 `draft-training-scheme` 的核心 Prompt，只保留可行性、阶段、当前周/下一周课程骨架和能力优先的强度决策；算术约束、步骤生成、配速解析与最终安全校验继续由本地确定性层负责，避免重复指令和首四周输出要求拖慢 Provider 推理。
- **影响范围**: `prompts/skills/draft-training-scheme/SKILL.md`、`src/coach_runtime/registry.py`、`tests/test_coach_runtime.py`。
- **关联文档**: [训练体验产品方案](product/training-experience.md)、[训练系统技术设计](design/training-system.md)

### Fixed

- **改动描述**: 草稿创建和更新改为异步任务，页面可在刷新后恢复任务进度并在完成后读取中文训练方案预览。
- **改动描述**: 草稿任务运行期间锁定现实约束表单，避免编辑值与正在执行的输入快照不一致。
- **改动描述**: 草稿/重算总等待上限由 90 秒放宽至 300 秒（5 分钟），覆盖 Provider 长尾响应，仍保留 45 秒连续无数据读取上限与手动重试边界。
- **影响范围**: `src/ai_inference_coordinator.py`、`src/web.py`、`web/templates/training.html`。
- **关联文档**: [训练体验](product/training-experience.md)、[训练系统设计](design/training-system.md)

- **改动描述**: Provider 已交付完整草稿 JSON 时立即进入本地校验，不再因等待连接关闭而误报生成超时。
- **改动描述**: 将两周紧凑草稿输出上限收至 1800 tokens，减少 Grok 4.5 长尾响应；保留 Provider 原有协议兼容性。
- **影响范围**: `src/coach_runtime/runner.py`、训练草稿与方案调整请求。
- **关联文档**: [训练系统设计](design/training-system.md)、[连接收尾超时修复](bugfixes/2026-08-07-training-draft-provider-stream-timeout.md)

neurun 项目变更日志，按日期倒序。

---

## 2026-08-07

### Changed
- **进一步收敛草稿输出**: 两周 AI 课程骨架限制为短字段、单个替代方案和单个调整触发条件，Provider 输出上限从 4500 调整为 2600 tokens；本地四周延展和 Workout Steps v2 逻辑不变。
- **影响范围**: src/coach_runtime/runner.py, prompts/skills/draft-training-scheme, prompts/skills/revise-training-scheme, docs/process/2026-08-07-training-draft-latency.md
- **关联文档**: [训练系统技术设计](design/training-system.md), [训练草稿延迟过程记录](process/2026-08-07-training-draft-latency.md)
- **训练草稿 AI 输出窗口收敛为两周**: `draft-training-scheme` 与 `revise-training-scheme` 只要求当前周和下一周课程骨架，不再要求完整 `training_prescription`；Normalizer 延展后两周并补全 Workout Steps v2，读模型和历史完整四周候选保持兼容。
- **影响范围**: prompts/skills/draft-training-scheme, prompts/skills/revise-training-scheme, src/coach_runtime/runner.py, src/coach_runtime/schemas.py, src/training_planning.py, docs/product/training-experience.md, docs/design/training-system.md, tests/test_coach_runtime.py, tests/test_training_planning.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [实现过程记录](process/2026-08-07-training-draft-latency.md)
- **训练方案草稿延迟收敛**: AI 草稿输出改为课程骨架与强度意图，Workout Steps v2、完成标准、旧客户端 blocks 和个人目标由确定性 Normalizer 补全；保留完整 AI 处方兼容路径，将草稿/改期 Provider 单次读取上限统一为 45 秒、输出上限调整为 4500 tokens，不增加页面、后台任务或自动重试。
- **影响范围**: src/coach_runtime/runner.py, src/coach_runtime/schemas.py, src/training_planning.py, prompts/skills/draft-training-scheme, prompts/skills/revise-training-scheme, tests/test_coach_runtime.py, tests/test_training_planning.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [实现过程记录](process/2026-08-07-training-draft-latency.md)

## 2026-08-07

### Changed
- **报告主线与训练执行边界确认**: 报告中心负责运动事实、执行、表现、恢复、目标进程和下一阶段建议；训练模块负责将结构化建议转为可确认、可执行的训练方案，报告不得直接改写方案。新增 `Athlete Habit Profile` 领域语言，并显式区分 `completion` 完赛目标与 `performance` 成绩突破目标。
- **报告建议衔接训练提案**: 已归档周复盘可通过 `POST /api/training/proposals/from-report` 或 MCP `propose_training_adjustment_from_report` 生成带报告引用的 `pending` 方案提案，默认下周生效；当前方案只有用户确认后才改变。目标建立向导和目标存储同步 `goal_intent`，旧目标按目标成绩兼容推断。
- **影响范围**: CONTEXT.md, src/main.py, src/training.py, src/web.py, src/mcp_server.py, web/templates/setup.html, web/templates/reports.html, web/templates/training.html, tests/test_training.py, tests/test_web.py, docs/product/daily-report.md, docs/product/training-experience.md, docs/design/training-system.md, docs/design/memory-system.md
- **关联文档**: [报告中心产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [实现过程记录](process/2026-08-07-report-first-product-boundary.md)

- **训练配速决策改为能力与状态优先**: 当天配速先使用当前可持续能力和近期同类表现，再在有生效目标且状态稳定时允许最多 1% 的目标导向推进；恢复和疼痛负责安全修正，ACWR 仅作次级风险校验，不再单独触发 2% 放慢。同步更新训练 Skill、产品/技术边界、运行时解释和回归测试。
- **影响范围**: prompts/skills/draft-training-scheme, prompts/skills/revise-training-scheme, src/training.py, src/training_pace.py, tests/test_training_pace.py, tests/test_training.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [实现过程记录](process/2026-08-07-training-pace-priority.md)

## 2026-08-06

### Changed
- **训练方案精简规则进入核心运行时**: 实现生效/草稿方案下的新目标门禁、同日单活动高置信度匹配与 `completed|partially_completed|skipped|unmatched` 执行状态、疼痛当日只读安全指引、长期可训练日同一 `plan_id` 下周生效、目标改期预览/确认、排期取消退回草稿和候选失败不写半份版本；同步更新 Web/MCP 反馈、改期和取消排期接口。未引入伤病管理、Safety Hold 生命周期、暂停态或课程编辑器。
- **影响范围**: src/training.py, src/training_planning.py, src/memory.py, src/web.py, src/mcp_server.py, web/templates/training.html, tests/test_training.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [实现过程记录](process/2026-08-06-training-rule-convergence.md)
- **长期可训练日变化失败安全**: 生成、校验或写入失败时不产生半份 Scheme Version，当前方案和本周安排保持不变；仅允许用户主动重试，不自动重试或后台补写。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **长期可训练日变化从下周生效**: `new_available_days` 复用既有反馈入口并生成同一 `plan_id` 的 Scheme Version，默认 `effective_from` 为下一个自然周周一；当前周继续按原安排和已确认局部调整执行，不回溯、不重排。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **长期可训练日变化复用既有反馈入口**: 不新增设置页；“时间或安排变了”可选提交 `new_available_days`，确认后在同一 `plan_id` 下生成新的 Scheme Version，旧版本保留。临时日期变化仍写入当前自然周 Local Schedule Adjustment，不创建新方案主线。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **临时改日期只做局部调整**: 不增加设置页；用户通过“时间或安排变了”提交当前周日期变化，确认后只写入 `Local Schedule Adjustment`，保留 Training Goal、当前 Training Scheme 和历史版本，不归档整份方案、不创建新 `plan_id`。只有跨周负荷、阶段或长期路线变化才在同一 `plan_id` 下生成新 Scheme Version。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练详情只展示当前版本与最近一次调整**: 用户界面、Web/API/MCP 普通读模型不展示方案版本时间线、历史列表、差异、撤销或恢复；后台继续保留不可变 Scheme Version、调整提案和审计记录，用于按日期解释历史课次和故障追溯。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练后不提供完成状态手动按钮**: 执行状态由活动匹配自动产生，训练后只补充体感、疼痛和完成质量；用户明确选择“今天没练”才写入 `skipped`。Web/API/MCP 不接受用户写入 `completed` 或 `partially_completed`，也不新增完成状态确认流程。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **执行状态移除替代完成**: 第一版计划课只保留 `completed`、`partially_completed`、`skipped` 和 `unmatched`；不同训练不再自动判断为替代完成，不建设替代状态、聚合或展示。无法可靠归入计划课的活动只标记为“计划外训练”。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **第一版不提供人工关联活动**: 训练课只做同日、单活动的高置信度自动匹配；无法确定时保持“未匹配到记录”，用户只能明确选择“今天没练”。不建设活动选择器、手动录入、跨日替代或多活动拼接；多余活动只标记为“计划外训练”。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **方案预览默认展开首周并收起后三周细节**: 完整首四周仍由同一次 AI 候选生成并经过 Schema、Normalizer 与 Validator 校验，Web/API/MCP 数据合同不裁剪。页面默认展开候选首周；第二至第四周先显示周跑量、关键课和训练目的摘要，可就地展开已经返回的逐日课程。折叠不触发生成或请求、不保存新状态，也不提供编辑。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **建立方案的可训练约束收敛为星期集合与统一时长**: 第一版只让用户选择常规每周可训练星期，并填写一个适用于所有训练日的单次最长时间；不建设早晚时段、逐日时长、日期例外、请假或旅行日历。方案生效后的临时变化统一通过“时间或安排变了”进入既有待确认调整流程，Web/API/MCP 拒绝 `cross_training` 等多余建立字段。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **完整方案生成态只保留单一等待提示**: 请求期间仅显示“正在生成方案…”并禁用重复提交，不显示百分比、分析/规划/校验阶段或预计时间，也不提供取消按钮；用户离开页面即终止当前请求，迟到结果不得写入。成功直接进入预览，失败只显示“重试”。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **完整训练方案失败后不自动重试**: 新建、重新生成、方案级重规划和目标改期替代草稿均在单次请求的端到端时限内完成；失败后只显示用户触发的“重试”，不创建后台任务、自动重试、轮询或完成通知。`Retry-After` 仅作提示，MCP 也必须由新的 tool call 重试；周复盘等其他异步旅程不在本次范围内。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练建立流程取消能力编辑**: 能力摘要继续由活动与历史事实自动计算并只读展示，第一版不让用户填写或纠正当前周跑量、历史稳定水平和中断原因。用户只修改目标、可训练日、单次最长时间和开始日期；Web/API/MCP 移除 capacity write 与 `reported_weekly_mileage` 等新写入字段，存量事实只读兼容。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **输入变化后旧草稿不可回退确认**: 用户修改目标、可训练日或单次最长时间后，旧草稿和启用预览立即退出当前读模型；新草稿生成失败时页面只显示失败与重试，Web/API/MCP 均不返回或确认旧草稿，也不增加 stale 生命周期状态。active Training Goal 继续占用唯一目标主线。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **重新生成后不保留旧草稿**: 启用前始终只保留同一 plan_id 的最新草稿；重新生成成功后原地原子替换，不创建草稿历史、差异比较、撤销或恢复能力，也不产生半份新草稿。输入变化后的失败行为按最新指纹门禁处理；正式启用后的方案版本与调整历史仍保留。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **方案预览不允许直接编辑课程**: 周跑量、训练日期、课程类型、训练步骤和单课内容全部只读，Web/API/MCP 不提供拖拽、替换、增删或字段覆盖。用户只能修改目标、可训练日、单次最长时间或开始日期，系统随后对同一 plan_id 整体重新生成并校验唯一草稿。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **历史训练数据不足不阻断建方案**: 只有赛事、距离、日期、可训练日或单次最长时间缺失时才在 AI 调用前返回字段级补充提示；可选目标成绩或历史活动、能力、配速证据不足时仍生成低置信度、保守起步的唯一方案并正常一次确认。方案生成移除 `recommendation_status=insufficient_data`，周复盘的同名 Progression Decision 保持不变。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **具有挑战的目标不增加二次确认**: `challenging` 仍生成唯一训练方案候选，风险、取舍和调整触发条件直接展示在方案预览中，用户继续只执行一次“确认该安排”；不新增风险弹窗、勾选框、确认端点或生命周期状态。只有 `not_recommended` 才阻止生成方案并要求先修改目标。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **不可行训练目标只给一条修改建议**: AI 判断原目标无法安全形成方案时不生成 Training Scheme Draft，也不展示保守、平衡等路线列表；只返回一项 Feasibility Recommendation，优先放宽可选目标成绩，只有赛事日期无法支持安全周期时才建议改期。用户接受或自行修改同一目标后再生成唯一草稿，建议过期或目标版本变化时不得写入。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)

## 2026-08-05

### Changed
- **完整训练方案取消规则版兜底**: 新建方案、目标改期替代草稿和完整重新生成只有在 AI 候选通过 Schema、Normalizer 与 Validator 后才形成；任何技术失败都保持原目标、草稿或生效方案并提供重试，不写规则版完整候选。确定性规则只保留事实归一化、机械安全修正、Validator、疼痛安全指引和简单局部调整；历史 fallback 方案只读兼容。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **周执行移除综合完成率与加权评分**: 本周节奏和周复盘只展示计划量、实际量，以及完成、部分完成、跳过、未匹配和计划外训练的独立计数；不再为部分完成、未匹配或计划外训练配置权重，也不生成依从性分数。Progression Decision 直接读取执行、负荷、恢复、能力和数据完整性事实。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **计划外训练只标记不自动重排**: 计划休息日活动或完成计划课后的额外活动只显示“计划外训练”，继续计入实际负荷、能力事实和报告，但不抵扣未来课次、不换课、不生成提案或自动重排。用户需要变化时主动点击“重新生成安排”，复用既有待确认提案流程。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **迟到高置信度活动可更正已确认跳过**: 用户选择“今天没练”后若 Provider 到达高度匹配且无竞争归属的活动，系统追加新的 Execution Record，更正为完成或部分完成并保留原 skipped 与用户确认历史；低置信度只提示候选关联。执行更正不创建补课、局部调整、提案或方案版本。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练跳过只接受用户明确确认**: 没有匹配活动时始终保留 `unmatched`；同步完成、日期结束、周末复盘或后台任务都不能推断用户没练。课程卡仅允许用户关联其他活动或选择“今天没练”，后者才写入 `skipped`。周复盘分列未匹配与跳过，未匹配关键课只降低数据置信度，不单独触发调整。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **漏练与部分完成不再自动补课**: Execution Record 只保存实际发生的完成量与状态，未完成距离、时长、组数或刺激不进入补课队列，不顺延到次日、不压缩进本周剩余课程，也不提高后续剂量。累计偏离确需调整时只生成待确认提案，确认前后续课表保持不变。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **当日与当周调整不再升级长期方案版本**: 用户仍只执行“确认调整”，系统按影响范围应用；仅涉及当前自然周日期、时长、训练量、替代课程或休息时写入不可变 Local Schedule Adjustment 并递增周修订号，跨周负荷、阶段、关键课演进或目标路线变化才生成新 Scheme Version。局部调整保留原课次、参与执行比较并通过周修订号阻止并发覆盖。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **疼痛反馈拆分为当日安全指引与长期调整**: 疼痛反馈成功后立即以只读 overlay 收紧当日训练展示，无需先确认且不改写方案；AI 可解释或选择更严格行动，确定性规则提供最低安全兜底。长期变化仍通过待确认提案，“保持原计划”只拒绝后续调整，不能恢复当天原强度。本设计不引入 Safety Hold、pause 或伤病状态。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **课前训练反馈收敛为三个入口**: “调整今天”只展示“时间或安排变了”“身体状态不好”“疼痛或不适”；天气、场地和日程冲突归入安排变化的结构化原因，训练后感受回到完成记录。目标领域类型同步收敛为 `constraint_change | fatigue | pain | post_workout`，存量记录只读兼容，实施时将同步 Web API 与 MCP schema。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练调整提案移除继续讨论分支**: 用户只保留“确认调整”和“保持原计划”两个动作，不建立提案会话或消息历史；补充事实改为提交新的结构化反馈，新提案成功写入后原子替代同范围旧待确认提案，失败时旧提案保持有效。被替代提案进入调整记录但不可再批准。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练目标与方案收敛为单一当前主线**: 明确一个账户从 draft 起就只能有一份当前训练方案，scheduled 与 active 继续占用同一主线；任一当前状态都禁止创建第二个训练目标、草稿或并行后续方案。用户确认目标改期后废弃旧方案并保留历史，基于更新后的同一目标重新生成待确认安排，不建立 active 与未来 successor 并行模型。本条仅完成产品、领域语言与技术设计确认，运行时门禁和改期事务仍待实现。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md, docs/adr/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [ADR-0012](adr/0012-enforce-a-single-current-training-scheme.md)
- **训练板块第一版只保留目标赛事方案**: 无目标赛事时不生成滚动四周方案，不维护 continuous_running、race_preparation 或 recovery_transition 教练模式；完赛、取消或主动结束后归档方案并回到无方案状态。现有 coaching mode 存储、Web API 和 MCP tools 将在实施时移除。本条仅完成产品、领域语言与技术设计确认，运行时尚未收敛。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md, docs/adr/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [ADR-0013](adr/0013-limit-training-to-race-goal-schemes.md)
- **周中启用统一展示为本周剩余安排**: 用户可在周中确认并立即生效；只安排生效日至周日，不补排此前课程、不计算完整周达成率且不计阶段周，下周一进入第一个完整周。`bridge` 仅保留为内部兼容字段，不再作为用户需要理解的概念。本条仅更新产品和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **进程判断对用户收敛为三种结果**: 内部继续保留六种 Progression Decision 供规则和审计使用；报告与训练界面只展示“按原方案继续”“建议调整方案”或“数据不足，请先补充或同步”，具体判断和依据按需收纳在“为什么这样建议”。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **方案启用收敛为单一推荐安排**: 启用页不再并列提供稳妥、正常或快速恢复策略；系统依据能力、恢复与数据质量计算唯一推荐安排，用户只确认或修改开始日期，修改后重新计算预览。内部策略枚举仅保留为计算与审计字段。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **目标改期增加失败安全预览**: 改期先生成不进入当前方案主线的 Goal Rescheduling Preview；生成失败、事实变化或预览过期均保持旧目标与旧方案不变。用户确认有效预览后才原子更新目标、废弃旧方案并提交唯一替代草稿；文件存储通过 transaction journal 与单一可见提交点防止半完成状态。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md, docs/adr/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [ADR-0012](adr/0012-enforce-a-single-current-training-scheme.md)
- **目标日期次日自动结束训练方案**: 目标日期当天继续提供比赛安排；次日起所有训练读写幂等关闭当前方案，匹配赛事活动时记为 completed，否则以 `target_date_reached` archived，不伪造完赛。两种结果都停止后续排课并释放唯一方案主线，迟到活动只补赛后结果。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md, docs/adr/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [ADR-0013](adr/0013-limit-training-to-race-goal-schemes.md)
- **移除训练方案暂停生命周期**: 第一版状态只保留 draft、scheduled、active 及终态，不提供 pause/resume。明确日期的暂时不可训练区间通过待确认提案调整同一方案；没有恢复日期且不再备赛时只允许结束归档，存量旧暂停记录升级后归档。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md, docs/adr/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [ADR-0012](adr/0012-enforce-a-single-current-training-scheme.md)
- **取消已排期开始后返回方案预览**: scheduled 方案的取消动作收敛为“取消开始安排”，原 schedule 与确认记录留作历史，同一 plan_id 从不可变排期版本派生新 draft 并返回启用预览；目标和方案内容不丢失，唯一方案主线不释放，也不生成执行课次。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **放弃时同时归档训练目标与方案**: 新流程同一时间最多一个 active Training Goal；“放弃目标与方案”或“结束备赛”经确认后原子归档 active goal 与可空 current scheme，保留历史并释放唯一主线。completed/archived 目标不再自动预选，避免孤立 active goal 让用户再次被旧目标锁住。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md, docs/adr/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [ADR-0012](adr/0012-enforce-a-single-current-training-scheme.md)
- **训练方案建立向导由五步收敛为三步**: 用户只经历目标赛事、现实约束、方案预览与开始确认；能力档案由系统自动生成并折叠在“为什么这样安排”，只有会改变安全起点的缺失事实才追加追问，用户仍可主动纠正。默认现实约束只要求可训练日和单次最长时间。本条仅更新产品、领域语言和技术设计，运行代码未修改。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)

## 2026-08-04

### Removed
- **删除无当前需求的通用 Chat 与冗余 AI 编排**: Web 不再注册 `/api/chat/stream`，删除流式 Chat 客户端、旧 fallback、日报 Tool Use Agent、应用工具定义、运行期 ToolPolicy、贡献 Skill 编排和六个没有直接产品入口的 Skill；原 `chat.html` 日报仪表盘按实际职责更名为 `dashboard.html`。MCP 保留为外部 AI 客户端的独立事实与业务工具接口。
- **影响范围**: src/coach.py, src/web.py, src/coach_runtime/, prompts/, web/templates/dashboard.html, tests/
- **关联文档**: [报告中心产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [AI 教练工作流设计](design/ai-coaching.md), [开发过程](process/2026-08-04-ai-workflow-convergence.md)

### Added
- **Workout Steps v2 逐段可执行训练处方**: 新生成的非休息课程现在必须包含有序步骤；变速、间歇和法特莱克明确组数、工作段、恢复段、分段剂量、Z1–Z5 意图、转场与结构化完成分类。AI 候选、确定性兜底、个体化目标解析、Validator、训练前说明、Web 时间轴与 MCP 返回合同同步升级；个人事实不足时仅分段降级为体感，不丢失快慢结构。历史 v1 方案保持只读兼容，不静默猜测或覆盖。
- **影响范围**: prompts/skills/, src/coach_runtime/, src/training_planning.py, src/training_pace.py, src/training.py, src/memory.py, src/mcp_server.py, web/templates/training.html, web/templates/chat.html, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-08-04-workout-steps-v2.md), [README](../README.md)
- **个体化动态配速处方 v1**: 新增版本化 Pace Calibration Profile，使用用户确认阈值或最近 28–42 天可信同类训练分析校准 Z1–Z5；每日训练结合近期表现、恢复和负荷只做保持、放慢或体感降级。训练首页、周课程详情和训练前说明直接展示结果与必要安全行动，依据通过“为什么这样建议”按需展开；展开不触发生成或写入。
- **影响范围**: src/training_pace.py, src/training_analysis.py, src/training.py, src/coach.py, src/mcp_server.py, web/templates/training.html, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-08-04-dynamic-pace-guidance.md), [README](../README.md)

### Changed
- **在线 AI 收敛为六条单 Skill 工作流**: 日报、周复盘、方案草稿、局部调整解释、方案重规划和比赛策略各自只运行一个主 Skill；运动者建档、计划执行匹配、恢复判断和训练前说明改由表单或确定性代码持有。日报先在内存中构建一次，选择在线或本地洞察后只渲染并落盘一次。
- **影响范围**: src/main.py, src/memory.py, src/coach.py, src/coach_runtime/, src/training.py, src/training_planning.py, tests/
- **关联文档**: [报告中心产品方案](product/daily-report.md), [AI 教练工作流设计](design/ai-coaching.md), [记忆系统设计](design/memory-system.md), [开发过程](process/2026-08-04-ai-workflow-convergence.md)
- **先明确关键课逐段可执行处方合同**: 将原 Training Prescription 定位为课程级基础框架，不再把“热身 / 模糊主体 / 放松”视为完整关键课；产品与技术设计先规定 Workout Steps v2 的重复组、工作/恢复段、分段强度意图、个人目标解析、完成分类、AI/确定性分工和旧方案兼容边界，随后按同日 Added 条目完成实现。
- **影响范围**: docs/product/training-experience.md, docs/product/index.md, docs/design/training-system.md, docs/design/index.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **AI 准入改为用户公平的 32 并发独立线程池**: 同一用户跨日报、周复盘和方案生成最多一个任务；不同用户共享单进程 32 个执行槽位和 64 个总接纳位，等待最多 5 秒且不占用线程。重复提交返回 `429 ai_request_in_progress`，全局超量或等待超时返回 `503 ai_capacity_reached`。
- **影响范围**: src/ai_inference_coordinator.py, src/web.py, src/config.py, .env.example, docker-compose.yml, README.md, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [报告中心产品方案](product/daily-report.md), [AI 教练设计](design/ai-coaching.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-08-04-ai-inference-admission-control.md)
- **训练调整记录支持分页归档**: 已确认、未采纳或过期的调整提案现在在训练页“计划与数据”中按每页 5 条展示；待确认提案继续留在执行区，不会被归档或隐藏。周复盘维持报告中心的独立分页，不新增重复的调整入口。
- **影响范围**: src/training.py, src/web.py, web/templates/training.html, tests/test_web.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **报告归档列表支持独立分页**: 日报与已完成周复盘均按每页 7 项读取，提供当前页、总页数、总数及上一页/下一页操作；分页 URL 状态互不影响，翻页不会改变日期选择、二级 Tab 或触发报告生成。日报分页读取会同时返回已选日期的摘要，避免历史日报因不在当前页而被误判为不存在。
- **影响范围**: src/web.py, web/templates/reports.html, tests/test_web.py
- **关联文档**: [日报产品方案](product/daily-report.md), [训练系统技术设计](design/training-system.md)
- **训练页按执行注意力重排**: 首要区域现在只依次呈现“今日怎么跑、接下来三天、本周节奏”；完整七日课表默认折叠，长期方案、能力/数据状态与教练模式收纳在“计划与数据”。待确认的调整和模式转换保持独立可见，避免隐藏用户需要决策的事项。
- **影响范围**: web/templates/training.html, tests/test_web.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-04-training-page-attention-hierarchy.md)
- **移除三域模型在训练方法学中的残留地位**: 五级模型现在是课程编排、展示和安全校验的唯一强度语言；个人阈值、临界速度、近期有效跑步和体感只用于校准 Z1–Z5，不再引用或换算三域模型。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练处方切换为五级主体强度模型**: AI 编排和训练页使用 Z1 恢复、Z2 轻松有氧、Z3 稳态/专项耐力、Z4 阈值、Z5 间歇/高强度表达课程主体；热身和放松可低于主体等级。个人配速/心率仍须由实际阈值或近期事实支持，五级模型不等同于设备默认心率区间。
- **影响范围**: prompts/skills/draft-training-scheme, prompts/skills/revise-training-scheme, src/training_planning.py, web/templates/training.html, src/coach_runtime/registry.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-08-04-training-prescription-and-skill-constraints.md)
- **AI 方案增加方法学参考与可审计边界**: `draft-training-scheme` / `revise-training-scheme` 以个人生理锚点、近期个人事实优先、非固定分布策略和风险降级为共同合同；极化、金字塔、阈值型等体系只用于按阶段选择策略，不得照搬为固定模板。
- **影响范围**: prompts/skills/draft-training-scheme, prompts/skills/revise-training-scheme, prompts/skills/evaluate-plan-execution, src/coach_runtime/registry.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-08-04-training-prescription-and-skill-constraints.md)
- **训练方案升级为体系约束下的结构化训练处方**: 课程不再以固定课型枚举表达；方案 Skill 按阶段、训练刺激、能力、恢复和现实约束组合课程块。今日训练和本周安排展示距离/时长优先级、结构、通俗强度、完成标准与调整边界；个人事实不足时不伪造配速或心率。日报将使用同一处方评价训练执行。
- **影响范围**: CONTEXT.md, prompts/skills/, src/coach_runtime/, src/training_planning.py, src/training.py, src/memory.py, web/templates/training.html, web/templates/chat.html, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-04-training-prescription-rpe-only.md), [开发过程](process/2026-08-04-training-prescription-and-skill-constraints.md)
- **训练首页重排并补齐长期阶段时间线**: 首屏回到“今日训练 → 本周安排 → 长期方案”；教练定位与能力数据状态移至辅助区。长期方案新增所有阶段、阶段目的、加权计划时间线与百分比；衔接周不计入阶段进度，页面明确该图不等同于自动训练推进。能力卡同步标明其为估算、事实截止时间与本周覆盖。
- **影响范围**: src/training.py, web/templates/training.html, tests/test_training.py, tests/test_web.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-04-training-home-timeline-and-facts.md)

### Fixed
- **方案级重规划消除串行 AI 超时链路**: 重规划不再先调用周复盘 AI，也不再依次运行目标可行性、计划执行和恢复贡献 Skill；本地结构化事实一次性交给主 `revise-training-scheme`。完整四周候选仍受 90 秒总预算约束，单次读取上限由 20 秒调整为 45 秒，超时后继续保留安全候选且不会误标 AI 推荐。
- **影响范围**: src/training.py, src/training_planning.py, src/coach_runtime/runner.py, src/coach_runtime/registry.py, prompts/skills/revise-training-scheme, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [AI 教练设计](design/ai-coaching.md), [训练系统技术设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-04-scheme-revision-ai-timeout.md)
- **周复盘在线 AI 任务长期卡在生成中**: 有生效方案时，周复盘不再串行调用计划执行、恢复和主复盘三个模型请求；本地结构化事实一次性交给主复盘 Skill。模型响应改为带总预算的分块读取，周复盘最迟约 45 秒转入完整确定性兜底并释放账户级任务占位。
- **影响范围**: src/training.py, src/coach_runtime/runner.py, tests/test_training.py, tests/test_coach_runtime.py, README.md
- **关联文档**: [Bug 记录](bugfixes/2026-08-04-weekly-review-ai-task-stuck.md), [报告中心产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md)
- **无方案周复盘只有总次数和跑量**: 周复盘现在独立生成本周概览、最近完整周趋势、恢复/负荷风险和下周行动；补充运动日、时长、最长单次与可信训练结构，并在归档卡片中提供完整复盘展开。比较只使用同步完整的最近四个自然周，周末恢复快照也按复盘日期截断，避免历史报告读取未来状态。
- **影响范围**: src/training.py, src/web.py, web/templates/reports.html, tests/test_training.py, tests/test_web.py, README.md
- **关联文档**: [Bug 记录](bugfixes/2026-08-04-no-plan-weekly-review-too-shallow.md), [报告中心产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [记忆系统设计](design/memory-system.md)
- **刷新后可恢复 AI 生成状态与周中进度结果**: 已接纳任务现在保留用户隔离的等待、生成、完成或失败状态和真实耗时；报告页刷新后自动恢复并轮询，完成后直接显示周中进度或刷新正式归档，不再只提示任务占用，也不伪造百分比。
- **影响范围**: src/ai_inference_coordinator.py, src/web.py, web/templates/reports.html, tests/
- **关联文档**: [Bug 记录](bugfixes/2026-08-04-ai-task-progress-lost-after-refresh.md), [报告中心产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [AI 教练设计](design/ai-coaching.md), [训练系统技术设计](design/training-system.md)
- **无训练方案时周复盘被阻塞**: 周复盘先独立汇总自然周实际活动、恢复、能力与数据完整度；Training Goal、草稿和生效方案均不再是生成前置条件。存在历史生效方案时才附加计划执行、适应信号和 Progression Decision，方案只覆盖部分自然周时仅评价生效窗口。报告页可正常展示空方案关联，并将“制定训练方案”作为可选动作。
- **影响范围**: src/training.py, src/coach_runtime/registry.py, prompts/skills/review-training-week/SKILL.md, web/templates/reports.html, tests/test_training.py, tests/test_web.py, tests/test_coach_runtime.py, README.md, docs/design/memory-system.md
- **关联文档**: [Bug 记录](bugfixes/2026-08-04-weekly-review-without-plan.md), [报告中心产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md)
- **周课表无法查看非当天的完整训练内容**: 每个非休息日卡新增“查看训练详情”，在同页展开目标、Z1–Z5 主体强度、体感、训练块、完成要求和调整边界；不产生任何写入。
- **影响范围**: web/templates/training.html, tests/test_web.py
- **关联文档**: [Bug 记录](bugfixes/2026-08-04-weekly-workout-detail-unreachable.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **轻松跑把相互矛盾的距离和时长展示为可执行双目标**: 新增以近期个人跑步中位配速校验的 `pacing_guard`；冲突课程在训练页和训练前说明收紧为单一安全目标，AI 候选会被 Validator 拒绝。移除固定 5/6 分钟每公里的时间容量/时长换算。
- **影响范围**: src/training_planning.py, src/training.py, web/templates/training.html, tests/
- **关联文档**: [Bug 记录](bugfixes/2026-08-04-easy-run-dose-consistency.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md)
- **训练首页将能力估算误呈现为训练事实与推进**: 原“训练事实与推进”卡仅展示当前可持续周跑量，既不是本周实际跑量，也不能直接推导训练阶段。现在改为有数据边界的能力参考，阶段位置以独立计划时间线呈现。
- **影响范围**: src/training.py, web/templates/training.html, tests/test_training.py
- **关联文档**: [Bug 记录](bugfixes/2026-08-04-training-home-timeline-and-facts.md), [训练系统技术设计](design/training-system.md)

## 2026-08-03

### Changed
- **训练概念收敛**: Training Goal 只定义赛事/成绩等目标结果，不再保存或询问“目标周跑量”；周跑量统一由当前能力、训练方案和自然周计划表达。周复盘统一由报告中心生成与归档，训练页只深链到对应自然周；训练调整及其历史仅属于训练页，不再作为报告中心的重复标签或 API。
- **影响范围**: CONTEXT.md, src/training.py, src/main.py, src/coach.py, src/mcp_server.py, src/web.py, web/templates/reports.html, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [报告中心产品方案](product/daily-report.md), [训练系统技术设计](design/training-system.md), [README](../README.md)

### Added
- **Strava 个人用户活动同步方案**: 在既有数据源绑定旅程中定义官方 OAuth、最小活动授权、Token 轮换、活动详情/laps/streams、健康数据缺口、Webhook 增量更新、隐私、限流、断开与真实账号验收边界；当前仅为 Proposed，未修改运行时代码或 README。
- **影响范围**: docs/product/data-source-sync.md, docs/design/12-multi-platform.md, docs/product/index.md, docs/design/index.md
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [多平台技术设计](design/12-multi-platform.md)
- **报告中心与显式周报归档**: 主导航将“日报”统一为“报告”；报告页聚合日报、自然周周报和调整记录。周报仅由用户显式生成/刷新，保存事实截止时间、方案版本、执行摘要和 Progression Decision；训练页只深链至报告，报告页只深链回训练确认调整。
- **影响范围**: src/training.py, src/web.py, web/templates/training.html, web/templates/reports.html, web/templates/sync.html, web/templates/chat.html, web/templates/profile.html, tests/
- **关联文档**: [报告中心产品方案](product/daily-report.md), [训练系统技术设计](design/training-system.md), [记忆系统设计](design/memory-system.md), [Bug 记录](bugfixes/2026-08-03-week-review-read-mutates-decision.md), [README](../README.md)
- **训练方案启用与自适应推进核心闭环**: 新增基于已同步事实的 Athlete Capacity Profile、用户确认历史能力事实、可失效 Activation Preview、今天/下周/指定日期启用、未来 scheduled 方案、非周一 Bridge Week 和 Progression Decision。训练、同步、日报通过同一事实截点联动：同步只更新事实，训练重新计算候选，日报回到原日期重新检查完整性，三者都不自动改写对方结果。
- **影响范围**: src/training.py, src/web.py, src/coach.py, src/mcp_server.py, web/templates/training.html, web/templates/sync.html, web/templates/reports.html, prompts/, tests/, docs/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [日报产品方案](product/daily-report.md), [数据源同步产品方案](product/data-source-sync.md), [README](../README.md)
- **海外部署渠道与架构设计**: 新增设计文档，给出 Hetzner / DigitalOcean / Vultr / Lightsail 等 VPS、Cloudflare Registrar / Porkbun 域名、Cloudflare Free 入口网关的选型结论，以及与现有 Web Chat 单机 Docker 方案对齐的架构、SSE/回源注意点、落地步骤与验收标准；明确验收前不写入 README 作为已上线能力。
- **影响范围**: docs/design/14-overseas-deployment.md, docs/design/index.md, docs/design/13-sae-deployment.md
- **关联文档**: [海外部署设计](design/14-overseas-deployment.md), [Web Chat 部署设计](design/13-sae-deployment.md)
- **目标赛事专业方案与 Coach Skill runtime**: 新增 12 个仓库内 Coach Skill、受限 registry/runner、不可变运行上下文、输出 Schema 和工具副作用策略；方案草稿由 PlanningFactPack、训练容量边界、`draft-training-scheme` 与确定性 Validator 共同生成，页面展示可行性、置信度、完整周期、首四周、依据和不确定性。AI 或校验失败时显式标记规则兜底。
- **影响范围**: src/coach_runtime/, src/training_planning.py, src/training.py, prompts/coach-core.md, prompts/skills/, web/templates/training.html, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md), [开发过程](process/2026-08-03-professional-coach-skills.md), [README](../README.md)
- **赛事备赛执行适应闭环**: 新增训练前说明、自然周执行复盘与 AdaptationSignal、Skill 辅助局部调整、完整方案重规划、赛前 21 天比赛策略；方案级候选由 `revise-training-scheme` 生成并经过 Validator，确认前不改变当前方案，确认后在同一 plan_id 下生成新版本。
- **影响范围**: src/training.py, src/training_planning.py, src/web.py, src/coach.py, src/mcp_server.py, web/templates/training.html, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md), [README](../README.md)

### Changed
- **报告日期选择统一为带上下文的范围控件**: 日报与周复盘不再裸露原生日期输入；两者均显示日期/自然周摘要、可见的精确修改字段和相邻日期快捷操作。日报会提示已有报告及完整性状态，并以用户本地今天限制未来日期。
- **影响范围**: web/templates/reports.html, tests/test_web.py
- **关联文档**: [日报产品方案](product/daily-report.md), [记忆系统设计](design/memory-system.md), [Bug 记录](bugfixes/2026-08-03-report-date-picker-unstyled.md)
- **训练目标统一收敛到训练模块**: “我的”移除目标列表和目标增删改；训练的建立向导成为唯一的目标选择、新建与编辑入口。已生效方案的目标或赛事变化统一从训练页发起方案级重规划，当前版本在确认前保持不变。
- **影响范围**: web/templates/profile.html, web/templates/training.html, tests/test_web.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-03-training-goal-entry-duplication.md), [README](../README.md)
- **单日同步日期选择分层为“目标 + 修改”**: 同步日历仍是主要日期上下文；单日卡将本次同步目标、完整日期、星期及既有同步状态与可见的“修改日期”字段分开，保留前后一天和昨天/今天快捷操作。选择会同步更新日历高亮，未来日期不可选。
- **影响范围**: web/templates/sync.html, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [数据流设计](design/05-data-flow.md), [Bug 记录](bugfixes/2026-08-03-sync-single-date-picker-interaction.md)
- **报告中心按任务拆分并收紧日报详情层级**: `/reports` 改为日报、周复盘和调整记录三个互斥二级页；同步返回保留日报日期与 Tab，训练页周链接直达周复盘。日报详情改为“今日结论 → 计划执行与目标进展 → 当日训练事实 → 可展开的数据依据”，不再让恢复指标、AI 长文本和负荷卡与行动结论竞争首屏。
- **影响范围**: src/training.py, src/web.py, web/templates/reports.html, web/templates/chat.html, web/templates/training.html, web/templates/sync.html, tests/
- **关联文档**: [日报产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [数据源同步产品方案](product/data-source-sync.md), [记忆系统设计](design/memory-system.md), [训练系统设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-03-report-center-information-architecture.md), [README](../README.md)
- **训练方案启用与自适应推进设计**: 将固定“确认当天生效、按日历逐周升级”扩展为跨时间 Athlete Capacity Profile、可失效 Activation Preview、未来开始的 Activation Schedule、非周一 Bridge Week 和 Progression Decision；当前可持续能力决定安全起点，历史已证明能力只决定恢复上限和推进观察窗口。正常周滚动与缩短阶段、提高负荷等重大方案改写分离，后者继续要求用户确认。后续实现已在同日 Added 条目记录。
- **影响范围**: CONTEXT.md, docs/product/training-experience.md, docs/design/training-system.md, docs/process/2026-07-31-training-system.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-07-31-training-system.md)
- **Web 部署文档交叉引用海外部署**: [13-sae-deployment.md](design/13-sae-deployment.md) 背景与成本节指向海外渠道附录；design 索引补充 §14。
- **影响范围**: docs/design/13-sae-deployment.md, docs/design/index.md
- **关联文档**: [海外部署设计](design/14-overseas-deployment.md), [Web Chat 部署设计](design/13-sae-deployment.md)
- **AI 服务配置改为供应商无关命名**: 日报洞察、Web 流式对话和 Coach Skill 共用 `NEURUN_AI_API_KEY`、`NEURUN_AI_BASE_URL`、`NEURUN_AI_MODEL`，支持 OpenAI-compatible 根地址或完整 Chat Completions 端点；默认继续使用 DeepSeek，不保留供应商专用环境变量或类名兼容层。
- **影响范围**: src/config.py, src/coach.py, src/coach_runtime/, src/training_planning.py, docker-compose.yml, .env.example, tests/, README.md, docs/design/
- **关联文档**: [AI 教练设计](design/ai-coaching.md), [记忆系统设计](design/memory-system.md), [ECS 部署设计](design/13-sae-deployment.md), [README](../README.md)
- **日报生成前数据完整性门禁**: Web、CLI 与 MCP 共用 readiness 模型，按 Provider 能力和逐维度同步证据判断 `ready / limited / blocked`；核心活动覆盖不足时禁止写报告和调用 AI，辅助维度不足时仅允许用户显式生成受限版并省略相关结论。今天的报告保存数据截止时间并标记为暂态；日报页“前往同步”会保留生成日期、预填单日同步、切换对应月份并锚定同步区域。
- **影响范围**: src/report_readiness.py, src/storage.py, src/memory.py, src/main.py, src/web.py, src/mcp_server.py, src/render.py, prompts/coach.md, web/templates/reports.html, web/templates/chat.html, web/templates/sync.html, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [日报生成与数据完整性](product/daily-report.md), [记忆系统设计](design/memory-system.md), [数据流](design/05-data-flow.md), [开发过程](process/2026-08-03-daily-report-readiness.md), [README](../README.md)
- **持续陪伴与赛事备赛双向模式状态机落地**: 新增 `continuous_running`、`race_preparation`、`recovery_transition` 持久化上下文和两阶段转换提案。确认赛事草稿才进入备赛；结束备赛先确认进入恢复过渡，再确认回到持续陪伴；转换展示能力变化和方案收尾并保留全部历史。Web、MCP 和 Coach 工具合同同步扩展。
- **影响范围**: src/training.py, src/web.py, src/coach.py, src/mcp_server.py, web/templates/training.html, tests/, README.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md), [README](../README.md)

### Fixed
- **训练方案页面主题不再与主应用漂移**: 训练页改为复用 fresh / sport / dark 的全局主题令牌，保留训练状态的语义表达但不再使用独立深绿或棕橙页面配色；模板测试防止同名主题再次出现色值分叉。
- **影响范围**: web/templates/training.html, tests/test_web.py
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-03-training-page-theme-drift.md)
- **日报补齐可解释的计划执行与目标进展**: 日报 Front Matter 与 Web 固定聚合报告日生效课程、当天活动事实、截至报告日的自然周跑量、阶段/目标日期和恢复建议；活动同步未知显示待同步，不把空记录视为未完成，周累计只统计跑步活动。报告只深链训练页，保持不写入方案或宣称目标成败。
- **影响范围**: src/memory.py, src/web.py, web/templates/chat.html, tests/
- **关联文档**: [日报产品方案](product/daily-report.md), [记忆系统设计](design/memory-system.md), [Bug 记录](bugfixes/2026-08-03-daily-report-plan-execution-summary.md), [README](../README.md)
- **训练前说明不再暴露内部依据或等待在线 Skill**: 训练前说明改为本地即时规则，只输出运动者可执行的目的、强度边界、替代方式和停止信号；恢复/负荷原始字段、Skill trace 与降级原因不进入 Web DTO。
- **影响范围**: src/training.py, web/templates/training.html, tests/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统设计](design/training-system.md), [Bug 记录](bugfixes/2026-08-03-training-session-brief-raw-evidence-and-latency.md), [README](../README.md)
- **历史日报不再套用后来生效的训练方案**: Training Scheme 新增激活与日期生效生命周期，日报、Coach
  和历史训练查询统一按报告日期解析方案版本与具体课次；报告日期早于首个生效方案时写入
  `no_effective_plan / not_applicable`，不再输出“未执行”或偏离计划。`get_training_plan` MCP/Coach
  工具新增可选 `target_date`，当前实时读取行为保持兼容。
- **影响范围**: src/training.py, src/memory.py, src/coach.py, src/mcp_server.py, prompts/, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [Bug 记录](bugfixes/2026-08-03-daily-report-plan-time-travel.md), [日报产品方案](product/daily-report.md), [训练体验产品方案](product/training-experience.md), [训练系统设计](design/training-system.md), [记忆系统设计](design/memory-system.md), [README](../README.md)
- **报告日活动被误称为前一日训练**: 日报日期现在是活动归属的唯一锚点，选择日期 `D` 只汇总 `D` 当天活动；Front Matter 新增规范字段 `daily_activities` 并兼容旧 `yesterday_activities`，Web、CLI、MCP、AI 上下文、Markdown、HTML 和 PNG 统一改为“当日训练”。在线 AI 的相对日期文本在返回后按报告日期规范化，避免历史日报再次出现“今日/昨日”歧义。
- **影响范围**: src/memory.py, src/coach.py, src/main.py, src/mcp_server.py, src/render.py, src/web.py, prompts/coach.md, web/templates/chat.html, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [Bug 记录](bugfixes/2026-08-03-report-day-activity-labeled-yesterday.md), [日报生成与数据完整性](product/daily-report.md), [记忆系统设计](design/memory-system.md), [README](../README.md)
- **AI 阶段周数偏差不再导致整份训练方案降级**: 专业方案结果拆分 AI 来源、校验状态、推荐状态和综合展示状态；阶段结构完整时按相对权重规范化到真实赛事周数并保护减量下限，同时公开所有确定性修正。具有挑战或信息不足的 AI 结果继续展示证据、风险和可选路线，只有模型失败或无法机械修正的安全冲突才进入规则兜底。
- **影响范围**: src/training_planning.py, src/training.py, web/templates/training.html, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [Bug 记录](bugfixes/2026-08-03-ai-periodization-over-fallback.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md), [README](../README.md)
- **专业训练方案错误降级为 DeepSeek 400**: Coach Skill 模型适配层统一为所有主 Skill 和贡献 Skill 注入 DeepSeek JSON Output 协议与精确输出字段示例，并将非 200 响应收敛为脱敏、可操作的错误类型；方案候选新增只向下夹紧安全负荷和机械对齐课程距离的确定性规范化，完成后仍需通过 Validator。虚构数据在线双 Skill 链路已返回 `generation_mode=skill`。
- **影响范围**: src/coach_runtime/runner.py, src/training_planning.py, prompts/skills/draft-training-scheme/SKILL.md, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [Bug 记录](bugfixes/2026-08-03-training-skill-deepseek-json-contract.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md), [README](../README.md)

## 2026-08-02

### Fixed
- **训练方案确认前无法调整草稿**: 最终确认页新增修改目标、训练基础和现实约束的明确入口；用户可纠正当前通常周跑量，修改后在同一份草稿上重新计算建议和首周结构，仍需再次显式确认才会生效。
- **影响范围**: src/training.py, src/web.py, web/templates/training.html, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [Bug 记录](bugfixes/2026-08-02-training-draft-confirmation-not-editable.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [README](../README.md)

### Changed
- **持续跑步陪伴与赛事备赛可双向转换**: 两个定位调整为同一用户在不同阶段的教练模式，而不是固定人群标签。无赛事用户可先使用共享分析、恢复和周复盘能力并积累数据；建立目标赛事后经可行性评估和确认进入备赛，完赛、取消或退出后经恢复过渡回到持续跑步。转换保留全部事实与历史，禁止按目标或日期静默切换。本轮仅更新 Proposed 设计。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md, docs/design/ai-coaching.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md)
- **一阶段定位为目标赛事备赛教练**: 第一阶段围绕明确目标赛事构建从运动者了解、目标可行性、周期方案、训练前说明、日报与周复盘、局部调整、方案级重规划到比赛策略的完整闭环；无赛事滚动训练保留兼容入口但不纳入专业能力验收。Coach Skill 集合和迁移顺序同步调整，本轮仅更新 Proposed 设计。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md, docs/design/ai-coaching.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md)
- **专业训练方案制定与 Coach Skill 解耦方案**: 明确现有固定比例生成器只作为确定性兜底；正式草稿由共享结构化事实、方案制定 Skill 和确定性安全校验共同产生。日报分析与方案制定拆为两个独立主 Skill，只共享领域事实，不读取彼此的自然语言输出；新增目标可行性、周期化、首四周、生成模式和规划 trace 合同。本轮仅更新 Proposed 设计，不改变运行时行为。
- **影响范围**: docs/product/training-experience.md, docs/design/training-system.md, docs/design/ai-coaching.md
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md)

## 2026-07-31

### Changed
- **Coach Skills 重构方案沉淀**: 在现有 AI 教练技术真相源中明确单 Prompt 拆分边界、六个候选 Coach Skills、一个主 Skill 加少量贡献 Skill 的结构化协同协议、运行时工具权限、自然周与活动状态等领域不变量边界、分阶段迁移和基线对照评测；方案明确第一版继续使用 DeepSeek Tool Use，不引入 LangChain。当前仅为 Proposed，未创建 Skill 或修改运行时。
- **影响范围**: docs/design/ai-coaching.md
- **关联文档**: [AI 教练设计](design/ai-coaching.md)

### Added
- **训练方案建立向导与目标合并**: 将“我的”中的 Training Goal 设为目标唯一真相源，训练方案通过 `goal_id` 引用并保存确认时快照；无方案页改为目标选择或快速创建、28 天训练基础确认、现实约束、方案草稿和最终确认五步向导。周跑量由系统根据已同步训练基础和可训练时间提出，覆盖不足保持未知并使用说明充分的保守建议。按用户决定不新增存量数据迁移。
- **影响范围**: CONTEXT.md, src/training.py, src/web.py, web/templates/training.html, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-07-31-training-system.md), [README](../README.md)
- **交互式训练方案核心闭环**: 新增训练主界面、实时今日/自然周/长期方案、无方案草稿确认、旧方案安全迁移、四类快捷反馈、结构化调整差异、拒绝/过期/版本冲突/幂等确认和不可变历史版本；Web、MCP 与 AI Coach 共用 `TrainingService`，原整段 Markdown 覆盖工具已废弃。活动缺少同步或匹配证据时保持等待匹配，疼痛反馈只允许降级或暂停。
- **影响范围**: src/training.py, src/web.py, src/coach.py, src/mcp_server.py, prompts/coach.md, web/templates/, tests/, README.md, docs/product/, docs/design/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-07-31-training-system.md), [README](../README.md)
- **训练内容识别 v1**: 新增训练主类型与地形属性双轴分析，结合活动汇总、分段、活动发生前 28–42 天个人基线和恢复状态识别有氧、节奏、间歇、坡地、越野及山地训练；结果以版本化派生记录保存，包含数据质量、置信度、证据和后续训练约束，并接入日报、AI 教练历史上下文和 MCP 活动详情。当前不自动改写训练方案，也不覆盖平台原始负荷。
- **影响范围**: src/training_analysis.py, src/activity.py, src/main.py, src/memory.py, src/coach.py, src/mcp_server.py, src/providers/, src/storage.py, prompts/coach.md, tests/, README.md, CONTEXT.md, docs/product/, docs/design/
- **关联文档**: [训练体验产品方案](product/training-experience.md), [数据源同步产品方案](product/data-source-sync.md), [训练系统技术设计](design/training-system.md), [多平台数据合同](design/12-multi-platform.md), [记忆系统设计](design/memory-system.md), [AI 教练设计](design/ai-coaching.md), [开发过程](process/2026-07-31-training-session-analysis.md), [README](../README.md)

### Fixed
- **ECS 旧预期 SHA 不再阻断平台 checkout**: 发布脚本移除 `EXPECTED_COMMIT` 强制门禁，始终以阿里云发布任务已 checkout 的干净 `HEAD` 为候选版本；旧控制台入口即使仍传入错误 SHA 也不再拦截，完整 SHA、并发锁、候选构建、健康检查和回滚保护保持不变。
- **影响范围**: scripts/deploy-ecs.sh, tests/test_packaging.py, README.md, docs/design/13-sae-deployment.md
- **关联文档**: [Bug 记录](bugfixes/2026-07-31-ecs-legacy-expected-commit-blocks-checkout.md), [ECS 部署设计](design/13-sae-deployment.md), [README](../README.md)
- **ECS 平台选定的新提交被固定分支拒绝**: 控制台入口不再额外固定或刷新 `feature/huawei`，直接把发布平台已 checkout 的干净 `HEAD` 作为候选 Commit；完整 SHA、并发锁与发布后版本健康检查保持不变。
- **影响范围**: README.md, docs/design/13-sae-deployment.md, tests/test_packaging.py
- **关联文档**: [Bug 记录](bugfixes/2026-07-31-ecs-fixed-deploy-ref-rejects-selected-commit.md), [ECS 部署设计](design/13-sae-deployment.md), [README](../README.md)
- **空活动不再误判为休息日**: 日报先读取逐日活动同步覆盖，再输出 `training`、`confirmed_rest`、`unknown` 三态；只有活动同步完成且确无记录时才确认休息，其余空活动日在 Web、CLI、HTML、AI 教练和 MCP 中显示运动数据未同步或状态未知。
- **影响范围**: src/training_analysis.py, src/memory.py, src/coach.py, src/main.py, src/web.py, src/render.py, src/mcp_server.py, tests/, README.md, CONTEXT.md, docs/product/, docs/design/
- **关联文档**: [Bug 记录](bugfixes/2026-07-31-empty-activity-misclassified-as-rest.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [记忆系统设计](design/memory-system.md)
- **周目标改为自然周统计**: 新增共享自然周边界和专用周进度工具，AI 教练与 MCP 只累计报告日期所在周一至周日内的实际训练；滚动 7 天继续仅用于负荷和恢复趋势，未知活动日不计作训练或休息。
- **影响范围**: src/training_analysis.py, src/coach.py, src/mcp_server.py, src/storage.py, prompts/coach.md, tests/, README.md, CONTEXT.md, docs/product/, docs/design/
- **关联文档**: [Bug 记录](bugfixes/2026-07-31-week-goal-rolling-window.md), [训练体验产品方案](product/training-experience.md), [训练系统技术设计](design/training-system.md), [AI 教练设计](design/ai-coaching.md)
- **日报训练史、爬升和负荷口径不完整**: 教练历史工具改为直接读取 SQLite 原始活动和健康数据，不再跳过未生成日报的日期；同步持久化累计爬升并补齐常见 Provider 分段详情，缺失爬升保留为 `null`；ACWR 改为近 7 天累计负荷除以近 28 天周均负荷，同时修复持久化字段名与 HRV 状态大小写问题。
- **影响范围**: src/main.py, src/activity.py, src/memory.py, src/coach.py, src/providers/, src/storage.py, tests/
- **关联文档**: [Bug 记录](bugfixes/2026-07-31-training-analysis-data-gaps.md), [数据源同步产品方案](product/data-source-sync.md), [训练系统技术设计](design/training-system.md), [开发过程](process/2026-07-31-training-session-analysis.md)
- **ECS 旧 Git checkout 被误报为成功发布**: 发布入口刷新目标分支并显式传入期望 Commit；发布脚本
  校验干净 Git 工作树与完整 SHA，使用全局锁拒绝并发发布，并让 release 标记、systemd 运行环境和
  `/healthz` 共同返回同一 Commit。旧暂存代码、错误源码目录或其他进程的存活响应不再满足成功条件。
- **影响范围**: scripts/deploy-ecs.sh, src/web.py, tests/test_packaging.py, tests/test_web.py, README.md, docs/design/
- **关联文档**: [ECS 部署设计](design/13-sae-deployment.md), [Bug 记录](bugfixes/2026-07-31-ecs-stale-checkout-release.md), [README](../README.md)
- **Coros 自动鉴权默认勾选状态显示不清晰**: 首次绑定、运动重新授权和睡眠重新授权表单改用
  独立复选框样式，避免继承账号输入框的内边距、背景和边框而遮住勾选标记；重新授权页面同时移除
  重复 Provider 初始化，单次进入只请求一次安全存储能力。
- **影响范围**: web/templates/setup.html, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [多平台设计](design/12-multi-platform.md), [Bug 记录](bugfixes/2026-07-31-coros-auto-refresh-checkbox-visibility.md), [README](../README.md)

## 2026-07-30

### Fixed
- **Coros 首次绑定无法勾选自动鉴权**: 新增只要求应用登录态的绑定能力查询接口，首次绑定用户无需
  active 数据源即可读取服务端安全加密能力；页面不再通过受绑定状态约束的个人资料接口初始化
  复选框。配置密钥时默认勾选且可取消，缺少密钥时才禁用。
- **影响范围**: src/web.py, web/templates/setup.html, tests/test_registration.py, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [多平台设计](design/12-multi-platform.md), [Bug 记录](bugfixes/2026-07-30-coros-auto-refresh-capability-gate.md), [README](../README.md)

## 2026-07-29

### Added
- **联系我们与内测交流群入口**: 登录后的所有 Web 页面新增统一浮动联系按钮；桌面支持 hover/focus 预览与点击锁定，移动端支持点击、长按或保存二维码，包含外部点击、关闭按钮、Escape、焦点返回和图片不可用状态。群二维码端点受应用会话保护，优先读取持久化覆盖文件并禁用缓存，后续换图无需修改页面代码。
- **影响范围**: src/web.py, web/assets/contact-wechat.jpg, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [联系入口产品方案](product/contact-community.md), [联系入口技术设计](design/contact-community.md), [README](../README.md)
- **Web 异步同步任务与实时阶段进度**: `POST /api/sync` 在任务持久化和调度接纳后立即返回 202 与 `task_id`；同步页通过用户隔离的轮询接口展示排队、认证、指标、活动、备份和终态，支持刷新恢复、网络退避、同用户任务找回及服务重启后的 `interrupted`。任务仍复用 4/100 受控并发，不引入 Redis/Celery，CLI/MCP 合同不变。
- **影响范围**: src/sync_tasks.py, src/sync_coordinator.py, src/main.py, src/web.py, src/users.py, web/templates/sync.html, tests/test_sync_tasks.py, tests/test_sync_coordinator.py, tests/test_main.py, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-29-web-async-sync-tasks.md), [README](../README.md)

### Changed
- **Coros 运动认证默认区域改为中国大陆**: 首次认证页面默认选中中国大陆，兼容绑定接口和独立
  Training Hub 认证接口在未传区域时也统一使用 `cn`；用户显式选择其他区域及重新授权继承区域的
  行为保持不变。
- **影响范围**: src/web.py, web/templates/setup.html, tests/test_registration.py, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)
- **Coros 运动与睡眠分域认证**: 将 Training Hub 运动数据与 Mobile 睡眠数据调整为
  独立按钮、独立账号说明、独立授权接口和独立刷新状态；安全加密可用时两个自动鉴权选项默认开启，
  缺少密钥时禁用并解释。Mobile 重放载荷从 token JSON 迁入独立 Fernet 密文，`1019` 只刷新
  Mobile Token，不写 coros-mcp 全局认证文件。
- **影响范围**: src/providers/coros_credentials.py, src/providers/coros.py, src/web.py, web/templates/setup.html, web/templates/profile.html, tests/test_coros_credentials.py, tests/test_providers.py, tests/test_registration.py, tests/test_web.py, README.md, docs/product/, docs/design/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [多平台设计](design/12-multi-platform.md), [Mobile Bug 记录](bugfixes/2026-07-29-coros-mobile-auth-failure-hidden.md), [开发过程](process/2026-07-29-coros-training-token-auto-relogin.md), [README](../README.md)
- **Coros 同步增加真实阶段内进度**: 活动同步按 Training Hub 分页已返回记录数推进，健康同步按已聚合日期推进，并复用 `progress.items` 持久化、轮询和刷新恢复；第三方请求未返回时不按耗时伪造百分比，Garmin 国际区和中国区行为不变。
- **影响范围**: src/providers/coros.py, src/main.py, tests/test_providers.py, tests/test_main.py, README.md, docs/product/, docs/design/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-29-web-async-sync-tasks.md), [README](../README.md)

### Fixed
- **Coros Training Hub 短期 Token 到期后反复要求重新输入密码**: 安全存储可用时默认开启加密自动鉴权；服务端只保存 Fernet 加密的密码等价重放凭据，`result=1019` 后按用户和认证域 singleflight 自动重登并仅重试一次。用户可随时分域删除密文；凭据被拒绝时只删除对应域，临时上游错误保留 `active`。
- **影响范围**: src/providers/coros_credentials.py, src/providers/coros.py, src/config.py, src/web.py, web/templates/setup.html, web/templates/profile.html, pyproject.toml, docker-compose.yml, .env.example, tests/test_coros_credentials.py, tests/test_config.py, tests/test_registration.py, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-coros-training-token-auto-relogin.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [多平台设计](design/12-multi-platform.md), [部署设计](design/13-sae-deployment.md), [依赖清单](design/07-dependencies.md), [开发过程](process/2026-07-29-coros-training-token-auto-relogin.md), [README](../README.md)
- **Coros Mobile 授权失败被误显示为重新授权成功**: Training Hub 与 Mobile 改为两个独立认证入口；睡眠页只接受 Coros App 邮箱并继承运动区域，失败时返回脱敏原因并停留在对应表单，活动 token 继续可用。
- **影响范围**: src/providers/coros.py, src/providers/coros_credentials.py, src/web.py, web/templates/setup.html, web/templates/profile.html, tests/test_coros_credentials.py, tests/test_providers.py, tests/test_registration.py, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-coros-mobile-auth-failure-hidden.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)
- **Garmin 批量同步进度长时间停在 2/4**: 接入 garmy 逐日期/逐指标完成、跳过和失败事件，在四阶段总进度下持久化真实的已处理项数、总项数、日期和指标；同步页使用独立细进度条展示并在刷新后恢复，不再因长阶段缺少更新时间而误判假死。
- **影响范围**: src/storage.py, src/main.py, src/sync_tasks.py, src/web.py, web/templates/sync.html, tests/test_storage.py, tests/test_main.py, tests/test_sync_tasks.py, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-sync-progress-stale-after-refresh.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [Web 部署设计](design/13-sae-deployment.md), [README](../README.md)
- **Garmin Access Token 到期反复要求输入密码**: 同步恢复 Token 后先判断 `needs_refresh` 并自动换取、持久化新 OAuth2 Token，不再把约一天到期的 Access Token 直接回退为空密码 SSO 登录；profile、刷新和 Provider 初始化保留原始临时异常，只有明确 401/403 或不可再刷新的凭据才把连接标记为 `expired`，其他错误保持 `active` 并允许重试。
- **影响范围**: src/auth.py, src/providers/garmin.py, src/main.py, src/web.py, tests/test_auth.py, tests/test_providers.py, tests/test_main.py, tests/test_web.py
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-garmin-token-refresh-rebind-loop.md), [模块设计](design/04-modules.md), [数据流](design/05-data-flow.md), [多平台设计](design/12-multi-platform.md), [README](../README.md)
- **ECS Web 同步阻塞与文件描述符耗尽**: 阻塞式平台同步和 SQLite 写入移入受控工作线程，单进程默认同时执行 4 个、接纳 100 个不同用户；同用户 singleflight 与容量超限分别返回 409/503。同步、MFA 和临时 SQLite/HTTP 客户端显式释放资源，systemd 服务增加 `LimitNOFILE=8192` 防线。
- **影响范围**: src/sync_coordinator.py, src/resource_lifecycle.py, src/web.py, src/main.py, src/storage.py, src/auth.py, src/config.py, scripts/deploy-ecs.sh, tests/
- **关联文档**: [数据源同步产品方案](product/data-source-sync.md), [Bug 记录](bugfixes/2026-07-29-ecs-sync-event-loop-fd-exhaustion.md), [Web 部署设计](design/13-sae-deployment.md), [开发过程](process/2026-07-29-web-sync-capacity.md), [README](../README.md)
- **Coros 睡眠重新授权被重置为 Garmin**: 重新授权页面的最终 Provider 初始化改为由 `rebind=coros` 与 `scope` 决定，不再被脚本末尾的 Garmin 默认值覆盖；页面固定提交当前 Coros 认证域，运动页显示邮箱或手机号及区域，睡眠页只显示 Coros App 邮箱。
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
