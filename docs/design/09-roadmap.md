# 设计方案 — 9. 实现路线图

> 属于 [设计方案索引](../design.md) · 版本 v3.0 · 2026-06-24

---

## 9. 实现路线图

| 阶段 | 内容 | 优先级 |
|------|------|:---:|
| **Phase 1** | 项目骨架：目录结构、pyproject.toml、.env.example、.gitignore、memory/ 各子目录初始化（含 daily/） | P0 |
| **Phase 2** | 认证模块：config.py + auth.py，实现环境变量登录 | P0 |
| **Phase 3** | 数据拉取：fetcher.py，对接活动列表 + 全部健康指标 | P0 |
| **Phase 4** | 存储模块：storage.py，初始化 LocalDB + SyncManager，封装查询接口 | P0 |
| **Phase 5** | CLI 基础命令：main.py，sync / daily / activities / health / status 命令 | P0 |
| **Phase 6** | 记忆核心：memory.py — MemoryStore 类、MemoryReader、MemoryWriter、MemoryValidator、MemoryLinker | P0 |
| **Phase 7** | 记忆生成器：**日报生成器**、运动摘要聚合、恢复摘要聚合、执行跟踪更新、异常检测 | P0 |
| **Phase 8** | 日报 CLI：`rundown daily` 命令，支持 --date / --format json / --no-ai，rich 美化终端输出 | P0 |
| **Phase 9** | 记忆 CLI：memory 子命令（list / show / summarize / goal / plan / case / profile / check / index） | P0 |
| **Phase 9.5** | HTML 日报渲染：render.py — Markdown + YAML → 静态 HTML，绿黑色风格，output/YYYY-MM-DD.html | P0 |
| **Phase 10** | MCP Server 核心：mcp 子命令，Resources 暴露（daily/latest、context/full）、Tools 暴露（查询+分析） | P0 |
| **Phase 11** | AI 教练对话：对话上下文注入、System Prompt 模板、AI 可写入洞察（add_coaching_insight）、OpenClaw 配置生成 | P0 |
| **Phase 12** | 记忆模板：各类型记忆的 Markdown 模板，`memory goal create` / `memory plan create` 交互式问答 | P1 |
| **Phase 13** | 导出功能：exporter.py，CSV / JSON 导出 | P1 |
| **Phase 14** | 定时任务：cron/launchd 配置模板，每日自动 sync + 生成日报 | P1 |
| **Phase 15** | 测试与文档：单元测试（聚合算法、schema 校验）、README 完善、memory/README.md 使用指南、OpenClaw 配置文档 | P1 |

---
