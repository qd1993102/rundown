# 设计方案 — 5. 数据流

> 属于 [设计方案索引](../design.md) · 版本 v3.6 · 2026-07-29

---

## 5. 数据流

### 5.1 整体数据流

```mermaid
graph LR
    A["📋 读取<br/>环境变量"] --> B["🔐 创建<br/>AuthClient"]
    B --> C["✅ 检查并刷新<br/>Token"]
    C --> D["📡 创建<br/>APIClient"]
    D --> E["📥 按日期<br/>/类型拉取"]
    E --> F["🔄 SyncManager<br/>调度 + 去重"]
    F --> G[("💾 SQLite<br/>garmin_data.db")]

    G --> H["🖥️ CLI 查询<br/>终端展示"]
    G --> I["📄 CSV 导出<br/>文件输出"]
    G --> J["🧠 MemoryStore<br/>聚合生成"]
    J --> K["📝 memory/<br/>Markdown<br/>+ YAML FM"]

    K --> L["🖥️ CLI 查询<br/>memory 子命令"]
    K --> M["📖 直接阅读<br/>编辑器编辑<br/>Git diff"]
    K --> N["🤖 MCP Server"]
    N --> O["💬 OpenClaw<br/>AI 教练对话<br/>晨间简报 / 训练规划<br/>状态查询 / 赛后分析"]
    O -.->|"AI 洞察写入"| K

    A -.->|".env 文件<br/>或系统环境"| A
    C -.->|"用户目录<br/>Token 文件"| C
```

### 5.2 sync 命令执行流程

```mermaid
graph TD
    SYNC["sync 命令"]
    S1["1. config.py<br/>加载并校验环境变量"]
    S2["2. auth.py / GarminAuth<br/>恢复 Token，必要时自动刷新"]
    S3["3. fetcher.py<br/>创建 APIClient，初始化 SyncManager"]
    S4["4. storage.py<br/>SyncManager.sync_range()"]
    S4A["按日期遍历"]
    S4B["检查 sync_status 去重"]
    S4C["拉取指标 → 写入 SQLite"]
    S4D["更新 sync_status"]
    S5["5. memory.py<br/>[若未指定 --no-memory]"]
    S5_DAILY["⭐ generate_daily()<br/>查询昨日活动 + 昨夜睡眠<br/>+ 今晨状态 + 7日趋势<br/>异常检测 + 训练建议<br/>写入 auto/daily/YYYY-MM-DD.md"]
    S5A["generate_summaries()<br/>查询 SQLite → 聚合统计<br/>检测 PR / 趋势对比<br/>写入 auto/summaries/"]
    S5B["generate_recovery()<br/>查询健康指标 → 计算恢复评分<br/>写入 auto/recovery/"]
    S5C["update_execution()<br/>检查活跃训练计划<br/>对比计划 vs 实际<br/>更新 auto/execution/"]
    S5D["rebuild_index()<br/>重建各子目录 index.md"]

    SYNC --> S1 --> S2 --> S3 --> S4
    S4 --> S4A --> S4B --> S4C --> S4D
    S4 --> S5
    S5 --> S5_DAILY
    S5 --> S5A
    S5 --> S5B
    S5 --> S5C
    S5 --> S5D
```

### 5.3 daily 命令执行流程 ⭐

```mermaid
graph TD
    D1["neurun daily"]
    D2["1. 确定 target_date<br/>(默认今天, --date 可指定)"]
    D3["2. 连接 SQLite (只读)"]
    D4["3. 查询昨日活动<br/>db.get_activities(yesterday)"]
    D5["4. 查询昨夜睡眠<br/>db.get_health_metrics(today) → sleep"]
    D6["5. 查询今晨指标<br/>resting_hr, hrv, body_battery, readiness"]
    D7["6. 计算训练负荷<br/>acute/chronic load, ACWR"]
    D8["7. 7日趋势分析<br/>各指标 slopes + direction"]
    D9["8. 异常检测<br/>规则引擎扫描"]
    D10["9. 生成训练建议<br/>结合偏好 + 目标 + 状态"]
    D11["10. 渲染 + 写入<br/>auto/daily/YYYY-MM-DD.md"]
    D12["11. 终端 rich 输出<br/>日报摘要面板"]

    D1 --> D2 --> D3 --> D4 --> D5 --> D6 --> D7 --> D8 --> D9 --> D10 --> D11 --> D12
```

### 5.4 memory 子命令执行流程

```mermaid
graph TD
    subgraph SUMMARIZE["memory summarize"]
        MS1["1. 确定 period 和 target_date"]
        MS2["2. 连接 SQLite (只读)"]
        MS3["3. 查询 activities + health_metrics"]
        MS4["4. 执行聚合算法"]
        MS5["5. 渲染 Markdown + YAML Front Matter"]
        MS6["6. 写入 auto/summaries/ + auto/recovery/"]
        MS7["7. 更新 index.md"]
        MS1 --> MS2 --> MS3 --> MS4 --> MS5 --> MS6 --> MS7
    end

    subgraph GOAL_CREATE["memory goal create"]
        GC1["1. 交互式问答"]
        GC2["2. 构建 Front Matter + Markdown 正文"]
        GC3["3. Schema 校验 (Level 1)"]
        GC4["4. 写入 goals/active/{slug}.md"]
        GC5["5. 更新 index.md"]
        GC1 --> GC2 --> GC3 --> GC4 --> GC5
    end

    subgraph CHECK["memory check"]
        CK1["Level 1: Schema 校验"]
        CK2["Level 2: 交叉校验"]
        CK3["Level 3: 完整性检查"]
        CK4["输出检查报告"]
        CK1 --> CK2 --> CK3 --> CK4
    end
```

---

### 5.5 Web 邀请注册与数据源绑定流程

```mermaid
sequenceDiagram
    actor User as 用户
    participant Web as 浏览器
    participant API as web.py
    participant Invite as InvitationStore
    participant Users as UserManager
    participant Provider as 运动平台

    User->>Web: 打开首页（无会话）
    Web->>API: GET /
    API-->>Web: 302 /login
    User->>API: POST /api/invitations/validate
    API->>Invite: validate(invite_code)
    Invite-->>API: 当前可用
    User->>API: POST /api/register（昵称/邮箱/密码/邀请码）
    API->>Invite: 再次 validate
    API->>Users: register_account
    Users->>Users: 邮箱唯一性 + scrypt 哈希 + 用户 JSON
    API->>Invite: consume（写入 used_by / used_at）
    API-->>Web: 201 + HttpOnly Cookie + next=/setup
    User->>API: POST /api/setup（运动平台凭证）
    API->>Users: 校验 Cookie 对应用户
    API->>Provider: Garmin/Coros/Huawei 认证
    Provider-->>API: Token / MFA 状态
    API->>Provider: 按用户目录持久化 Token/连接凭证
    API->>Users: 更新 provider 与 token_status
    API-->>Web: 绑定成功
    Web-->>User: 展示默认收起的个性化资料（选填）
    alt 用户主动填写
        User->>API: POST /api/profile、/api/goals、/api/preferences
        API-->>User: 保存后可进入首页
    else 用户跳过
        Web-->>User: 直接进入首页，之后可在“我的”补填
    end
```

注册认证和运动平台认证是两层独立边界：`/api/setup` 不得为无会话请求自动创建用户；
应用退出只删除 Cookie，不清理运动平台 Token。再次登录后仍使用原 API Key 和用户数据目录。
身体信息、个人最佳、训练目标和偏好不属于账号创建或平台绑定的前置条件；首次设置页面必须默认
收起这些复杂表单并提供显式跳过入口，跳过时不得写入空档案或默认偏好。

同步请求重新创建 Provider 时，必须先从用户目录恢复并执行 `authenticate()`，再读取平台
`user_id`。Garmin Access Token 到期而 Refresh Token 仍有效时，认证阶段调用
`refresh_tokens()` 并持久化新 OAuth2 Token，不进入账号密码 SSO。只有刷新凭据已过期、被撤销
或平台明确返回 401/403 时，才在本地 SQLite 写入之前终止并把连接状态更新为 `expired`；
超时、429、5xx、响应解析或 profile 临时异常保持连接为 `active`，任务以可重试错误结束。
任何失败路径都禁止用 `user_id=0` 或服务级默认凭证继续同步。

Coros Training Hub 没有平台 Refresh Token。服务端支持安全加密时，运动认证页默认开启自动鉴权，Web 将
密码等价重放对象加密保存到当前用户 Token 目录；明文密码和摘要不进入用户 JSON、日志或 API。
活动、每日分析或 HRV 请求明确返回 `result=1019` 时，Provider 在单用户锁中重新读取 Token，避免
并发重复登录；仍是旧 Token 才解密重放对象换取新 Access Token 并重试一次。重登被明确拒绝时
删除密文并让 Web 标记 `expired`；临时上游失败不改变连接状态。Mobile 睡眠认证使用独立表单，
把上游生成的重放载荷加密保存到 `coros-mobile-relogin.enc`；Mobile `1019` 时只更新
`mobile_access_token`，不得复用 Training Hub 重登对象、写入 coros-mcp 全局认证文件，或因
Training Hub 重登成功而伪装为可用。

认证完成且范围确定后，核心同步函数先将范围内每天的 `neurun_provider_sync` 标记为
`pending`。全部 Provider 拉取与 SQLite 写入成功后统一更新为 `completed`；异常退出时
统一更新为 `failed` 并保留截断后的错误信息。Web 日历 API 只聚合这些状态和本地表，
不为展示日历触发 Provider 认证或远程请求。

三平台写入活动时同时保存标准化 `activity_type`。日历聚合先判断当天是否存在跑步记录；
存在时直接输出 `synced`，优先于范围标记和指标级状态。升级前的历史活动没有该字段时，
只在名称包含“跑步”或 `run` 时作兼容识别；非跑步活动和健康数据仍按原状态证据聚合。

Web 异步任务在核心同步外增加一层任务状态流，不改变上述 Provider 与日历写入顺序：

```mermaid
sequenceDiagram
    actor User as 用户
    participant Web as sync.html
    participant API as web.py
    participant Task as SyncTaskStore
    participant Queue as SyncCoordinator
    participant Core as _do_data_sync

    User->>API: POST /api/sync
    API->>Queue: submit(request, task_store, work)
    Queue->>Queue: 临界区校验 singleflight 与容量
    Queue->>Task: 原子写 queued
    Queue->>Queue: 注册并强引用后台协程
    Queue-->>API: accepted(task_id)
    API-->>Web: 202 + task_id + Location
    Queue->>Task: running / authenticating
    Queue->>Core: 在线程池执行
    Core->>Task: syncing_metrics / syncing_activities
    loop Garmin 每个日期与指标
        Core->>Task: progress.items 已处理数/总数/日期/指标
    end
    Core->>Core: 写 SQLite + 同步日历
    Queue->>Task: backing_up
    Queue->>Queue: 释放 Provider / Storage
    Queue->>Task: succeeded 或 failed
    loop queued 或 running
        Web->>API: GET /api/sync/tasks/{task_id}
        API->>Task: 读取当前用户任务
        API-->>Web: 200 + status/progress/error
    end
```

202 必须发生在任务记录写入和后台调度接纳都成功之后。排队阶段不修改同步日历；工作线程开始后
才写 `neurun_provider_sync=pending`。成功终态必须晚于用户 SQLite 备份和资源回收；失败路径也先
释放 Provider、Storage，再写结构化任务错误并释放 singleflight 名额。进程重启后，旧实例的
`queued/running` 进入 `interrupted`，不自动重放第三方请求。

Garmin 指标同步通过 garmy 已有完成/跳过/失败事件推进 `progress.items`；任务文件保存最近一次真实
明细，因此轮询断开或页面刷新只会丢失中间动画，不会丢失已处理计数。适配层不会从运行时间推算
百分比，也不会改变 Provider 写入、日历状态或后续活动补齐的执行顺序。

Coros 活动查询仍按范围分页访问 Training Hub；每一页成功返回后才上报累计记录数。Mobile 睡眠
仍先按范围批量读取并允许失败降级，随后健康聚合循环每完成一个日期才上报逐日进度。慢请求在响应
返回前不会虚增计数；活动、RHR、HRV 与睡眠的既有容错和写入顺序保持不变。

---

### 5.6 Web 日报图片保存流程

```mermaid
sequenceDiagram
    actor User as 用户
    participant Web as chat.html
    participant Canvas as Browser Canvas
    participant OS as 系统分享或下载

    User->>Web: 点击“保存图片”
    Web->>Web: 复用当前 GET /api/dashboard 数据
    Web->>Canvas: 按当前主题绘制日报摘要
    Canvas-->>Web: PNG Blob
    alt 浏览器支持文件分享
        Web->>OS: navigator.share(files)
        OS-->>User: 分享或保存到相册
    else 不支持文件分享
        Web->>OS: Blob URL + download
        OS-->>User: 下载 PNG 文件
    end
```

此流程不再请求服务端，也不读取页面之外的数据。取消系统分享属于正常可恢复状态；生成或保存失败时，
页面通过 `aria-live` 状态文本反馈原因，并恢复“保存图片”按钮。
