# AI 审阅约束与最小训练方案校验

- **日期**: 2026-08-08
- **类型**: architecture

## 背景与动机

训练方案用于执行推荐和建议，现实训练会受到改期、漏训和状态变化影响。原 `TrainingSchemeValidator` 把周期、跑量增长、恢复周、配速和课程间隔等计划质量规则全部作为硬失败条件，导致可执行但不完美的草稿无法进入预览。

## 方案选择

保留代码对 JSON、字段、Workout Steps v2 和极端执行风险的最小底线校验；将周期、负荷、恢复、配速适配和目标取舍沉淀为同一次 AI 生成中的 `review` 约束。一般审阅项不阻断草稿，明确风险以 `safety_hold` 返回并由用户确认。为了控制 Provider 延迟，不增加第二次 AI 审阅请求，也不新增页面。

## 实现步骤

1. 扩展 TrainingSchemeCandidate 合同，兼容 `review.status/items/safety_hold`。
2. 精简 `TrainingSchemeValidator`，移除计划质量类硬失败。
3. 更新草稿和重规划 Skill Prompt，要求同次输出非阻断审阅。
4. 更新产品/技术设计、单元测试和变更日志。

## 遇到的问题与解决

旧测试把配速不一致、周增长过快、周期不一致和医疗限制下的高强度都视为异常。测试改为验证这些情况仍能返回候选，同时结构损坏仍然失败。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
