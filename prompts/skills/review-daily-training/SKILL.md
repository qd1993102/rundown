---
name: review-daily-training
description: 汇总指定日期的训练、恢复、当前方案执行和可操作建议。用于生成日报或评估当天，不创建或修改长期方案。
---

# 日报复盘与单日训练摘要

1. 先确认活动数据状态，再汇总训练和恢复事实。
2. 按报告日期解析当时生效的方案版本；不得读取后来启用的当前方案评价历史训练。
3. 直接使用 `daily_facts` 中已有的计划执行、恢复、负荷和训练识别；不得重新计算领域指标。
   `daily_facts.training_day_summary` 是共享单日摘要：只引用其中的运动特点和结构化 evidence，不重新识别活动类型或编造证据数值。
   `daily_facts.session_analyses[].session_summary` 含 `session-summary-v2` 的确定性事实：整体水平（距离/用时、配速、爬升/下降、步频、训练效果）、强度分布（配速/心率带、分位数）、配速节奏（前后半程、CV、复合结构）。用它们解释“这课怎么跑、练到了什么”，但不重新计算数值；`effect.estimated=true` 的训练效果是本地估算，必须表述为估算并注明依据。
   `daily_facts.session_analyses[].structure_classification` 是确定性训练结构判定（变速/间歇/节奏/有氧/混合 + 快慢交替组数 + 置信度 + 成分占比 + 每组配速明细 `work_recovery_groups`）：把它作为“教练观察”的重要来源，例如“本课识别为间歇结构（4 组快慢交替，置信 100%）”，并**必须**在 `work_recovery_groups` 非空时逐组列出全部组的快/慢配速与心率（格式固定为“第 N 组快 X'/km(hrNN) → 慢 Y'/km(hrNN)”，按组序号升序，缺心率时省略 hr 并注明），不得只写组数不列明细；只引用其中的 `label`/`alternations`/`confidence`/`composition`/`work_recovery_groups`，不重新判定结构，也不把低置信判定表述为确定结论。
4. 没有生效方案时保留训练事实，但计划执行输出 `not_applicable`，不评价未执行或偏离。
5. 分开观察、建议、警告和结论，并说明证据。
6. 需要调整时只建议进入提案流程。
7. 观察（`observations`）输出**完整的教练观察**，内容固定由三块组成、按序输出，全部写成连贯的自然语言段落，每条观察是一句话或一小段，不做数值罗列、不写无依据断言：
   - **运动概要**：当天练了什么——距离/用时、平均配速、课型结构与整体水平；只陈述事实，不展开强度细节。课型结构必须引用 `structure_classification` 的 `label`/`alternations`，且当 `work_recovery_groups` 非空时，**必须在此处逐组列出每组快/慢配速与心率**（如“间歇结构（4 组快慢交替）：第1组快 3'44"/km(hr147) → 慢 5'04"/km(hr139)；第2组快 3'44"/km(hr149) → 慢 5'07"/km(hr141)；第3组…第4组…”），全部组按序展示，不得省略或只写组数。同时引用 `daily_facts.session_analyses[].session_summary.effect` 给出**训练效果解释**（如有氧/无氧 TE 值 + 一句话强度含义，如“训练效果有氧 5.0，高强度刺激对体能提升作用明显”）；`effect.estimated=true` 时必须标注“估算，依据心率/配速”，TE 不可得时如实写“训练效果不可得”，不得编造数值。
   - **强度分布的解释与分析**：引用 `daily_facts.session_analyses[].session_summary.intensity`（`pace_bands_pct`/`hr_bands_pct`/`basis`）与 `pace_profile`（分位数/CV/前后半程）解释配速与心率分布落在什么强度（如“配速以 3'40"–4'20" 为主、心率 67% 时间在 130–145 区间”），并结合 `session_summary.structure` 的 `avg_cadence`/`avg_stride` 说明节奏与效率（如“平均步频 184 spm、步幅 1.1m”）；缺失维度（无心率 → `basis=pace`、无分段、无步频/步幅）必须如实说明，不得补造。
   - **恢复分析**：当日恢复状态——把睡眠、HRV/静息心率、身体电量与恢复评分合并为一段连贯表达，说明今天恢复是否支持训练（如"昨夜睡眠 6.3h 是主要限制，但身体电量与恢复评分支持正常训练"）。
   - **近 7 天负荷与恢复分析**：趋势维度，不与当日恢复重复——引用 `daily_facts.training_load`（`acute_load_7d`/`chronic_load_28d`/`acwr`/`acwr_status`）与 `daily_facts.trends_7d` 及近一周睡眠/HRV 方向，说明近期负荷与恢复的相对位置和走向（如"ACWR 1.15 处于最优区间；但睡眠近 3 天走低、HRV 也在回落，疲劳在累积"）；缺 28 天基准时只报急性负荷并说明无法判断相对位置，无证据时不编造 ACWR 或趋势结论。
   四块之间同一事实只出现一次，不与 `conclusion`/`warnings`/`recommendations` 重复强调；低置信结构判定不得表述为确定结论。
8. `daily_facts.athlete_context` 是训练域固化的只读能力背景：可持续周跑量参考、长距离参考、近期参考配速、历史已证能力与中断背景、负荷边界、置信度与 `facts_cutoff`。用它解释当日/近期负荷相对个人可持续容量的相对位置；不得重新计算能力画像。能力字段为 0、null 或缺失，`status=unavailable`，或 `confidence=low` 时，相关能力/负荷相对结论保持未知，不得编造能力数值。
9. 同一次输出同时填充 `session_summary`、`session_characteristics`、`training_effect`、`recovery_response`、`capability_signals`、`next_day_constraints`、结构化 `evidence`、`gaps` 和 `confidence`。`session_characteristics` 最多 5 项，每项必须包含中文结论、1–3 个关键数据和 evidence 来源；不要只输出“有氧/节奏”标签。
10. 严格输出 `CoachInsight`：包含 `plan_execution`、`conclusion`、`observations`、`recommendations`、`warnings` 和恒为 `false` 的 `plan_adjusted`。

完成条件：结论只引用报告日期事实；数据缺失明确保留未知；没有生效方案时 `plan_execution.comparison_status=not_applicable`；输出不包含工具请求或训练方案写入；含间歇/变速/混合课型且 `work_recovery_groups` 非空时，`observations` 的运动概要必须逐组列出每组配速明细（不允许只写“N 组快慢交替”而不列每组配速）；`session_summary.effect` 可用时运动概要必须给出训练效果解释，估算值带“估算，依据心率/配速”标注。
