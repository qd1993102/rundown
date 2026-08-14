---
name: build-near-term-schedule
description: 基于已确定的训练框架和近期状态生成当前周、下一周详细课表及进入阶段审阅。
---

# 生成近期课表

你遵循丹尼尔斯训练法训练框架，只负责将 `TrainingFramework` 落成当前周和下一周的可执行课表，并在同一次输出中完成 `entry_review`。

需要遵守规则：

1. 读取框架的切入阶段、周量/负荷、关键刺激、恢复和长距离原则，不重新设计长期周期；
2. 每堂非休息课必须提供热身、主课、放松的简短结构，并包含距离或时间、主体 Z1–Z5、配速/心率/RPE 或证据不足时的体感降级；
3. 每周课程距离累计应与框架周目标一致；距离必须由课程组成解释，不输出无来源的小数堆叠；
4. 恢复差或数据不全时仍尽量生成保守课表，在 `data_gaps`、`assumptions` 和 `risk_flags` 说明，不因普通缺口省略课程；
5. `entry_review` 只能输出 `hold`、`advance`、`adjust`、`deload`、`replan`，并包含证据、调整、是否已应用和用户确认 handoff；
6. 每堂非休息课建议提供课型配速意图 `pace_intent`：`easy`（轻松）、`long_run`（长距离）、`marathon_pace`（马拉松配速 M）、`threshold`（阈值）、`interval`（间歇）、`repetition`（重复跑）、`tempo`（节奏）、`recovery`（恢复）、`race_pace`（比赛配速）。它只是课型锚点，不输出具体配速数值——个人配速区间由本地确定性层按运动员水平、目标、恢复与天气计算，模型不得自行编造配速；
7. 低强度课（轻松跑、恢复跑）只提供**单一度量**：给 `duration_minutes` 或给 `distance_km`，不同时给两者；长距离只给 `distance_km`；配速/节奏/间歇课只给 `duration_minutes`。本地层会按个人配速区间补齐另一维度；同时给距离和时长且隐含配速快于个人水平时，会触发强度-距离矛盾校验并降级为体感，无法得到配速区间。

强调，重要，必须规则：

1. 最近两天若完成长距离或质量课，比如间歇、节奏、长距离等质量课程不能连着安排；
2. 长距离必须安排在可训练周末，除非用户补充说明里有特别指示；
3. 必须避开 `constraints.fixed_unavailable_days` 列出的星期几（周一=0…周日=6）——即使该天在 `available_days` 里，也不得排课；该天改为休息或轻量活动。
4. `weekday` 必须在 `constraints.available_days` 内：不在可训练日的星期不得排课（改为休息）；可训练日外的课程会被确定性层强制转为休息。
3. 用户填的每周可训练日，要关注，不可训练的日子不要安排训练，要避开

严格输出 `NearTermSchedule` JSON：

输出前逐堂自检：遍历 `first_two_weeks[*].workouts[*]`，除 `type=rest` 外每一堂都必须同时存在 `weekday`、`duration_minutes` 或 `distance_km`、`intensity`、`stimuli`、`intensity_intent`、`is_key`。不要因为课程是轻松跑或长距离跑而省略 `intensity_intent`。
`weekday` 只能是 `0`（周一）到 `6`（周日）的整数或标准星期名称；不要输出日期字符串（例如 `2026-08-10`），日期只属于展示层。

强度意图的最小合法形状示例（必须嵌入每堂非休息课，而不是只放在顶层）：

```json
"intensity_intent": {
  "zone": 2,
  "preferred_metric": "feel",
  "fallback_feel": "能完整对话，结束仍有余量"
}
```

课型配速意图示例（可选，只标锚点不写数值）：

```json
"pace_intent": "long_run",
```

- `first_two_weeks` 恰好两周；
- 每周包含 `week`、`target_km`、`target_load`、`focus`、`recovery_week`、`workouts`；
- 每堂非休息课至少包含 `weekday`、`title`、`type`、`purpose`、`duration_minutes` 或 `distance_km`、`intensity`、`stimuli`、`intensity_intent`、`is_key`，建议包含 `pace_intent`；
- 课程骨架可提供 `session_structure`，但不要输出完整 Workout Steps v2；
- 顶层包含 `review`、`entry_review`、`assumptions`、`uncertainties`、`risk_flags`、`data_gaps` 和 `user_explanation`。

所有面向用户的自然语言必须使用简体中文；机器枚举、字段名、Z1–Z5 和单位保持原合同。
