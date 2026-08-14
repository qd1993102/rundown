---
name: revise-training-scheme
description: 根据连续执行偏差、长期停训、目标或现实约束变化重规划赛事方案。用于方案级变化，不处理单次局部约束。
---

# 重规划赛事方案

这是中文用户旅程：所有面向用户的自然语言字段（阶段名称与目的、课程标题与目的、训练重点、可行性摘要、证据、假设、不确定性、风险、解释和审阅意见）必须使用简体中文。`feasible`、`challenging`、`hold` 等机器枚举、字段名、Z1–Z5 和单位保持原合同。

1. 一次性消费原方案、`ExecutionSummary`、恢复快照、更新后的 `PlanningFactPack` 与 `TrainingLoadEnvelope`；这些结构已经包含本地计算的目标可行性、计划执行和恢复事实，不得要求再次调用贡献 Skill。
2. 说明触发证据，重新评估目标可行性和剩余周期。
3. 输出阶段、周规划目标（`target_km`，可选 `target_load`）和当前周/下一周的前后差异，保留历史版本；后两周由运行时确定性延展。课程累计与周目标不一致时仍完整输出课程，由本地 reconciler 对齐或按课程累计值结算并记录 `adjustments`，不要因此拒绝草稿。
4. 区分保持赛事、延期、调整成绩或退出备赛路线。
5. 只创建方案级提案，不直接发布新版本。
6. 延续 `draft-training-scheme` 的五级主体强度、个体事实优先和安全门禁：Z1 恢复、Z2 轻松有氧、Z3 稳态/专项耐力、Z4 阈值、Z5 间歇/高强度；极化、金字塔或阈值型分布不构成固定模板；目标成绩和通用配速表不得生成个人配速或容量。
7. 轻松/恢复课的距离与总时长同时存在时，必须与 `athlete_profile.recent_running_pace_sec_per_km` 做隐含配速一致性检查；快于参照 8% 以上时降低距离、延长时长或只保留单一硬目标。重规划仍按“当前可持续能力与近期表现 → 当下状态 → 目标导向的小幅推进 → 恢复与安全修正 → ACWR 次级风险校验”的顺序判断；ACWR 高不能单独压过能力和稳定表现，只有恢复或表现同步恶化时才允许进一步保守。疼痛、恢复异常或数据不足只允许降级或提出待确认方案。
8. 在同一次输出中审阅新候选；周期、负荷、恢复、关键课间隔、长距离比例和配速适配属于非阻断建议。一般问题写入 `review.status=attention` 和 `review.items`，明确疼痛或异常恢复风险才填写 `review.safety_hold`，由用户最终确认。

9. 排课衔接只看最近两天：长跑或质量课的次日不安排强度，长距离优先放在可训练周末；完整周期阶段保持基础/适应→专项强度→高峰→减量的时间顺序。

严格输出与 `draft-training-scheme` 相同的紧凑 `TrainingSchemeCandidate` JSON：顶层包含
`feasibility`、`periodization`、`load_progression`、`first_two_weeks`、
`assumptions`、`uncertainties`、`risk_flags` 和 `user_explanation`。
另需包含 `review`，其字段为 `status`、`items` 和 `safety_hold`；没有问题时 `items` 为空数组。
`first_two_weeks` 必须恰好两项，分别代表当前周和下一周；每周包含 `week`、
`target_km`、可选 `target_load`、`focus`、`recovery_week` 和 `workouts`；每个课程必须包含
`weekday`、`title`、`type`、`purpose`、`duration_minutes`、`distance_km`、
`intensity`、`stimuli`、`intensity_intent`、`alternatives`、
`adjustment_triggers` 与 `is_key`。不要输出完整 `training_prescription`。输出仍只是候选方案，不得声称已经生效。

`weekday` 使用周一=0 至周日=6 的相对星期槽位，只表达周内位置和课程间隔；不要输出具体 ISO `date`。运行时会根据方案级 `effective_from` 进行确定性日期投影，临时日期变化仍走 Local Schedule Adjustment，不由模型改写长期方案日期。

每个非休息课程必须包含课程骨架、刺激和简短强度意图；不要输出完整 Workout Steps v2 `training_prescription`，也不要只用模糊自由文本表达关键课。运行时会确定性生成 `structure_version=2`、顺序 `steps`、重复组、work/recovery 子步骤、分段剂量、完成标准和调整规则；个人数字目标仍只能来自输入事实。

保持输出紧凑：课程字段使用短句；每节课最多一个替代方案和一个调整触发条件；`intensity_intent` 只写 `zone`、`preferred_metric` 和 `fallback_feel`，不要在课程中写方法解释或长段落。

另需返回 `review` 与 `entry_review`。`review` 包含 `status`、`items`、`safety_hold`；
`entry_review` 包含 `recommended_entry_phase`、`decision`（`hold` 或 `advance`）、
`confidence`、`evidence`、`adjustments`。审阅比较当前能力、训练连续性、训练成熟度、近期有氧基础、恢复状态与目标要求；可以建议跳过基础阶段，但仍保留适应周，且必须由用户在现有确认入口确认后才生效。审阅只是建议，不因一般质量问题阻断候选，也不生成第二套方案。

完成信号：当前周和下一周的候选完整、前后差异可解释、所有风险与不确定性保留，并且结果仍需通过外部 Normalizer 和 Validator 延展为四周结果；缺少任一必需事实时返回不确定性或保守路线，不补造个人能力、配速或恢复结论。

审阅不会因为一般计划质量问题而省略候选或返回失败，也不要生成第二套方案。
