# 训练处方与体系 Skill 约束

- **日期**: 2026-08-04
- **类型**: architecture

## 背景与动机

训练页把 RPE 直接暴露为主要强度语言，今日训练没有完整执行要求，本周课程也被压缩为同质化的 `easy / quality / long` 摘要。用户要求课程能够结合不同训练体系形成间歇、节奏、长距离和变速等多样训练，同时不让自由文本绕过安全边界。

## 方案选择

采用“自由组合的课程处方 + 稳定的安全语义”而非固定课程库枚举。课程名称和训练块由 Coach Skill 在阶段、能力、恢复和约束下选择；`training_prescription.stimuli`、blocks、完成优先级和强度数据可用性构成可验证合同。`type` 仅保留为旧数据和既有负荷逻辑兼容字段。

体系知识进入 `draft-training-scheme`、`brief-training-session`、`evaluate-plan-execution` 和 `review-training-week` 的约束，不能直接写入方案。确定性 Validator 继续负责高负荷间隔、医疗限制、时长、长距离比例、距离—时长—个人配速一致性和伪精确配速/心率拦截。

## 实现步骤

1. 在领域词汇、产品真相源和技术设计中定义 Training Prescription。
2. 在 `training_planning.py` 增加旧方案兼容补全和规则兜底处方生成。
3. 更新 Coach Skill 指令与 runtime registry 版本。
4. 训练页展示今日完整处方、本周强度摘要与按需展开的课程结构。
5. 日报复用相同处方比较优先完成目标和实际训练事实。
6. 增加五级主体强度参考层与 `pacing_guard`：Z1 恢复、Z2 轻松有氧、Z3 稳态/专项耐力、Z4 阈值、Z5 间歇/高强度；只以近期个人跑步事实或已测阈值落到数值，拒绝把固定配速、目标成绩或单一学派比例当作用户处方。

## 遇到的问题与解决

训练前说明一开始把高负荷下“明显疲劳则降级”的提示排在课程块之后，超过页面展示上限。调整为安全提示先于替代方案，确保高风险用户首先看到降级边界。

另发现旧方案可将 13.7 km / 60 分钟的 Easy Run 拆为 40 分钟主体，隐含配速与用户近期事实冲突。修复不静默修改已生效方案：读模型以 `pacing_guard` 显示待修正并收紧本次执行；后续候选由 Normalizer/Validator 拒绝。时间容量不再以统一 5 或 6 分钟每公里换算。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
- Bugfix: [docs/bugfixes/2026-08-04-training-prescription-rpe-only.md](../bugfixes/2026-08-04-training-prescription-rpe-only.md), [docs/bugfixes/2026-08-04-easy-run-dose-consistency.md](../bugfixes/2026-08-04-easy-run-dose-consistency.md)
