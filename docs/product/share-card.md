# 产品方案 — 分享卡

> 版本: v1.7 · 日期: 2026-08-24
> 状态: Canvas 与结构化 AI 分享摘要已实现，待真实浏览器 E2E 验收
> 配套技术设计: [分享卡浏览器 Canvas 生成](../design/share-card.md)

> 视觉规范: 分享卡采用“跑步日志页”视觉。页眉标识报告周期，居中距离为主信息，三项训练指标使用分栏记录；一条低饱和虚线路线连接起点与终点。AI 教练区把训练观察作为紧凑证据行，把结论作为独立、较大的收束句；三主题颜色、系统字体栈、10px 工具标签和 10px 圆角与整站设计 token 对齐。视觉服务于跑步记录的可读性，不使用页面截图、渐变背景或装饰性插图。

## 1. 业务目标与范围

- **核心价值**: 用户在不上传健康隐私数据、不依赖服务端浏览器运行时的前提下，从日报或周复盘一键生成可分享的训练 PNG。
- **成功指标**:
  1. 有可分享跑步数据时，受支持浏览器生成的 PNG 成功率达到 99%，导出宽度固定为 1125 px，且任何成功图片均不截断底部水印。
  2. 点击分享后 2 秒内展示本地预览（P95，基准为近两年发布的主流手机，数据已在页面加载完成且不计系统分享面板响应时间）。
  3. 分享动作发起后不再请求服务端分享卡图片路由，训练图片不上传 neurun 或第三方服务。
  4. 自动化隐私字段测试中，睡眠、HRV、静息心率、身体电量、恢复评分、训练准备度、ACWR、风险、警告、异常提醒和训练建议的泄漏率为 0%。
- **In-Scope**:
  1. 日报详情和周复盘归档中的分享入口。
  2. 基于页面已加载报告 JSON 构造白名单数据，并用浏览器 Canvas 2D 专门绘制分享卡；不截取页面 DOM。
  3. 日报分享卡展示当日跑步事实，以及日报生成时由 AI 基于完整训练事实提炼的结构化分享摘要；周复盘分享卡展示自然周跑步汇总，以及概览、质量课、近期变化和结论。
  4. Canvas 生成 PNG、本地预览、支持文件分享时调用系统分享面板、不支持时下载 PNG。
  5. `fresh`、`sport`、`dark` 三种主题跟随当前 Web 主题。
  6. 保持 `GET /api/dashboard` 与 `GET /api/reports/weekly` 的 URL、鉴权和既有 JSON 响应合同不变。
  7. 旧分享卡图片 URL 保留过渡响应，明确告知调用方图片已改为浏览器内生成。
  8. 生产依赖和运行链路不安装、不启动、也不调用 Playwright、Chromium 或系统 Chrome；Web Canvas 是唯一 PNG 生成能力。
  9. MCP 和 CLI 不提供分享卡或完整日报 PNG 生成命令/工具，仅保留报告数据和 HTML 能力。
- **Out-of-Scope**:
  1. 页面 DOM、整页报告或隐藏节点截图。
  2. 服务端生成、缓存、存储或转发分享卡图片。
  3. 改变日报和周复盘数据接口的字段、类型、状态码或鉴权规则。
  4. CLI/MCP 的报告数据与 HTML 生成能力；本次只移除其中的 PNG 输出，不改变报告事实或 HTML 输出合同。
  5. 回流链接、邀请码二维码、落地页、注册转化追踪和社交平台 API 直发。
  6. 用户头像、昵称、社交身份、自由文案、裁剪或模块自定义。
  7. 无跑步活动的休息日分享卡。

## 2. 用户故事与验收标准 (Acceptance Criteria)

### US-01: 生成日报分享卡
- **As a**: 已登录且已绑定数据源的跑者
- **I want**: 在已生成日报中预览并导出当日跑步分享卡
- **So that**: 我可以分享训练成果而不暴露恢复和健康隐私
- **Gherkin 验收条件**:
  - Given 日报 JSON 已加载且包含至少一条跑步活动
  - When 用户点击“分享卡”
  - Then 浏览器只从白名单字段构造卡片数据并用 Canvas 2D 生成预览，不请求 `/api/reports/share-card/daily`
  - And 主活动为距离最长的跑步活动，其余跑步活动按距离降序全部列入“当日其他训练”
  - And 卡片展示训练类型、距离、时长和配速；有步频时展示步频，无步频时指标列从 3 列变为 2 列
  - And L1 数据展示强度分布，L0 数据不展示强度分布
  - And 优先读取 `ai_insight.share_card` 中由 AI 基于完整训练事实生成的 `headline`、逐次 `sessions`、`takeaway` 和 `conclusion`，不依赖中文观察前缀
  - And `share_card.sessions` 最多展示 4 条，每次跑步最多 1 条，每条摘要最多 70 个中文字符，其他摘要文本最多 90 个字符
  - And `ai_insight.share_card` 缺失时，兼容读取旧日报的安全运动观察；旧观察中的 `第N次跑步` 前缀必须可以被识别
  - And 有安全 `share_card.conclusion` 或 `ai_insight.conclusion` 时允许展示教练结论，无安全摘要且无结论时省略 AI 教练区块
  - And `ai_insight.warnings` 与 `ai_insight.recommendations` 始终不读取
  - Given 同一日报的 AI 观察同时包含允许类别和恢复、HRV、ACWR、风险、警告、异常提醒或训练建议
  - When 浏览器构造日报分享卡
  - Then 只展示允许类别中的训练观察和结论，禁止内容及其原始文本均不进入分享卡数据对象或 PNG

### US-02: 生成周复盘分享卡
- **As a**: 已登录且已绑定数据源的跑者
- **I want**: 从已归档周复盘生成本周训练分享卡
- **So that**: 我可以分享一周跑量和质量课亮点
- **Gherkin 验收条件**:
  - Given 周复盘 JSON 已加载且 `actual_summary.running_distance_km` 大于 0
  - When 用户点击“分享本周”
  - Then 浏览器使用该归档周复盘的既有 JSON 在本地生成预览，不重新生成周复盘也不请求 `/api/reports/share-card/weekly`
  - And 卡片展示周范围、跑量、最长单次、平均配速和跑步天数
  - And 有质量课时逐课展示，零质量课时展示“本周以有氧跑为主，未检测到满足证据门槛的质量课”
  - And 有可比较历史周时展示趋势，无参照周时省略趋势区块
  - And 只有 `review_sections.overview`、`review_sections.quality_sessions` 和 `review_sections.trend` 中的概览、质量课和近期变化允许进入 AI 教练区块，其他周复盘观察类别不进入分享卡
  - And 有 `finding.conclusion` 时允许展示 AI 教练结论，无安全观察且无结论时省略 AI 教练区块
  - And `review_sections.recovery_and_risk`、行动建议及警告来源始终不读取
  - Given 同一周复盘同时包含概览、质量课、近期变化以及恢复、HRV、ACWR、风险、警告、异常提醒或训练建议
  - When 浏览器构造周复盘分享卡
  - Then 只展示概览、质量课、近期变化和结论，禁止内容及其原始文本均不进入分享卡数据对象或 PNG

### US-03: 分享或下载 PNG
- **As a**: 已看到分享卡预览的跑者
- **I want**: 通过系统分享面板发送图片，或将图片下载到本地
- **So that**: 我能在不同浏览器中完成可预测的导出
- **Gherkin 验收条件**:
  - Given Canvas 已成功导出 `image/png` Blob
  - When 浏览器同时支持 `navigator.share` 和文件分享
  - Then 用户操作触发系统分享面板，文件名分别为 `neurun-daily-YYYY-MM-DD.png` 或 `neurun-weekly-YYYY-Www.png`
  - Given 浏览器不支持文件分享或调用返回能力不支持错误
  - When 用户执行分享
  - Then 页面在同一次操作中回退为下载 PNG，并显示“当前浏览器不支持直接分享，图片已下载”
  - Given 用户主动取消系统分享面板
  - When 浏览器返回取消结果
  - Then 预览保持可用，不自动下载，并显示“已取消分享”

### US-04: 隐私最小化
- **As a**: 关注健康数据隐私的跑者
- **I want**: 分享图片只包含训练成果白名单
- **So that**: 分享时不会意外暴露恢复和健康状态
- **Gherkin 验收条件**:
  - Given 报告 JSON 同时包含允许的训练观察，以及睡眠、HRV、恢复、风险、警告、异常提醒和建议数据
  - When 浏览器构造分享卡数据并绘制 PNG
  - Then 分享卡数据对象和绘制调用中均不存在睡眠、HRV、静息心率、身体电量、恢复评分、训练准备度、ACWR、风险标记、警告、异常提醒、方案执行状态或训练建议
  - And 日报 AI 文本优先来自结构化 `share_card` 摘要；旧日报回退时仅来自运动概要、强度分布、跑步动力学、跑步分析和结论，周复盘 AI 文本仅来自概览、质量课、近期变化和结论
  - And 即使文本来自允许来源，只要单条文本包含“睡眠”“恢复”“HRV”“ACWR”“风险”“警告”“异常”或“建议”（`HRV`、`ACWR` 不区分大小写），该条文本必须整体剔除，不得部分截取后展示
  - And PNG Blob 只存在当前浏览器内，不上传服务器，不写入服务端临时文件
  - And 预览关闭或被替换后释放对应 Object URL

### US-05: 保持报告数据接口稳定
- **As a**: Web 报告页面或既有报告数据接口调用方
- **I want**: 分享卡实现迁移不改变报告读取合同
- **So that**: 日报和周复盘的其他功能不会回归
- **Gherkin 验收条件**:
  - Given 调用方使用既有 Cookie 鉴权访问 `GET /api/dashboard` 或 `GET /api/reports/weekly`
  - When 浏览器 Canvas 分享卡能力上线
  - Then 两个接口的 URL、方法、鉴权、既有成功与错误状态码、既有 JSON 字段名称和字段类型保持不变
  - And 本次实现不要求两个接口新增分享卡专用字段

### US-06: 迁移旧分享卡图片 URL
- **As a**: 仍持有旧分享卡图片 URL 的调用方
- **I want**: 收到明确且机器可读的迁移响应
- **So that**: 我不会把 JSON 错误误当成损坏的 PNG
- **Gherkin 验收条件**:
  - Given 已登录且已绑定数据源的调用方访问 `GET /api/reports/share-card/daily` 或 `GET /api/reports/share-card/weekly`
  - When 浏览器 Canvas 方案上线
  - Then 服务端返回 `410 Gone` 和 `application/json`，错误码为 `share_card_client_rendering_required`
  - And 响应提示用户前往日报或报告页面使用浏览器内分享入口，不返回空图片、重定向或伪造的 `image/png`
  - Given 未登录或未绑定数据源的调用方访问旧 URL
  - When 服务端执行现有鉴权门禁
  - Then 仍返回既有 `401` JSON，不泄露报告是否存在

### US-07: 失败与重试
- **As a**: 遇到浏览器资源不足或图片编码失败的跑者
- **I want**: 保留当前报告并获得可重试的错误状态
- **So that**: 失败不会触发服务端兜底或产生残缺图片
- **Gherkin 验收条件**:
  - Given 报告没有跑步活动
  - When 用户点击分享入口
  - Then 不创建 Canvas 或 PNG，并显示“该日报无可分享的跑步活动”或“该周无跑步活动，不生成分享卡”
  - Given 计算后的逻辑高度超过 2048 px、Canvas 上下文创建失败或 PNG Blob 编码失败
  - When 浏览器尝试生成卡片
  - Then 状态进入“生成失败”，不输出截断图片，显示“分享卡生成失败，请重试”，并保留重试入口
  - Given 用户重试
  - When 报告数据仍在页面内
  - Then 复用现有报告数据重新生成，不刷新页面、不重新调用报告 API

## 3. 状态机与边界条件 (Edge Cases)

- **状态流转**: [不可用] -> (加载到含跑步活动的报告 JSON) -> [可生成] -> (点击分享) -> [生成中] -> (Canvas 绘制及 PNG 编码成功) -> [预览就绪] -> (系统分享或下载成功) -> [已完成]
- **失败流转**: [生成中] -> (无数据、超出高度上限、Canvas 或编码失败) -> [生成失败] -> (重试) -> [生成中]
- **分享降级流转**: [预览就绪] -> (文件分享能力不支持) -> [下载中] -> (下载触发) -> [已完成]
- **异常处理规则**:
  1. 未登录或数据源未绑定: 报告数据接口按既有规则返回 `401`，页面不得尝试生成分享卡；提示文案“请先绑定数据源”。
  2. 日报不存在或周复盘未归档: 分享入口不可用；不得为分享动作隐式生成报告。
  3. 无跑步活动: 不生成空卡或休息日卡，使用对应的明确提示文案。
  4. 多活动日: 只保留跑步活动；按距离降序，最长为主活动，其余全部展示。
  5. 缺失可选指标: 省略对应指标或区块，不显示 `null`、`undefined`、`NaN`、`0'00\"/km` 等占位错误。
  6. 文本过长: AI 摘要在 CoachInsight Schema 阶段限制长度；Canvas 对合规摘要按实测宽度完整换行，不因固定行数添加省略号。对绕过 Schema 的异常或历史字段保留前端防御性长度限制；活动名称等事实标签最多 1 行，超出部分以省略号结束。
  7. 内容过高: 逻辑高度最大 2048 px；超过时整体失败且不导出部分图片。
  8. 重复点击: “生成中”期间忽略后续点击，只允许一个生成任务；完成、失败或取消后恢复入口。
  9. 主题切换: 每次生成读取点击时的当前主题；已打开的预览不随主题切换重绘，下次生成使用新主题。
  10. 系统分享取消: 保留预览，不视为故障，不自动下载。
  11. Object URL 生命周期: 新预览替换旧预览或关闭弹窗时立即释放；页面卸载时释放剩余 URL。
  12. 旧图片路由: 登录用户固定返回 `410` JSON；不得按日期、主题或报告存在性继续渲染图片。
  13. AI 观察为空: 保留训练事实卡；日报四类安全观察或周报三类安全观察全部缺失时，只在有安全结论时展示 AI 教练区块。
  14. AI 观察混合: 结构化分享摘要优先；无结构化摘要时再应用旧来源白名单。所有候选文本都必须执行禁止词过滤；包含“睡眠”“恢复”“HRV”“ACWR”“风险”“警告”“异常”或“建议”的整条文本不展示，禁止规则优先级高于来源白名单。
  15. AI 分享摘要: 日报生成时一次教练调用同时产出完整 `CoachInsight` 和 `share_card`；`share_card` 只描述训练成果，不包含恢复、健康风险或训练建议。模型漏传该字段时按空摘要兼容，前端可使用旧日报安全回退。

## 4. 数据实体草案

- **ShareCardRequest**: { card_type: Enum(daily/weekly), Required: Yes; reference_date: String(YYYY-MM-DD), Required: Yes; theme: Enum(fresh/sport/dark), Required: Yes }
- **ShareCardActivity**: { label: String, Required: Yes; distance_km: Number, Required: Yes; duration_seconds: Int, Required: Yes; average_pace_seconds_per_km: Int, Required: No; average_cadence_spm: Int, Required: No; intensity_distribution: Object<String, Number>, Required: No }
- **DailyShareCardData**: { kind: Enum(daily), Required: Yes; report_date: String(YYYY-MM-DD), Required: Yes; primary_activity: ShareCardActivity, Required: Yes; additional_activities: Array<ShareCardActivity>, Required: Yes; coach_summary: Object(headline/sessions/takeaway/conclusion), Required: No; coach_observations: Array<String (旧日报兼容回退)>, Required: No; ai_conclusion: String, Required: No; theme: Enum(fresh/sport/dark), Required: Yes }
- **WeeklyQualitySession**: { date: String(YYYY-MM-DD), Required: Yes; label: String, Required: Yes; distance_km: Number, Required: No; average_pace_seconds_per_km: Int, Required: No; aerobic_effect: Number, Required: No; anaerobic_effect: Number, Required: No }
- **WeeklyShareCardData**: { kind: Enum(weekly), Required: Yes; week_id: String(YYYY-Www), Required: Yes; week_start: String(YYYY-MM-DD), Required: Yes; week_end: String(YYYY-MM-DD), Required: Yes; running_distance_km: Number, Required: Yes; longest_run_km: Number, Required: No; average_pace_seconds_per_km: Int, Required: No; running_days: Int, Required: Yes; trend: Object, Required: No; quality_sessions: Array<WeeklyQualitySession>, Required: Yes; coach_observations: Array<String (概览/质量课/近期变化)>, Required: No; ai_conclusion: String, Required: No; theme: Enum(fresh/sport/dark), Required: Yes }
- **ShareCardResult**: { state: Enum(preview_ready/shared/downloaded/cancelled/failed), Required: Yes; blob: Blob(image/png), Required: No; filename: String, Required: No; error_code: Enum(no_running_activity/canvas_unavailable/content_too_tall/png_encode_failed/share_failed), Required: No }
