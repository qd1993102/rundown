---
name: review-training-week
description: 复盘自然周内的实际活动、恢复、能力变化和数据完整度。用于无生效方案或周中事实解释，不形成方案推进决策。
---

# 自然周复盘

1. 始终只累计目标自然周内的实际活动、恢复、能力趋势和数据完整度；`natural_week_actual` 是必需事实。
2. 优先复用 `training_day_summary` 的单日事实和 evidence，不重新从日报正文识别课型或补造数字；它缺失时仍基于 `natural_week_actual` 完成确定性复盘。
   `training_day_summary.daily_summaries[].sessions[].structure_classification` 与 `observed_features` 的 `structure_*` 特征是确定性训练结构判定（变速/间歇/节奏/有氧/混合 + 快慢交替组数 + 置信度）：用它总结本周训练结构（如“本周含 1 次间歇结构（4 组交替）+ 2 次有氧”），只引用 `label`/`alternations`/`confidence`，不重新判定结构。
3. `active_scheme` 和 `natural_week_progress` 只是可选的事实比较上下文。缺少方案不是数据不足，不得拒绝复盘。
4. 无方案时总结运动量、结构、连续性、恢复、风险和下一周一般建议；不得生成计划完成率或计划偏离。
5. 周中有方案时可以解释截至当前的执行事实，但不得形成阶段推进、减量或重规划决策。
6. 输出 `SkillFinding`，只包含事实结论、证据、不确定性、风险和一般建议，不输出 `plan_review`。

完成条件：周级结论说明实际活动、恢复和数据完整性；只有存在方案比较上下文时才说明计划执行；输出不包含方案决策或写入意图。
