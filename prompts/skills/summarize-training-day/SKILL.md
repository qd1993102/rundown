---
name: summarize-training-day
description: 基于单日结构化运动事实提炼可复用的运动特点和训练含义，不生成日报或修改训练方案。
---

# 单日训练摘要

只消费运行时提供的 `training_day_fact`，不得读取日报自然语言、调用工具或改写原始数据。所有面向用户的文字使用简体中文。

提炼单次运动的结构和特点，例如热身、主体、恢复、间歇、节奏、变速、长距离、后程掉速、心率漂移或地形负荷。特点不要求全部命中枚举，但必须保留可核对的结构化 `evidence`；没有事实依据时返回未知并降低置信度。

不要输出今天是否适合训练、明天如何安排、是否推进阶段或任何方案调整建议；这些由日报、周报和统一方案审阅分别负责。不得编造距离、时长、配速、心率、睡眠或负荷数值。

严格输出 `TrainingDaySemanticSummary` JSON：

- `status`：`ok` 或 `insufficient_data`；
- `summary`：一句简体中文总结；
- `observed_features`：特点数组，每项包含 `feature_code`、`label`、`confidence`、`evidence`；
- `training_implications`：仅描述事实对训练理解的含义，不给出下一课安排；
- `uncertainties`：数据缺口或无法确认的部分。

每项 `evidence` 尽量包含 `metric`、`value`、`unit`、`scope`、`reference`、`delta_percent` 和 `source` 中可用字段。结构化摘要不可用时不阻断日报、周报或草稿，它们继续使用代码事实。
