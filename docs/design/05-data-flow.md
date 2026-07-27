# 设计方案 — 5. 数据流

> 属于 [设计方案索引](../design.md) · 版本 v3.2 · 2026-07-26

---

## 5. 数据流

### 5.1 整体数据流

```mermaid
graph LR
    A["📋 读取<br/>环境变量"] --> B["🔐 创建<br/>AuthClient"]
    B --> C["✅ 检查<br/>Token 有效性"]
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
    C -.->|"~/.garmy/<br/>Token 文件"| C
```

### 5.2 sync 命令执行流程

```mermaid
graph TD
    SYNC["sync 命令"]
    S1["1. config.py<br/>加载并校验环境变量"]
    S2["2. auth.py<br/>创建 AuthClient，检查/执行登录"]
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
```

注册认证和运动平台认证是两层独立边界：`/api/setup` 不得为无会话请求自动创建用户；
应用退出只删除 Cookie，不清理运动平台 Token。再次登录后仍使用原 API Key 和用户数据目录。

同步请求重新创建 Provider 时，必须先从用户目录恢复并执行 `authenticate()`，再读取平台
`user_id`。任何平台认证失败都在本地 SQLite 写入之前终止，并把连接状态更新为
`expired`；禁止用 `user_id=0` 或服务级默认凭证继续同步。

认证完成且范围确定后，核心同步函数先将范围内每天的 `neurun_provider_sync` 标记为
`pending`。全部 Provider 拉取与 SQLite 写入成功后统一更新为 `completed`；异常退出时
统一更新为 `failed` 并保留截断后的错误信息。Web 日历 API 只聚合这些状态和本地表，
不为展示日历触发 Provider 认证或远程请求。

三平台写入活动时同时保存标准化 `activity_type`。日历聚合先判断当天是否存在跑步记录；
存在时直接输出 `synced`，优先于范围标记和指标级状态。升级前的历史活动没有该字段时，
只在名称包含“跑步”或 `run` 时作兼容识别；非跑步活动和健康数据仍按原状态证据聚合。
