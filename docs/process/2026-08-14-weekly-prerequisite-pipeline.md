# 周复盘前置事实与质量课流水线

- **日期**: 2026-08-14
- **类型**: architecture

## 背景与动机

周复盘需要在生成前自动准备每日事实和日级分析，同时避免要求用户手工生成七份日报或放大为七次在线 AI。

## 方案选择

复用统一 `TrainingDayFact`，增加确定性 `TrainingDayAnalysis` 与封口的 `WeeklyPrerequisiteManifest`。质量课
从结构化事实按统一门槛提取。逐日阶段零在线 AI，周级最多一次 AI；AI 失败时仍保存确定性周事实。

## 实现步骤

1. 将空白状态条和关闭详情正文从布局中移除。
2. 为单日事实增加稳定分析版本、覆盖、证据、gaps 和质量课候选。
3. 周复盘逐日准备事实与分析，封口版本清单后聚合。
4. 把质量课确定性摘要写入周报快照和周级事实包。
5. 增加单元、模板与浏览器回归检查。

## 遇到的问题与解决

已有活动级分类并不总有可靠分段，因此质量课门槛同时要求置信度和配速/心率主证据；数量门禁失败时只保留
强度模式，可靠分段字段显式写 gap。工作区已有大量未提交改动，本次只做增量修改，不覆盖其他文件历史。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
- Bugfix: [docs/bugfixes/2026-08-14-weekly-prerequisites-quality-sessions.md](../bugfixes/2026-08-14-weekly-prerequisites-quality-sessions.md)
