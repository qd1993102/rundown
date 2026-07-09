# 设计方案 — 7. 依赖清单

> 属于 [设计方案索引](../design.md) · 版本 v3.0 · 2026-06-24

---

## 7. 依赖清单

```toml
[project]
dependencies = [
    "garmy[all]>=1.0.0",       # 核心: Garmin API + LocalDB + MCP
    "python-dotenv>=1.0.0",    # .env 文件加载
    "pyyaml>=6.0",             # YAML Front Matter 解析
    "rich>=13.0.0",            # 终端美化输出（表格、进度条）
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

---
