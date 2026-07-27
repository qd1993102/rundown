# neurun

> Your AI running coach — Garmin data, AI coaching, and training knowledge base.

## Quick Start

```bash
# 1. 配置
cp .env.example .env
# 编辑 .env 填入 Garmin 账号密码

# 2. 安装
pip install -e .

# 3. 首次同步
neurun sync --full

# 4. 查看今日日报（自动同步 + md + HTML + PNG + AI）
neurun daily
```

## Commands

### `neurun sync`

纯数据同步：从运动平台拉取数据到本地，不生成报告（报告生成使用 `neurun daily`）。

```bash
neurun sync                        # 默认：拉取最近 30 天
neurun sync --days 90              # 拉取最近 90 天
neurun sync --from 2026-01-01 --to 2026-06-24  # 指定日期范围
neurun sync --full                 # 全量同步（最多回溯 3 年）
neurun sync --force                # 强制覆盖：清除已有数据后重新拉取
neurun sync --metrics sleep hrv    # 仅同步指定指标
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--days N` | int | 30 | 同步最近 N 天 |
| `--from DATE` | str | — | 起始日期 YYYY-MM-DD |
| `--to DATE` | str | — | 结束日期 YYYY-MM-DD |
| `--metrics ...` | list | all | 指定指标（空格分隔） |
| `--full` | flag | — | 全量同步（最多 3 年） |
| `--force` | flag | — | 强制覆盖：清除已有数据后重新拉取 |

> 💡 同步完成后运行 `neurun daily` 生成日报。

---

### `neurun daily`

一站式命令：自动检查并同步数据 → 生成完整日报（md + HTML + PNG + AI 洞察）。

```bash
neurun daily                       # 今天（自动检查并补同步缺失数据）
neurun daily --date 2026-06-25     # 指定日期
neurun daily --theme dark          # 暗黑主题
neurun daily --format json         # 仅 JSON 输出
neurun daily --skip-sync           # 跳过同步，仅基于本地数据生成报告
neurun daily --sync-days 7         # 同步最近 7 天数据后生成报告
neurun daily --full                # 全量同步（3年）后生成报告
neurun daily --force               # 强制覆盖已有数据后重新同步
```

每次执行自动：检查本地数据完整性 →（缺失时自动拉取）→ 生成 md → AI 洞察 → HTML → PNG → 终端展示。

> 默认智能检测：只同步缺失的日期，已有本地数据则跳过。`--force` 可强制重新拉取。

生成文件：
- `memory/auto/daily/YYYY-MM-DD.md` — 记忆文件
- `output/YYYY-MM-DD.html` — 静态网页
- `output/YYYY-MM-DD.png` — 截图

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--date DATE` | str | 今天 | 报告日期 YYYY-MM-DD |
| `--format FMT` | md/json | md | md(终端+文件输出) / json |
| `--theme NAME` | str | sport | HTML/PNG 主题: fresh / sport / dark |
| `--sync-days N` | int | auto | 同步最近 N 天（默认自动检测缺失） |
| `--skip-sync` | flag | — | 跳过同步，仅用本地数据 |
| `--full` | flag | — | 全量同步后生成报告 |
| `--force` | flag | — | 强制覆盖已有数据后重新同步 |

### Web 邀请注册

Web 服务不再通过“绑定运动平台”隐式创建用户。首次访问会进入登录页，新用户必须：

1. 输入有效邀请码；
2. 填写昵称、邮箱和至少 8 位密码；
3. 注册成功后，再单独绑定 Garmin、Coros 或 Huawei 数据源。

应用账号邮箱只用于 neurun 登录，和运动平台账号相互独立。密码使用随机盐 `scrypt`
哈希保存，不写入日志或用户 JSON 明文字段。登录成功后继续使用 HttpOnly Cookie 维持会话。

管理员通过 JSON 文件维护邀请码。默认路径为
`<NEURUN_DATA_DIR>/invite-codes.json`，也可用 `NEURUN_INVITE_CODES_FILE` 覆盖：

```bash
neurun invite create --output json
neurun invite list --output json
neurun invite show inv_xxx --reveal --output json
neurun invite revoke inv_xxx --output json
```

| 子命令 | 关键参数 | 说明 |
|--------|----------|------|
| `invite create` | `-n/--count`, `-o/--output` | 生成 1–100 个随机一次性邀请码，返回完整码 |
| `invite list` | `-o/--output` | 列出状态，邀请码默认掩码 |
| `invite show` | `invitation_id`, `--reveal`, `-o/--output` | 查看单条记录；仅本地 CLI 可显式显示完整码 |
| `invite revoke` | `invitation_id`, `-o/--output` | 非交互式停用邀请码 |

```json
{
  "codes": [
    {
      "id": "inv_xxx",
      "code": "neurun_系统随机生成的完整邀请码",
      "enabled": true,
      "created_at": "2026-07-21T00:00:00+00:00",
      "used_by": null,
      "used_at": null
    }
  ]
}
```

每个邀请码严格只能创建一个账号，不自动过期；`enabled=false` 可立即停用。
每次成功注册后服务端原子写入 `used_by` 和 `used_at`。邀请码文件缺失或
格式错误时，注册接口返回可操作的 `503` 错误，不会降级为开放注册。
普通 `list` 只显示掩码，完整邀请码必须通过服务器本地 `show --reveal` 显式读取。

Docker 部署可在服务启动后生成首个邀请码：

```bash
docker compose exec neurun neurun invite create --output json
```

systemd/ECS 部署必须使用与 Web 服务相同的低权限用户写入持久化目录，不能直接以
root 运行邀请码或同步命令。例如服务用户为 `neurun` 时：

```bash
sudo -u neurun env \
  NEURUN_DATA_DIR=/var/lib/neurun \
  NEURUN_INVITE_CODES_FILE=/var/lib/neurun/invite-codes.json \
  /opt/neurun-venv/bin/neurun invite create --count 5 --output json
```

若曾经用 `sudo neurun ...` 生成过 `/var/lib/neurun` 下的数据，先修复归属再重启服务：

```bash
sudo chown -R neurun:neurun /var/lib/neurun
sudo chmod 700 /var/lib/neurun
sudo systemctl restart neurun.service
```

对应 MCP 管理工具默认关闭，仅当本地 MCP 设置 `NEURUN_ENABLE_ADMIN_TOOLS=true` 时注册；
公网 `serve` 模式始终拒绝启用，MCP 也不提供完整邀请码读取。

相关 Web API：

- `GET|HEAD /healthz`：无鉴权的 Web 进程存活检查，不读取用户数据或访问外部平台；
- `POST /api/invitations/validate`：验证邀请码，不预占名额；
- `POST /api/register`：提交 `invite_code`、`nickname`、`email`、`password`；
- `POST /api/login`：提交应用账号 `email`、`password`。

ECS 通过 CLB 暴露 Web 服务时，进程必须使用 `MCP_HOST=0.0.0.0` 和
`MCP_PORT=8080` 监听私网网卡，CLB 后端端口设为 `8080`，健康检查方法设为
`GET`、路径设为 `/healthz`、正常状态码设为 `2xx`。部署后可验证：

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://<ECS_PRIVATE_IP>:8080/healthz
```

若第二条返回 `Connection refused`，请求尚未进入 neurun 路由，应先检查 systemd
中的 `MCP_HOST` 和实际监听地址，而不是调整登录接口权限。

### Web 同步页面

`/sync` 将同步分为两个独立入口：

- **单日同步**：选择一个日期，只拉取并保存该日数据。
- **批量同步**：选择开始和结束日期（包含首尾两天），批量补齐并保存范围内数据。

页面顶部提供按月切换的同步日历，每个日期显示以下状态：

- **已同步**：该日同步流程完整结束，即使当天没有运动或健康数据也视为已覆盖；
- **部分完成**：旧数据或部分指标已落库，但没有完整的逐日完成标记；
- **失败**：该日所在同步请求失败，可点击日期带入单日同步后重试；
- **同步中**：同步请求已开始但尚未结束；
- **未同步**：本地没有该日同步记录；未来日期不可同步。

对“运动记录同步”而言，当天只要存在至少一条跑步记录，就优先判定为**已同步**，不再因
Garmin 遗留的 `activities=pending` 或个别健康指标失败显示为“部分完成”。新同步的活动
会保存标准化 `activity_type`；升级前历史数据使用活动名称中的“跑步”或 `run` 兜底识别。

日历摘要以“已同步”显示当前月份覆盖天数，并给出“已同步至”日期。点击今天或过去的任意日期会自动填入
“单日同步”的日期输入框；单日或批量同步完成后，当前月日历立即刷新。

同步不会生成或覆盖日报。用户需要前往 `/reports` 选择日期并点击“生成日报”，
该操作只读取已同步到本地 SQLite 的数据，不会再次访问运动平台。

两种模式均支持“强制覆盖已有数据”。对应的机器可读请求为：

```json
{"mode":"single","date":"2026-07-19","force":false}
```

```json
{"mode":"batch","from_date":"2026-07-01","to_date":"2026-07-19","force":false}
```

`POST /api/sync` 成功响应包含 `mode`、`from_date`、`to_date`，不包含日报生成结果。
同步开始时 Garmin、Coros、Huawei 都会先恢复并验证当前用户凭据，再读取平台
`user_id` 和打开 SQLite；认证失败会返回 HTTP 401、将连接标记为 `expired` 并提示重新绑定，
不会用 `user_id=0` 或未认证客户端继续写本地数据。
`GET /api/sync/calendar?month=YYYY-MM` 返回当前用户该月的逐日状态、活动数量、健康数据
存在性、跑步记录数量及月份摘要；该接口只读取用户本地 SQLite，不访问运动平台。
未提供 `mode` 时继续兼容原来的 `date`、`sync_days`、`full` 请求格式。

显式生成日报使用：

显式生成接口为 `POST /api/reports`，请求体：

```json
{"date":"2026-07-19"}
```

日报先基于本地数据生成结构化指标，再通过 `prompts/coach.md` 驱动在线 AI 教练生成洞察，
并将洞察写入 Front Matter 和 Markdown 正文。未配置 `DEEPSEEK_API_KEY` 或在线调用失败时，
保留本地规则洞察作为降级结果。

---

### `neurun activities`

查询运动活动列表，支持按类型筛选和 CSV 导出。

```bash
neurun activities                  # 最近 30 天全部活动
neurun activities --recent 10      # 最近 10 条
neurun activities --type running   # 跑步活动
neurun activities --type cycling --export cycling.csv  # 导出 CSV
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--recent N` | int | 30 | 最近 N 条活动 |
| `--type TYPE` | str | — | 运动类型筛选（running/cycling/swimming/...） |
| `--export PATH` | str | — | 导出为 CSV 文件 |

---

### `neurun health`

查询健康指标数据。

```bash
neurun health                      # 最近 7 天全部指标
neurun health --days 14            # 最近 14 天
neurun health --metric sleep       # 仅查看睡眠
neurun health --metric hrv         # 仅查看 HRV
neurun health --export health.csv  # 导出 CSV
```

可选指标：`sleep`, `heart_rate`, `hrv`, `stress`, `body_battery`, `steps`, `calories`, `respiration`, `training_readiness`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--metric KEY` | str | — | 指定指标 key |
| `--days N` | int | 7 | 最近 N 天 |
| `--export PATH` | str | — | 导出为 CSV 文件 |

---

### `neurun memory`

记忆管理 — 浏览、搜索、生成记忆，管理目标和训练计划。

```bash
# 列出记忆
neurun memory list
neurun memory list --type daily_report          # 按类型
neurun memory list --tag 5k                     # 按标签
neurun memory list --status active              # 按状态
neurun memory list --search "间歇跑"             # 全文搜索

# 查看单条记忆
neurun memory show 2026-06-24                   # 查看 6/24 日报
neurun memory show 2026-W26                     # 查看第 26 周摘要

# 手动生成摘要
neurun memory summarize --period weekly         # 生成本周摘要
neurun memory summarize --period monthly        # 生成本月摘要
neurun memory summarize --date 2026-06-20       # 指定日期

# 校验与维护
neurun memory check                             # 完整性校验
neurun memory index                             # 重建所有索引
```

| 子命令 | 参数 | Description |
|--------|------|-------------|
| `list` | `--type` / `--status` / `--tag` / `--search` | 列出记忆，支持多维筛选 |
| `show <id>` | — | 查看单条记忆详情 |
| `summarize` | `--period` weekly/monthly, `--date` | 手动触发摘要生成 |
| `check` | — | 全库 schema + 一致性 + 完整性校验 |
| `index` | — | 重建所有子目录 index.md |

记忆类型（`--type` 可选值）：`daily_report`, `activity_summary`, `recovery_summary`, `execution_tracker`, `fitness_profile`, `goal`, `training_plan`, `case_study`

---

### `neurun status`

查看数据同步状态。

```bash
neurun status
```

---

### `neurun mcp`

启动 MCP Server，供 OpenClaw / Claude Desktop 连接进行 AI 教练对话。

```bash
neurun mcp                         # 默认端口 8765
neurun mcp --port 9876             # 自定义端口
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port N` | int | 8765 | MCP Server 监听端口 |

---

## HTML Daily Report

`neurun daily` 自动生成静态 HTML 日报 + PNG 截图，输出到统一目录 `output/`。

绿黑色硬核风格，无需服务器，浏览器直接打开。

```
output/
├── 2026-06-20.html
├── 2026-06-21.html
├── 2026-06-22.html
└── ...
```

页面包含：今晨状态面板、昨日训练卡片、ACWR 负荷可视化、7 日 SVG 趋势图、训练建议、异常提醒。

---

## Environment Variables

| 变量 | 必填 | 默认值 | 说明 |
|------|:---:|--------|------|
| `NEURUN_ACCOUNT` | Garmin/Coros | — | 运动平台账号（邮箱或手机号，兼容旧名 `NEURUN_EMAIL`/`GARMIN_EMAIL`） |
| `NEURUN_PASSWORD` | Garmin/Coros | — | 运动平台登录密码（兼容旧名 `GARMIN_PASSWORD`） |
| `NEURUN_PROVIDER` | — | `garmin` | 数据源: garmin / coros / huawei |
| `GROUP_PALS_TOKEN` | Huawei | — | 每个用户独立的 CrewPals JWT |
| `HUAWEI_TOKEN_DIR` | — | `~/.neurun/users/<token-hash>/huawei-tokens` | Huawei AT 本地缓存目录 |
| `NEURUN_DB_PATH` | — | `./data/rundown_data.db` | SQLite 数据库路径 |
| `NEURUN_MEMORY_DIR` | — | `./memory` | 记忆存储目录（目标、档案、日报等） |
| `NEURUN_HOME` | — | (当前目录) | 数据工作目录，设后所有相对路径基于此解析 |
| `NEURUN_DATA_DIR` | Web | `./data` | Web 用户记录、邀请码、Token、数据库与记忆的持久化根目录 |
| `NEURUN_INVITE_CODES_FILE` | Web | `<NEURUN_DATA_DIR>/invite-codes.json` | 管理员维护的邀请码 JSON 文件路径 |
| `NEURUN_ENABLE_ADMIN_TOOLS` | 本地 MCP | `false` | 仅在 stdio/localhost 注册邀请码管理 tools；公网 Web 模式禁止 |
| `NEURUN_SYNC_DAYS` | — | `30` | 默认同步天数 |
| `NEURUN_LOG_LEVEL` | — | `INFO` | 日志级别 |
| `GARMIN_DOMAIN` | — | `garmin.com` | Garmin 专用：API 域名 |
| `GARMIN_TOKEN_DIR` | — | `~/.garmy` | Garmin 专用：Token 目录 |
| `DEEPSEEK_API_KEY` | — | — | DeepSeek API Key（AI 洞察） |

Web 模式会按用户注册记录选择 Garmin、Coros 或 Huawei，不受服务级
`NEURUN_PROVIDER` 默认值影响。Coros 绑定成功后，访问 token 保存到
`data/<api_key>/tokens/coros-auth.json`（目录 `0700`、文件 `0600`）；后续同步不保存
明文密码，而是恢复该用户的 token。token 无效或缺失时，接口会提示重新绑定账号。
从旧版本升级时，如果系统中恰好只有一个已激活 Coros 用户，会自动把 coros-mcp 的
旧版全局 token 迁入该用户目录；存在多个 Coros 用户时不会猜测 token 归属。

Coros 活动时长使用 API 的 `workoutTime`（实际运动时间，不包含暂停），缺失时才回退
`totalTime`。重新执行包含旧活动日期的单日或批量同步，会自动更新数据库中已有活动的
`duration_seconds`；不需要开启“强制覆盖”。如果 Coros 返回 token 失效，接口会明确
失败、将连接状态改为 `expired` 并在同步页提供重新绑定入口，不再把空活动列表误报为
同步成功。

Huawei 配置只需要当前用户的 CrewPals token：

```env
NEURUN_PROVIDER=huawei
GROUP_PALS_TOKEN=your-token
HUAWEI_TOKEN_DIR=./data/user-a/huawei-tokens
```

Web 绑定成功后，CrewPals token 会保存到
`data/<api_key>/huawei-tokens/group-pals-token`；只有 Huawei 认证成功才写入，后续同步
从该用户目录恢复，不依赖 systemd 的服务级 `GROUP_PALS_TOKEN`。

然后运行：

```bash
neurun auth
```

`neurun sync` 当前支持 Huawei 运动列表和单条活动详情；睡眠、步数、HRV 等每日健康
数据仍待按 `sampleSets` 的实际授权和字段口径接入。

neurun 通过 CrewPals 预发布 HTTPS 接口获取 Huawei AT，并将返回值保存在 `HUAWEI_TOKEN_DIR`
指定目录下的 `huawei-oauth.json`，文件权限为 `0600`。本地 AT 未过期时不会重复请求；
过期或缺失时使用 `GROUP_PALS_TOKEN` 重新获取。
默认目录键由 `GROUP_PALS_TOKEN` 的 SHA-256 摘要派生，不包含原 token；不同用户默认写入不同目录。
请求将原始 JWT 直接放入 `Authorization` header，不添加 `Bearer` 前缀。
接口响应支持 `data.accessToken/refreshToken/expiredAt/openId`，并在本地归一化为
`access_token/refresh_token/expired_at/open_id`。

设置 `NEURUN_HOME` 时只加载该目录下的 `.env`，不会再从 `~/.neurun/.env` 补入其他
用户的账号配置；需要共享的 API Key 应通过实际环境变量注入。
`neurun init` 会为当前配置选择并以 `0700` 创建独立 Token 目录；`neurun auth`
也会自动补建目录、检查已有 JSON，并将 token 文件权限收紧为 `0600`。系统不会创建
空的 `huawei-oauth.json`，该文件只在取得或导入有效 token 后生成。

Web 持久化根目录及用户、Token、memory、backup 子目录统一收紧为 `0700`；账号、邀请码、
平台凭据、SQLite、备份、Markdown 记忆和导出文件统一为 `0600`。若服务用户没有目录
写权限，错误会显示实际路径并提示修复 `chown`，不会改写到 `/root` 或其他备用目录。

也兼容外部系统导出的 Huawei token 结构：

```json
{
  "user_id": 1,
  "access_token": "...",
  "refresh_token": "...",
  "open_id": "...",
  "expired_at": 1784110879
}
```

过期时间同时接受 `expired_at` 和 `expires_at`（Unix 秒）；用户标识依次使用
`user_id`、`open_id`、`openid` 或 `sub`。

### `neurun mcp`

启动 MCP Server，供 OpenClaw / Claude Desktop 连接进行 AI 教练对话。

```bash
neurun mcp                         # stdio 模式（默认）
```

**OpenClaw 配置** (`mcp.json`):

```json
{
  "mcpServers": {
    "neurun": {
      "command": "neurun",
      "args": ["mcp"],
      "description": "Garmin 运动数据 + AI 教练"
    }
  }
}
```

MCP 提供的 Resources:
- `neurun://daily/latest` — 最新日报
- `neurun://daily/{date}` — 指定日期日报
- `neurun://context/full` — 完整 AI 上下文（日报+7天趋势+目标+资料）
- `neurun://goals/active` — 进行中的目标
- `neurun://profile` — 个人档案
- `neurun://preferences` — 训练偏好

MCP 提供的 Tools:
- `query_activities` — 查询活动列表
- `query_health_metrics` — 查询健康指标
- `get_activity_detail` — 获取活动分段详情
- `search_memories` — 搜索记忆库
- `get_training_advice` — 生成训练建议

## Architecture

```
neurun
├── Data Layer:    SQLite (via garmy LocalDB)
├── Memory Layer:  Markdown + YAML Front Matter
├── Output Layer:  Terminal (rich) / JSON / Static HTML
└── AI Layer:      MCP Server → OpenClaw / Claude Desktop
```

## Project Structure

```
neurun/
├── src/
│   ├── main.py          CLI 入口 (argparse + rich)
│   ├── config.py        环境变量管理
│   ├── auth.py          Garmin 认证 (OAuth + MFA)
│   ├── fetcher.py       数据拉取 (活动 + 健康指标)
│   ├── storage.py       SQLite 存储 (garmy LocalDB)
│   ├── memory.py        记忆系统 (日报/摘要/异常检测)
│   ├── render.py        HTML 静态页面渲染
│   └── exporter.py      CSV/JSON 导出
├── memory/              记忆库 (Markdown + YAML FM)
│   └── auto/
│       ├── daily/       每日综合报告 ⭐
│       ├── summaries/   周/月运动摘要
│       ├── recovery/    恢复摘要
│       └── execution/   训练执行跟踪
├── output/              HTML 日报输出目录
├── data/                SQLite 数据库
├── docs/design/          技术设计文档 v3.0（模块化拆分）
└── pyproject.toml
```

## Development

```bash
# Install with dev deps
pip install -e ".[dev]"

# Run tests
pytest
```

> **Documentation Sync Rule**: 每次代码变更后，必须同步更新 `docs/design/` 下的对应子文档和 `README.md`。
