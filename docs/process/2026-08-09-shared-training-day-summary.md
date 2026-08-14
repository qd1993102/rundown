# 共享单日训练事实摘要

- **日期**: 2026-08-09
- **类型**: feature / architecture

## 背景与动机

训练草稿、日报和周报都需要判断单次运动的类型、特点、跑量、恢复和数据完整性。若每个入口各自解析原始活动或重复调用 AI，事实口径会漂移，也会增加草稿生成耗时。用户确认采用共享摘要串联三类入口，并要求草稿基于上一完整自然周而不是动态周。

## 方案选择

采用“确定性事实层 + 可选语义层”的轻量方案：

- `TrainingDaySummaryBuilder` 负责按用户自然日去重、归一化距离/时长、训练主类型、长距离特征、睡眠/恢复/负荷和覆盖状态，并保留 evidence。
- `summarize-training-day` 作为可选缓存 Skill，只在结构化事实之上补充难以枚举的运动特点；不生成日报、不调整方案，也不阻断主链路。
- 草稿使用前 4–8 个完整自然周聚合判断能力基线，最近两天只用于排课衔接；周报聚合目标自然周的单日摘要；日报读取当天摘要。
- 不增加页面、设置或第二次串行 AI 调用；语义摘要失败时继续展示确定性事实。

## 实现步骤

1. 新增共享摘要构造与自然周聚合模块及 schema/测试。
2. 将摘要写入日报记忆 front matter，并向教练事实包、草稿 `PlanningFactPack` 和周复盘事实注入。
3. 注册 `summarize-training-day` Skill，补充日报、周报和草稿的消费边界。
4. 更新产品/技术设计、运营验收口径和变更日志。

## 遇到的问题与解决

- 草稿 Prompt 长度超过既有轻量门槛：保留关键历史摘要约束，压缩重复说明，并继续由运行时注入 entry review 约束。
- 周复盘初次接入时遗漏局部 `daily_summaries` 变量：在复盘入口按同一事实窗口重建后再聚合，避免改变既有内部返回契约。
- 空活动不再直接表示休息：只有目标日期的同步状态明确为 `rest`/`confirmed_rest` 才标记确认休息，未知覆盖保持 unknown。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [训练体验](../product/training-experience.md)、[日报](../product/daily-report.md)
- Design: [训练系统](../design/training-system.md)、[AI 教练](../design/ai-coaching.md)
- Operations: [共享摘要上线验收](../operations/shared-daily-summary-acceptance.md)
