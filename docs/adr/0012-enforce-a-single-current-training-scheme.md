# Enforce a single current training scheme

一个 neurun Account 同一时间只允许一个 active Training Goal 和一份可空的当前 Training Scheme；`draft`、`scheduled`、`active` 方案都占用唯一主线，目标模型不包含 paused/resume。形成目标后即不得创建第二个 Training Goal、第二份草稿或并行后续方案。目标改期先生成不进入当前主线的 Goal Rescheduling Preview，预览失败时旧目标与方案不变；用户确认有效预览后，才原子更新同一目标、废弃并保留当前方案的历史事实，并提交唯一替代草稿。取消开始安排只返回同一方案预览；真正放弃时同时归档 active goal 与 current scheme，保留历史并释放主线。该约束放弃多目标、多草稿、暂停状态和无缝并行排期，以换取唯一的今日训练、清晰的执行归属和可审计的目标改期语义。
