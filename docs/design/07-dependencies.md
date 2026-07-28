# 设计方案 — 7. 依赖清单

> 属于 [设计方案索引](../design.md) · 版本 v3.1 · 2026-07-28

---

## 7. 依赖清单

```toml
[project]
dependencies = [
    "garmy[all]>=1.0.0",       # 核心: Garmin API + LocalDB + MCP
    "python-dotenv>=1.0.0",    # .env 文件加载
    "pyyaml>=6.0",             # YAML Front Matter 解析
    "rich>=13.0.0",            # 终端美化输出（表格、进度条）
    "httpx>=0.27.0",           # Coros/Huawei HTTP API
    "coros-mcp @ git+https://github.com/cygnusb/coros-mcp.git", # Coros 认证与模型
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-mock>=3.12.0",
]
```

**依赖说明**:
| 依赖 | 用途 |
|------|------|
| `garmy[all]` | Garmin API 认证、数据拉取、SQLite 持久化、MCP Server（安装全部可选依赖） |
| `python-dotenv` | 从 `.env` 文件加载环境变量，避免密码出现在命令行历史中 |
| `pyyaml` | 解析和生成记忆文件的 YAML Front Matter |
| `rich` | 终端表格渲染（活动列表、健康指标）、同步进度条、记忆内容美化展示 |
| `httpx` | Coros 与 Huawei API 的 HTTP 客户端 |
| `coros-mcp` | Coros 登录、Token 模型及 HRV/活动详情能力；Web 绑定 Coros 的必需运行依赖，默认安装，不属于可选 extras |

Docker 镜像执行默认的 `pip install .`，因此所有 Web 页面公开支持的 Provider 依赖都必须位于
`project.dependencies`。`coros-mcp` 当前从 Git 仓库安装，基础镜像必须同时提供 `git`，不能等到
用户选择 Coros 绑定时才发现模块缺失。

### 浏览器运行时依赖

| 依赖 | 加载位置 | 用途 | 降级边界 |
|------|----------|------|----------|
| 阿里云 ARMS Browser SDK v2 | `https://sdk.rum.aliyuncs.com/v2/browser-sdk.js` | Web PV/UV、性能、Web Vitals、API、静态资源、JS/Console 错误和用户行为监控 | CDN 或上报端不可用时不得阻塞 neurun 页面与 API 业务 |

ARMS SDK 由 `src/web.py` 在 HTML 响应中统一注入，不加入 Python 包依赖，也不引入前端构建步骤。
上报目标固定为当前杭州地域 RUM Web v2 endpoint，链路追踪保持关闭。

---
