# 深度分析、分享卡与设计系统整合

- **日期**: 2026-08-20
- **类型**: feature

## 背景与动机

本轮并行推进四条用户可见能力：日报跑步深度分析（有氧漂移、跑步经济性、疲劳代偿、
伤病风险等）、个人训练配速区间、日报/周复盘分享卡产品化，以及全站视觉与信息架构统一；
同时补齐账号自助修改密码，并修复日报 Web 空白等线上问题。

## 方案选择

- 跑步分析采用本地确定性引擎（`src/running_analysis/`）而非全部交给 AI：可复现、无在线成本、
  隐私友好，结果作为结构化 fact 输入 AI Coach Skill；
- 配速区间独立成 `src/pace_zones.py`，缓存写入 `fitness-assessment.md` front matter，
  按 7 天新鲜度惰性刷新、同步完成后强制刷新，「我的」页与训练 `pace_targets` 回填共用同一缓存；
- 分享卡采用 Chrome 全页截图 + 内容高度自适应，移除全局 `overflow:hidden` 字符串替换，
  避免固定 viewport 截断与样式误伤；
- 前端设计系统提取共享 CSS（tokens/reset/components/utilities）四件套，六页模板迁移，
  暗色主题重配色并统一品牌名为 neurun。

## 实现步骤

1. `running_analysis/` 10 个纯确定性模块 + 日报组装接入 + 73 个测试；
2. `pace_zones.py` 计算/缓存/「我的」页卡片 + 同步后刷新 + 新增测试；
3. `share_card.py` 分享卡生成、running-only 口径与缺陷修复 + Web/MCP 工具；
4. 前端设计系统审计与实现 + 同步页信息结构重构；
5. 登录用户自助修改密码（`/api/password` + profile 卡片）；
6. 修复 YAML `!!python/tuple` 序列化导致的日报 Web 空白；
7. 文档同步：产品（account/share-card）、设计（04-modules/summary-extraction/
   deterministic-running-analysis/frontend-design-system）、README、CHANGELOG、
   bugfix 记录与 ECS 还原操作手册。

## 遇到的问题与解决

- 日报 Web 空白：`yaml.dump()` 生成 `!!python/tuple` 标签而 `safe_load` 拒绝解析 →
  改 `safe_dump` + 递归 sanitize，读取端增加回退并重写受影响历史日报；
- 分享卡内容截断与样式破坏：固定 viewport 高度 + 全局字符串替换 → 高度自适应 + 移除替换；
- 配速区间无配套测试：补齐 `tests/test_pace_zones.py`（PB 清洗、心率/配速区间、
  近期校准、环境补偿、降级与缓存读写）。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- 产品: [account.md](../product/account.md)、[share-card.md](../product/share-card.md)
- 设计: [deterministic-running-analysis.md](../design/deterministic-running-analysis.md)、
  [frontend-design-system.md](../design/frontend-design-system.md)、
  [04-modules.md](../design/04-modules.md)
- Bugfix: [2026-08-18-share-card-bugs.md](../bugfixes/2026-08-18-share-card-bugs.md)、
  [2026-08-20-yaml-tuple-serialize.md](../bugfixes/2026-08-20-yaml-tuple-serialize.md)
- 操作手册: [ecs-user-data-restore.md](../operations/ecs-user-data-restore.md)
