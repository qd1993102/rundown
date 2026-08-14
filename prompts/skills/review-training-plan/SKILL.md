---
name: review-training-plan
description: 审阅训练草稿的起始阶段或完整自然周的执行事实。用于在同次草稿生成中应用安全调整，或为生效方案形成待用户确认的周末决策。
---

# 审阅训练方案

只基于运行时提供的结构化事实审阅方案，不重新计算周跑量、计划负荷、配速或恢复指标，
不直接激活草稿、推进阶段、修改课程或创建新版本。所有面向用户的自然语言使用简体中文。

## `entry_review`

草稿生成器在同一次 AI 请求中复用本模式；这不是第二次模型调用。

- 比较当前可持续能力、近期表现、训练连续性、训练成熟度、恢复状态和目标要求，选择适合的起始阶段。
- 判断顺序固定为：当前能力与近期表现 → 当下状态 → 目标导向的小幅拔高 → 恢复与安全修正 → ACWR 次级校验。
- 可以跳过不必要的基础阶段，但必须保留适应周，不直接采用更高阶段的峰值负荷。
- 完整周期按基础/适应→专项强度→高峰→减量推进；`advance` 只改变切入点，进入更高阶段后不得把基础期重新排到后面。只有长期中断后的 `replan` 才能重新建立基础。
- `advance`、`adjust` 或 `deload` 中可安全应用的建议必须已经体现在同次输出的最终候选中；不要输出第二套方案。
- `replan` 或 `safety_hold` 不得自动应用，只记录待确认建议。
- `entry_review` 必须包含 `decision`、`recommended_entry_phase`、`confidence`、`evidence`、`adjustments`、`applied`、`applied_adjustments` 和 `handoff`。

## `execution_review`

仅用于已经结束的完整自然周。输入包含实际周事实、趋势、数据质量、恢复、当前能力、
`PlanExecutionSummary`、`AdaptationSignal` 和生效方案。

- 同时解释本周发生了什么，以及方案下一步应如何处理。
- 只能选择 `hold`、`advance`、`adjust`、`deload` 或 `replan`。
- 数据覆盖不足、同步状态未知或事实不足时必须选择 `hold`，不得把缺失数据解释成未执行。
- 当前能力和真实状态的权重高于目标拔高；恢复和 ACWR 用于修正，不得由 ACWR 单独否定稳定表现。
- 输出只形成待确认建议；所有决策都必须保留 `requires_user_confirmation=true`。

严格输出一个 `TrainingPlanReview` JSON，包含：

- 周复盘展示字段：`skill`、`status`、`conclusion`、`evidence`、`uncertainties`、`risk_flags`、`recommendations`；
- 审阅字段：`review_stage`、`decision`、`scope`、`recommended_entry_phase`、`confidence`、`adjustments`、`safety_hold`、`user_explanation`；
- `handoff`：`status=review_pending`、`requires_user_confirmation=true`。

完成条件：输出只使用输入事实；决策属于固定五类；数据不足时为 `hold`；任何建议都没有直接改写生效方案。
