# 训练排课衔接与周期顺序

- **日期**: 2026-08-09
- **类型**: feature / refactor

## 背景与动机

训练方案的长期基线应继续使用上一完整自然周，但近期两天的高负荷训练会直接影响下一节课的安全衔接；同时，周期阶段需要保持可解释的时间顺序。

## 方案选择

将短期训练作为独立事实输入，仅做次日排课保护，不把动态周混入周量基线。阶段顺序采用非阻断审阅，避免把训练建议模块变成过多硬规则；发现倒置时提示用户重新生成或确认。

## 实现步骤

1. 在 TrainingService 读取最近两个已结束自然日并构造 `short_term_training`。
2. 在 Normalizer 和确定性兜底排课中避免高负荷次日强度，并优先将长距离安排到周末可训练日。
3. 在 Skill Prompt、产品文档和技术设计中同步约束。
4. 增加排课衔接、周末长距离和周期顺序审阅测试。

## 遇到的问题与解决

阶段顺序不适合作为 Validator 硬失败条件，因为用户已明确训练计划是推荐而非卡死标准。因此只记录 `PERIODIZATION_ORDER` attention，不改变 AI 候选原文。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Bugfix: [docs/bugfixes/2026-08-09-training-schedule-transition-and-periodization.md](../bugfixes/2026-08-09-training-schedule-transition-and-periodization.md)
