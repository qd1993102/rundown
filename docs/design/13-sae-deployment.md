# 设计方案 — 13. Web Chat 部署方案（多用户）

> 版本: v2.4 · 更新日期: 2026-07-26 · 状态: 已实现

---

## 1. 背景与目标

将 neurun 部署到阿里云轻量应用服务器（或同等廉价 VPS），通过 **Web Chat 页面**直接面向普通用户，
不需要任何 AI 客户端（Claude Desktop / OpenClaw）。用户打开浏览器 → 邀请注册/登录 →
绑定运动平台 → 直接和 AI 教练对话。

**初期约束**：低成本（月费 < ¥100）、不引入中间件、零前端框架依赖。

---

## 2. 整体架构

```mermaid
graph TB
    subgraph USER["👤 用户"]
        BROWSER["浏览器<br/>打开 neurun.xxx.com"]
    end

    subgraph VPS["轻量应用服务器 ¥68/月 (2C2G 40GB)"]
        subgraph WEB["Web 服务 (Python uvicorn :8080)"]
            PAGES["页面路由<br/>/login /register<br/>/setup / chat"]
            HEALTH["基础设施路由<br/>GET/HEAD /healthz"]
            API["API 路由<br/>邀请码验证 + 注册/登录<br/>数据源绑定 + 同步/对话"]
            SSE["SSE /api/chat/stream<br/>流式 AI 回复"]

            subgraph CORE["neurun 核心（复用现有代码）"]
                AUTH["auth.py<br/>Garmin 登录 + Token"]
                STORAGE["storage.py<br/>SQLite 查询"]
                MEMORY["memory.py<br/>日报/记忆/画像"]
                COACH["coach.py<br/>DeepSeek API"]
            end
        end
        DISK["本地磁盘 40GB<br/>invite-codes.json + users/<br/>data/{api_key}/tokens/<br/>memory/ + data.db"]
    end

    subgraph EXTERNAL["外部"]
        GARMIN["Garmin Connect"]
        DEEPSEEK["DeepSeek API"]
    end

    BROWSER <-->|"HTTPS"| WEB
    AUTH --> GARMIN
    COACH --> DEEPSEEK
    CORE --> DISK
```

---

## 3. 用户流程

```mermaid
sequenceDiagram
    actor User as 用户
    participant Web as 浏览器
    participant VPS as 服务器
    participant Garmin as Garmin API
    participant DS as DeepSeek

    Note over User,DS: === 首次使用 ===

    User->>Web: 打开 neurun.xxx.com
    Web->>VPS: GET /
    VPS-->>Web: 登录页（无会话）
    User->>Web: 输入邀请码
    Web->>VPS: POST /api/invitations/validate
    VPS-->>Web: 邀请码可用
    User->>Web: 输入昵称 + 邮箱 + 密码
    Web->>VPS: POST /api/register
    VPS->>VPS: scrypt 哈希 + 创建用户 + 核销邀请码
    VPS-->>Web: HttpOnly Cookie，跳转 /setup

    User->>Web: 输入运动平台账号 + 密码
    Web->>VPS: POST /api/setup
    VPS->>Garmin: login(email, password)
    Garmin-->>VPS: needs_mfa
    VPS-->>Web: { status: "needs_mfa" }

    User->>Web: 输入 MFA 验证码
    Web->>VPS: POST /api/mfa
    VPS->>Garmin: resume_login(code)
    Garmin-->>VPS: OAuth Token
    VPS->>VPS: 保存 Token → data/{api_key}/tokens/
    VPS-->>Web: { status: "ok" }

    Note over User,DS: === 首次同步 ===

    User->>Web: 点击"开始同步"
    Web->>VPS: POST /api/sync
    VPS->>Garmin: 拉取活动 + 健康数据
    VPS->>VPS: 只写入 SQLite
    VPS-->>Web: 同步完成
    User->>Web: 选择日期并点击"生成日报"
    Web->>VPS: POST /api/reports
    VPS->>VPS: 从 SQLite 生成结构化日报
    VPS->>DS: 使用 prompts/coach.md 生成 AI 洞察
    VPS->>VPS: 将 AI 洞察写入 Front Matter 与正文
    VPS-->>Web: 日报生成完成

    Note over User,DS: === 日常使用 ===

    User->>Web: 打开 neurun.xxx.com
    Web->>VPS: GET / → GET /api/dashboard
    VPS->>VPS: 读取最新日报 + 画像 + 数据
    VPS-->>Web: 仪表盘 JSON（训练/睡眠/身体/AI 洞察）
    Web-->>User: 日报仪表盘（数据卡片展示）

    User->>Web: 点击"同步"
    Web->>VPS: POST /api/sync
    VPS->>Garmin: 拉取活动 + 健康数据
    VPS->>VPS: 只写入 SQLite
    VPS-->>Web: 同步完成
    User->>Web: 按需进入日报页显式生成日报
```

---

## 4. 数据隔离

```
服务器本地磁盘 /app/data/
├── invite-codes.json         ← 管理员维护，成功注册后记录 used_by/used_at
├── users/
│   ├── rd_abc.json           ← 昵称、登录邮箱、密码哈希、平台状态
│   └── rd_xyz.json
├── rd_abc/
│   ├── tokens/
│   │   ├── oauth1_token.json
│   │   ├── oauth2_token.json
│   │   └── coros-auth.json       # Coros 用户绑定 token（0600）
│   ├── huawei-tokens/
│   │   ├── group-pals-token      # Huawei 每用户 CrewPals 凭证（0600）
│   │   └── huawei-oauth.json     # Huawei AT（0600）
│   ├── memory/
│   │   ├── auto/daily/...
│   │   ├── profile/...
│   │   └── goals/...
│   └── data.db              ← SQLite
├── rd_xyz/
│   ├── tokens/...
│   ├── memory/...
│   └── data.db
└── backup/                   ← 灾备（同步到 OSS，可选）
    ├── rd_abc.db
    └── rd_xyz.db
```

VPS 磁盘是持久化的，容器重启不丢。OSS 仅作为灾备，初期可跳过。
`data/` 下的目录统一为 `0700`，敏感文件统一为 `0600`，Web 服务、邀请码 CLI 和
部署脚本必须使用同一个低权限运行用户；不得用 root 在另一工作目录生成第二套数据。

---

## 5. 源码改动

### 5.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/users.py` | 用户管理器 |
| `src/invitations.py` | 邀请码 JSON 校验与原子核销 |
| `src/web.py` | Web 页面 + API 路由 |
| `web/templates/auth.html` | 登录 + 两步邀请注册页面 |
| `web/templates/chat.html` | 日报仪表盘页面（纯 HTML + CSS + JS） |
| `web/templates/setup.html` | 多步初始化向导（数据源绑定 + 个人资料 + 目标） |
| `Dockerfile` | 容器镜像 |
| `docker-compose.yml` | 一键部署 |

### 5.2 修改文件

| 文件 | 改动 |
|------|------|
| `src/config.py` | + `non_interactive` + `UserConfig` 多用户路径 + 邀请码文件配置 |
| `src/auth.py` | `start_login()` / `complete_mfa()` 两步拆分 |
| `src/storage.py` | + `backup_to()` / `restore_from()`；从用户 SQLite 解析唯一平台用户 ID |
| `src/providers/garmin.py` | `non_interactive` 参数传递 |
| `src/main.py` | + `cmd_serve` Web 服务入口；拆分纯同步与日报生成核心流程 |
| `src/memory.py` | 日报生成接口透传在线 AI 洞察，以同一洞察重新渲染正文 |
| `src/web.py` | 分离 `POST /api/sync` 与 `POST /api/reports` 的职责 |

### 5.3 不改的文件

`src/mcp_server.py`、`src/coach.py`、`src/render.py`、`src/image.py`、`src/activity.py`

---

## 6. 核心模块

### 6.1 `src/users.py`

```python
class UserManager:
    def __init__(self, data_dir: str):
        """data_dir: /app/data/"""

    def register_account(self, nickname: str, email: str, password: str) -> UserRecord:
        """创建带 scrypt 密码哈希的应用账号。"""

    def authenticate(self, email: str, password: str) -> UserRecord | None:
        """校验应用账号。"""

    def get(self, key: str) -> UserRecord | None:
        """通过 API key 查找用户。"""

    def get_token_dir(self, key: str) -> str: ...
    def get_memory_dir(self, key: str) -> str: ...
    def get_db_path(self, key: str) -> str: ...
```

用户标识：服务端生成 `rd_xxxx` 格式的 API key，写入 HttpOnly Cookie。不依赖微信 openid。
登录邮箱和运动平台邮箱分别存储，应用账号密码只保留 `scrypt` 哈希。

### 6.2 `src/web.py` — HTTP 层

复用你现有的 `render.py` 风格——手写 HTML + 内嵌 CSS，不引入前端框架。

```python
# 基础设施路由（无会话、用户数据或外部服务依赖）
@server.custom_route("/healthz", methods=["GET", "HEAD"])
async def healthz(request): ...
    # GET → 200 {"status": "ok"}
    # HEAD → 200，无响应体

# 页面路由（服务端渲染 HTML）
@server.custom_route("/", methods=["GET"])
async def index(request): ...
    # 已绑定用户 → 日报仪表盘（chat.html）
    # 无会话 → /login；已登录但未绑定 → /setup

@server.custom_route("/login", methods=["GET"])
@server.custom_route("/register", methods=["GET"])

@server.custom_route("/setup", methods=["GET"])
async def setup_page(request): ...

# API 路由（JSON）
@server.custom_route("/api/invitations/validate", methods=["POST"])
@server.custom_route("/api/register", methods=["POST"])
@server.custom_route("/api/login", methods=["POST"])

@server.custom_route("/api/setup", methods=["POST"])
async def api_setup(request): ...
    # 先校验应用会话，再绑定 Garmin / Coros / Huawei；不得隐式注册

@server.custom_route("/api/mfa", methods=["POST"])
async def api_mfa(request): ...

@server.custom_route("/api/dashboard", methods=["GET"])
async def api_dashboard(request): ...
    # 返回日报仪表盘 JSON（训练/睡眠/身体状态/AI 洞察）

@server.custom_route("/api/profile", methods=["POST"])
async def api_profile(request): ...
    # 保存个人资料 + 最佳成绩

@server.custom_route("/api/goals", methods=["POST"])
async def api_goals(request): ...
    # 保存训练目标

@server.custom_route("/api/preferences", methods=["POST"])
async def api_preferences(request): ...
    # 保存训练偏好

@server.custom_route("/api/sync", methods=["POST"])
async def api_sync(request): ...
    # 从用户注册表注入 provider；不得回退成服务级默认 provider
    # 单一 Coros 用户升级时，可安全迁移旧版全局 token；多用户时禁止猜测归属
    # mode=single: date → 精确单日，内部 sync_days=0
    # mode=batch: from_date/to_date → 包含首尾日期的精确范围
    # 只同步和持久化数据，不生成或覆盖日报
    # 未提供 mode 时兼容旧版 date/sync_days/full

@server.custom_route("/api/reports", methods=["POST"])
async def api_report_generate(request): ...
    # 用户显式选择 date，只读取本地 SQLite，不触发平台同步
    # 结构化日报生成后，用 prompts/coach.md 生成在线 AI 洞察
    # AI 成功时重新渲染正文；未配置或调用失败时保留本地规则兜底

@server.custom_route("/api/status", methods=["GET"])
async def api_status(request): ...

@server.custom_route("/api/logout", methods=["POST"])
async def api_logout(request): ...
```

**为什么不用 Flask/FastAPI？** FastMCP 内置的 uvicorn + Starlette 完全够用，
`custom_route` 注册路由。零额外依赖。

`/healthz` 是进程存活检查，不是业务就绪检查：它不得读取 Cookie、用户注册表、SQLite，
也不得调用 Garmin、Coros、Huawei 或 DeepSeek。负载均衡只用它判断 Web 进程能否响应 HTTP；
业务依赖故障应由各 API 自身的错误和监控暴露。

### 6.3 `src/auth.py` — MFA 两步

```python
class AuthManager:
    def start_login(self, email, password, token_dir) -> str:
        """返回 "ok" 或 "needs_mfa"。"""

    def complete_mfa(self, mfa_code: str) -> None:
        """完成登录，Token 落盘。"""
```

MFA 中间状态存内存 dict（5 分钟过期），容器重启丢失，用户重新发起即可。

### 6.4 `src/coach.py` — 流式对话

现有 `coach.py` 是一次性返回 JSON。新增流式版本：

```python
async def chat_stream(messages: list[dict], context: str) -> AsyncIterator[str]:
    """流式 AI 对话，SSE 逐块输出。"""
    # 调用 DeepSeek API stream=true
    # 每次 yield 一个 token
```

---

## 7. 日报仪表盘页面

一个自包含的 HTML 文件，全部内嵌——零构建、零依赖、零 CDN：

```
web/templates/chat.html    (~300行)
├── CSS: 移动端优先，暗色主题（跑步场景护眼）
├── HTML: 训练概览卡片 + 身体指标 + 睡眠 + AI 洞察
├── JS:  GET /api/dashboard 加载数据 → 渲染卡片
└── 交互: 同步按钮 → POST /api/sync
```

首页不再是聊天界面，而是**日报仪表盘**——展示昨日训练、睡眠评分、
身体状态（RHR/HRV/电量/训练准备）、训练负荷与恢复、AI 教练洞察。
用户看到的是结构化的数据卡片，而非对话消息列表。

同步管理页 `web/templates/sync.html` 提供两个明确入口：

| 模式 | 请求字段 | 后端标准化 | 日报副作用 |
|------|----------|------------|------------|
| 单日同步 | `mode=single`, `date` | `start=end=date`, `sync_days=0` | 无 |
| 批量同步 | `mode=batch`, `from_date`, `to_date` | `sync_days=(to-from).days`，范围包含首尾 | 无 |

日期缺失、格式错误、开始日期晚于结束日期或范围超过 3 年时，API 返回 HTTP 400 和
可操作的 JSON 错误信息。两种模式都允许 `force=true` 强制覆盖相应范围的数据。
同步完成后只返回标准化的数据范围，不调用 `MemoryStore.generate_daily_report()`，也不调用在线 AI。

日报页通过 `POST /api/reports` 提供独立的用户触发入口。后端先调用
`MemoryStore.generate_daily_report()` 汇总本地 SQLite，再将其 Front Matter 交给
`coach.get_coach_insight()`；该调用的 system prompt 必须来自 `prompts/coach.md`。
AI 调用成功后以返回洞察重新生成日报，确保 YAML Front Matter、Markdown 正文和 Web 仪表盘一致；
未配置 `DEEPSEEK_API_KEY` 或在线调用失败时，初次生成的本地规则洞察保留为可用降级结果。

风格参考你现有的 `render.py` 设计品味——简洁、大气、运动感。

---

## 8. 部署

### Docker Compose 一键启动

```yaml
# docker-compose.yml
version: "3"
services:
  neurun:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data          # 持久化
    environment:
      - NEURUN_DATA_DIR=/app/data
      - NEURUN_INVITE_CODES_FILE=/app/data/invite-codes.json
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
    restart: unless-stopped
```

```bash
# 启动服务后，通过本地 CLI 创建首个邀请码
git clone <repo>
cd neurun
echo "DEEPSEEK_API_KEY=sk-xxx" > .env
docker compose up -d
docker compose exec neurun neurun invite create --output json
```

### 加 HTTPS

```bash
# Caddy 反向代理，自动申请 Let's Encrypt 证书
# Caddyfile:
# neurun.xxx.com {
#     reverse_proxy localhost:8080
# }
```

### ECS + CLB 原生部署

CLB 通过 ECS 私网地址访问后端，因此 Web 服务必须监听所有网卡，而不是仅监听回环地址：

```ini
Environment=MCP_HOST=0.0.0.0
Environment=MCP_PORT=8080
```

部署后先分别验证回环地址和 ECS 私网地址；两者都必须返回 200：

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://<ECS_PRIVATE_IP>:8080/healthz
```

CLB 后端服务器端口配置为 `8080`，HTTP 健康检查使用：

| 配置项 | 值 |
|--------|----|
| 方法 | `GET`（也兼容 `HEAD`） |
| 路径 | `/healthz` |
| 正常状态码 | `2xx` |
| 域名 | 留空 |

若私网地址请求出现 `Connection refused`，说明连接尚未进入 HTTP 路由，应检查
`MCP_HOST`、systemd 实际环境、8080 监听地址和主机防火墙，而不是放宽登录鉴权。

---

## 9. 成本

| 项目 | 方案 | 月费 |
|------|------|------|
| 服务器 | 阿里云轻量应用服务器 2C2G 40GB | ¥68 |
| 域名 | `.com` / `.cn` | ~¥5 |
| 备份 | OSS（可选，灾备用） | ~¥5 |
| **合计** | | **~¥78/月** |

---

## 10. 为什么不用框架 / 中间件

| 传统方案 | 本方案 | 省了什么 |
|----------|--------|---------|
| Flask/FastAPI | FastMCP 内置 uvicorn | 额外 Web 框架 |
| React/Vue 前端 | 一个 HTML 文件 | 前端构建工具链 |
| MySQL/PostgreSQL | SQLite 每用户一个文件 | 数据库服务 |
| Redis Session | Cookie + 服务端 dict | 缓存中间件 |
| Nginx | Caddy 自动 HTTPS | 反向代理配置 |
| 微信小程序 | Web Chat 响应式 | 审核 + 双端开发 |

---

## 11. 验证计划

```bash
# 本地
docker compose up -d
curl -fsS http://localhost:8080/healthz  # → {"status":"ok"}
curl -I http://localhost:8080/healthz    # → HTTP 200
# 浏览器完成邀请码注册、数据源绑定与对话

# 多用户
# 两个浏览器（或无痕窗口）用不同邀请码注册并绑定不同运动平台账号
# 验证数据隔离 + AI 回复互不干扰

pytest  # 零失败
```

---

> **关联文档**：[index.md](index.md) · [03-architecture.md](03-architecture.md) · [04-modules.md](04-modules.md) · [memory-system.md](memory-system.md) · [ai-coaching.md](ai-coaching.md)
