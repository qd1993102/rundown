# 设计方案 — 文档索引

> 版本: v3.0 · 日期: 2026-06-24 · 基于 [garmy](https://github.com/bes-dev/garmy) v1.0.0

---

## 核心设计

| 文档 | 内容 |
|------|------|
| [01-project-goals.md](01-project-goals.md) | §1 项目目标 — 要做什么、解决什么问题 |
| [02-research.md](02-research.md) | §2 前置调研结论 — 同类库对比、技术选型依据 |
| [03-architecture.md](03-architecture.md) | §3 整体架构 — 分层设计、模块关系总览 |
| [04-modules.md](04-modules.md) | §4 模块设计 — 配置、认证、数据拉取、存储、记忆核心 |
| [05-data-flow.md](05-data-flow.md) | §5 数据流 — sync/daily 命令执行流程、Mermaid 图 |
| [06-directory-structure.md](06-directory-structure.md) | §6 目录结构 — 项目文件组织 |
| [07-dependencies.md](07-dependencies.md) | §7 依赖清单 — Python 包、系统依赖 |
| [08-design-decisions.md](08-design-decisions.md) | §8 关键设计决策 — ADR、技术权衡记录 |

## 专题设计

| 文档 | 内容 |
|------|------|
| [memory-system.md](memory-system.md) | 记忆系统设计 — 分类体系、模板规范、自动生成器、日报格式 |
| [ai-coaching.md](ai-coaching.md) | AI 教练对话设计 — System Prompt、对话原则、OpenClaw 配置、典型场景 |

## 规划与备忘

| 文档 | 内容 |
|------|------|
| [09-roadmap.md](09-roadmap.md) | §9 实现路线图 — 分阶段交付计划 |
| [12-multi-platform.md](12-multi-platform.md) | §12 多平台数据源架构 — Garmin/Coros Provider 设计 |
| [10-risks.md](10-risks.md) | §10 风险与注意事项 |
| [11-garmy-migration.md](11-garmy-migration.md) | §11 garmy 2.0 适配备忘 — API 差异、Schema、实现决策 |

---

> **维护规则**: 代码变更后，根据变更范围更新对应的子文档（而非单个大文件）。
> 详见 [CLAUDE.md](../../CLAUDE.md) — Documentation Sync Rule。
