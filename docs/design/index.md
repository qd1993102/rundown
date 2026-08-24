# 设计方案 — 文档索引

> 版本: v3.2 · 日期: 2026-08-03 · 基于 [garmy](https://github.com/bes-dev/garmy) v1.0.0

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
| [ai-coaching.md](ai-coaching.md) | AI 教练工作流设计 — 六条显式旅程、结构化事实、模型调用和安全边界 |
| [training-system.md](training-system.md) | 报告建议落地的训练系统 — 训练处方、周计划、逐段 Workout Steps v2、个体化配速校准 v1、反馈、调整提案、版本与活动匹配 |
| [summary-extraction.md](summary-extraction.md) | 运动摘要提炼 — 单函数粒度自适应（L0/L1/L2）、版本化 schema、毫秒级实测、证据接入训练内容识别与报告 AI |
| [frontend-design-system.md](frontend-design-system.md) | 前端设计系统 — 三主题 token、组件库、签名元素、动效框架与审计报告 |
| [share-card.md](share-card.md) | 分享卡浏览器 Canvas 生成 — 白名单 ViewModel、接口兼容、状态机、迁移与测试 |
| [user-data-archive.md](user-data-archive.md) | 独立脚本生成单用户脱敏 TAR — allowlist、昵称消歧、manifest、完整性校验与原子发布 |
| [contact-community.md](contact-community.md) | 联系入口与可更新群二维码 — 统一注入、权限、响应式交互与运维替换 |
| [13-sae-deployment.md](13-sae-deployment.md) | §13 Web 应用部署方案 — 多用户、数据隔离、API、国内轻量/ECS |
| [14-overseas-deployment.md](14-overseas-deployment.md) | §14 海外部署 — VPS/域名/Cloudflare 渠道选型、架构与验收（未实施） |

## 规划与备忘

| 文档 | 内容 |
|------|------|
| [09-roadmap.md](09-roadmap.md) | §9 实现路线图 — 分阶段交付计划 |
| [12-multi-platform.md](12-multi-platform.md) | §12 多平台数据源架构 — Garmin/Coros/Huawei 已实现；Strava OAuth 活动同步 Proposed |
| [10-risks.md](10-risks.md) | §10 风险与注意事项 |
| [11-garmy-migration.md](11-garmy-migration.md) | §11 garmy 2.0 适配备忘 — API 差异、Schema、实现决策 |

---

> **维护规则**: 代码变更后，根据变更范围更新对应的子文档（而非单个大文件）。
> 产品行为、用户流程和验收标准由 [产品文档索引](../product/index.md) 中的原始用户旅程文档维护。
> 详见 [CLAUDE.md](../../CLAUDE.md) — Documentation Sync Rule。
