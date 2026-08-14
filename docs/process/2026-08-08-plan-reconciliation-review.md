# 周目标结算与训练方案双阶段审阅

- **日期**: 2026-08-08
- **类型**: feature / architecture

## 背景与动机

训练草稿同时包含周规划目标和逐课课程。真实执行会与草稿存在偏差，因此“课程累计跑量/负荷不等于周目标”不应成为生成失败条件；同时，阶段是否适合当前能力不能只在生成瞬间判断，完整自然周结束后还需要结合执行和恢复事实复盘。

## 方案选择

- 采用本地 `TrainingPlanReconciler` 做算术一致性：可安全调整时缩放课程距离并记录 `adjustments`；无法安全调整时以课程累计值结算目标负荷并记录差异。
- 新增统一 `review-training-plan` 审阅合同。草稿阶段把 `entry_review` 规则拼入 `draft-training-scheme` 的同一次 AI 请求，先生成、再自审并把可安全修正反映到最终候选；完整自然周且存在生效方案时，直接用确定性周事实调用 `execution_review`，不再让 `review-training-week` 产生第二套 `plan_review` 决策。
- 无生效方案时继续由 `review-training-week` 解释实际运动、恢复和数据完整度；有生效方案时由 `review-training-plan(execution_review)` 在一次调用中同时输出周解释和待确认方案决策，避免串行两次在线推理。
- 审阅决策固定为 `hold | advance | adjust | deload | replan`。结果只进入待确认数据，用户确认后才改变下一周安排或方案版本；不确认则保持原方案。
- `TrainingSchemeValidator` 继续只保留可执行结构和安全底线，不把训练质量偏好变成硬阻断。

## 实现步骤

1. 增加周目标与课程累计的确定性 reconciler，并在 Normalizer 后执行。
2. 增加 `TrainingPlanReview` schema 和 `review-training-plan` Skill 注册；草稿运行时只组合 entry 约束，不发起第二次模型请求。
3. `review-training-week` 收敛为无方案/事实复盘，完整方案周由 `review-training-plan(execution_review)` 输出唯一方案决策并继续通过既有 `plan_review` 读模型字段兼容展示。
4. 在草稿持久化和周复盘读模型中透传审阅结果与确认 handoff。
5. 更新 draft/revise/review Skill 契约、产品真相源与技术设计。
6. 增加 Prompt 组合、Schema、单次调用和周复盘回归测试。

## 遇到的问题与解决

AI 可能只返回当前周和下一周，且周目标与课程累计存在四舍五入或规划偏差。Normalizer 先确定性延展四周，再由 reconciler 统一处理目标结算；这样不需要让模型输出完整处方，也不会因为算术偏差丢失课程。草稿 entry review 采用本地 Prompt 组合，完整方案周则绕过 `review-training-week` 的在线解释，直接把已整理事实交给 execution review，保证每条用户旅程各只有一次主 AI 调用。

## 关联文档

- 产品：[docs/product/training-experience.md](../product/training-experience.md)
- Design：[docs/design/training-system.md](../design/training-system.md)
- Changelog：[docs/CHANGELOG.md](../CHANGELOG.md)
- 运营验收：[docs/operations/review-training-plan-launch-acceptance.md](../operations/review-training-plan-launch-acceptance.md)
