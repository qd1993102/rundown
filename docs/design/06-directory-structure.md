# 设计方案 — 6. 目录结构

> 属于 [设计方案索引](../design.md) · 版本 v3.0 · 2026-06-24

---

## 6. 目录结构

```mermaid
graph TD
    subgraph ROOT["garmin/"]
        DOCS["docs/<br/>design/ (模块化)"]
        SRC["src/"]
        MEMORY_DIR["memory/<br/>记忆库 (Markdown + YAML FM)"]
        DATA["data/<br/>garmin_data.db"]
        ENV_FILES[".env.example / .env<br/>.gitignore"]
        PYPROJECT["pyproject.toml"]
        README_MD["README.md"]
    end

    subgraph SRC_FILES["src/"]
        MAIN["main.py — CLI 入口"]
        CONFIG["config.py — 环境变量"]
        AUTH_M["auth.py — 认证"]
        FETCHER["fetcher.py — 数据拉取"]
        STORAGE_M["storage.py — 存储"]
        MEMORY_M["memory.py — 记忆存储<br/>含 DailyReportGenerator"]
        RENDER["render.py — HTML 静态页面渲染 ⭐"]
        EXPORTER["exporter.py — 导出"]
        MCP_MODULE["mcp_server.py — MCP Server<br/>OpenClaw 集成"]
    end

    subgraph MEMORY_TREE["memory/ 目录结构"]
        AUTO_DIR["auto/<br/>├── daily/ ⭐<br/>├── summaries/<br/>├── recovery/<br/>└── execution/"]
        PROFILE_DIR["profile/<br/>竞技档案"]
        GOALS_DIR["goals/<br/>目标管理"]
        PLANS_DIR["plans/<br/>训练计划"]
        COACHING_DIR["coaching/<br/>├── preferences.md<br/>├── cases/<br/>├── insights/<br/>└── ai-insights/ ⭐"]
    end

    SRC --> SRC_FILES
    MEMORY_DIR --> MEMORY_TREE
```

---
