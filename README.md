# Rundown

> Your daily running rundown — Garmin data, AI coaching, and training knowledge base.

## Quick Start

```bash
# 1. 配置
cp .env.example .env
# 编辑 .env 填入 Garmin 账号密码

# 2. 安装
pip install -e .

# 3. 首次同步
rundown sync --full

# 4. 查看今日日报（自动同步 + md + HTML + PNG + AI）
rundown daily
```

## Commands

### `rundown sync`

纯数据同步：从运动平台拉取数据到本地，不生成报告（报告生成使用 `rundown daily`）。

```bash
rundown sync                        # 默认：拉取最近 30 天
rundown sync --days 90              # 拉取最近 90 天
rundown sync --from 2026-01-01 --to 2026-06-24  # 指定日期范围
rundown sync --full                 # 全量同步（最多回溯 3 年）
rundown sync --force                # 强制覆盖：清除已有数据后重新拉取
rundown sync --metrics sleep hrv    # 仅同步指定指标
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--days N` | int | 30 | 同步最近 N 天 |
| `--from DATE` | str | — | 起始日期 YYYY-MM-DD |
| `--to DATE` | str | — | 结束日期 YYYY-MM-DD |
| `--metrics ...` | list | all | 指定指标（空格分隔） |
| `--full` | flag | — | 全量同步（最多 3 年） |
| `--force` | flag | — | 强制覆盖：清除已有数据后重新拉取 |

> 💡 同步完成后运行 `rundown daily` 生成日报。

---

### `rundown daily`

一站式命令：自动检查并同步数据 → 生成完整日报（md + HTML + PNG + AI 洞察）。

```bash
rundown daily                       # 今天（自动检查并补同步缺失数据）
rundown daily --date 2026-06-25     # 指定日期
rundown daily --theme dark          # 暗黑主题
rundown daily --format json         # 仅 JSON 输出
rundown daily --skip-sync           # 跳过同步，仅基于本地数据生成报告
rundown daily --sync-days 7         # 同步最近 7 天数据后生成报告
rundown daily --full                # 全量同步（3年）后生成报告
rundown daily --force               # 强制覆盖已有数据后重新同步
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

---

### `rundown activities`

查询运动活动列表，支持按类型筛选和 CSV 导出。

```bash
rundown activities                  # 最近 30 天全部活动
rundown activities --recent 10      # 最近 10 条
rundown activities --type running   # 跑步活动
rundown activities --type cycling --export cycling.csv  # 导出 CSV
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--recent N` | int | 30 | 最近 N 条活动 |
| `--type TYPE` | str | — | 运动类型筛选（running/cycling/swimming/...） |
| `--export PATH` | str | — | 导出为 CSV 文件 |

---

### `rundown health`

查询健康指标数据。

```bash
rundown health                      # 最近 7 天全部指标
rundown health --days 14            # 最近 14 天
rundown health --metric sleep       # 仅查看睡眠
rundown health --metric hrv         # 仅查看 HRV
rundown health --export health.csv  # 导出 CSV
```

可选指标：`sleep`, `heart_rate`, `hrv`, `stress`, `body_battery`, `steps`, `calories`, `respiration`, `training_readiness`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--metric KEY` | str | — | 指定指标 key |
| `--days N` | int | 7 | 最近 N 天 |
| `--export PATH` | str | — | 导出为 CSV 文件 |

---

### `rundown memory`

记忆管理 — 浏览、搜索、生成记忆，管理目标和训练计划。

```bash
# 列出记忆
rundown memory list
rundown memory list --type daily_report          # 按类型
rundown memory list --tag 5k                     # 按标签
rundown memory list --status active              # 按状态
rundown memory list --search "间歇跑"             # 全文搜索

# 查看单条记忆
rundown memory show 2026-06-24                   # 查看 6/24 日报
rundown memory show 2026-W26                     # 查看第 26 周摘要

# 手动生成摘要
rundown memory summarize --period weekly         # 生成本周摘要
rundown memory summarize --period monthly        # 生成本月摘要
rundown memory summarize --date 2026-06-20       # 指定日期

# 校验与维护
rundown memory check                             # 完整性校验
rundown memory index                             # 重建所有索引
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

### `rundown status`

查看数据同步状态。

```bash
rundown status
```

---

### `rundown mcp`

启动 MCP Server，供 OpenClaw / Claude Desktop 连接进行 AI 教练对话。

```bash
rundown mcp                         # 默认端口 8765
rundown mcp --port 9876             # 自定义端口
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port N` | int | 8765 | MCP Server 监听端口 |

---

## HTML Daily Report

`rundown daily` 自动生成静态 HTML 日报 + PNG 截图，输出到统一目录 `output/`。

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
| `RUNDOWN_ACCOUNT` | ✅ | — | 运动平台账号（邮箱或手机号，兼容旧名 `RUNDOWN_EMAIL`/`GARMIN_EMAIL`） |
| `RUNDOWN_PASSWORD` | ✅ | — | 运动平台登录密码（兼容旧名 `GARMIN_PASSWORD`） |
| `RUNDOWN_PROVIDER` | — | `garmin` | 数据源: garmin / coros |
| `RUNDOWN_DB_PATH` | — | `./data/rundown_data.db` | SQLite 数据库路径 |
| `RUNDOWN_MEMORY_DIR` | — | `./memory` | 记忆存储目录（目标、档案、日报等） |
| `RUNDOWN_HOME` | — | (当前目录) | 数据工作目录，设后所有相对路径基于此解析 |
| `RUNDOWN_SYNC_DAYS` | — | `30` | 默认同步天数 |
| `RUNDOWN_LOG_LEVEL` | — | `INFO` | 日志级别 |
| `GARMIN_DOMAIN` | — | `garmin.com` | Garmin 专用：API 域名 |
| `GARMIN_TOKEN_DIR` | — | `~/.garmy` | Garmin 专用：Token 目录 |
| `DEEPSEEK_API_KEY` | — | — | DeepSeek API Key（AI 洞察） |

### `rundown mcp`

启动 MCP Server，供 OpenClaw / Claude Desktop 连接进行 AI 教练对话。

```bash
rundown mcp                         # stdio 模式（默认）
```

**OpenClaw 配置** (`mcp.json`):

```json
{
  "mcpServers": {
    "rundown": {
      "command": "rundown",
      "args": ["mcp"],
      "description": "Garmin 运动数据 + AI 教练"
    }
  }
}
```

MCP 提供的 Resources:
- `rundown://daily/latest` — 最新日报
- `rundown://daily/{date}` — 指定日期日报
- `rundown://context/full` — 完整 AI 上下文（日报+7天趋势+目标+资料）
- `rundown://goals/active` — 进行中的目标
- `rundown://profile` — 个人档案
- `rundown://preferences` — 训练偏好

MCP 提供的 Tools:
- `query_activities` — 查询活动列表
- `query_health_metrics` — 查询健康指标
- `get_activity_detail` — 获取活动分段详情
- `search_memories` — 搜索记忆库
- `get_training_advice` — 生成训练建议

## Architecture

```
Rundown
├── Data Layer:    SQLite (via garmy LocalDB)
├── Memory Layer:  Markdown + YAML Front Matter
├── Output Layer:  Terminal (rich) / JSON / Static HTML
└── AI Layer:      MCP Server → OpenClaw / Claude Desktop
```

## Project Structure

```
rundown/
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
