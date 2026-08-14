# 训练方案精简规则运行时收敛

- **日期**: 2026-08-06
- **类型**: feature / architecture

## 背景与动机

训练模块已经完成多轮 grill，需要把“单方案主线、周中介入、长期星期变化下周生效、疼痛安全指引、失败安全”和“不过度复杂”的结论落到现有领域服务、Web 和 MCP 合同中。

## 方案选择

- 保留现有 Training Goal、Training Scheme、Training Feedback、Adjustment Proposal 和 Scheme Version 主线，不增加伤病管理、Safety Hold 生命周期、暂停态或新设置页。
- 临时日期变化继续走局部提案；长期可训练日变化复用同一反馈入口，在同一 `plan_id` 下生成下周生效的版本。
- 活动只做同日单活动高置信度自动匹配；其他活动标记为 `unplanned`，不自动替代训练。
- AI 完整候选失败时保持当前状态，只允许用户手动重试；本地无 Provider 的开发环境继续保留已有保守建草稿能力，真实 Provider/Schema/Validator 失败不生成候选。

## 实现步骤

1. 在训练领域服务加入新目标门禁、目标改期预览/确认、排期取消退回草稿和长期星期变化版本生效边界。
2. 归一化训练反馈字段，保存疼痛当日安全指引，并让安全指引覆盖当天执行安排。
3. 收紧活动匹配与执行状态，移除 `substituted`，记录未分配活动标签。
4. 同步 Web/MCP 接口与单元测试。

## 遇到的问题与解决

- 现有 Provider 未配置时，测试和本地首次建方案依赖保守草稿；因此仅对真实 Provider、Schema、Normalizer 或 Validator 失败返回 `scheme_candidate_unavailable`，不把错误候选写入方案。
- 长期星期变化不能直接覆盖当前 active 方案；确认后写入 scheduled 版本，待下个自然周再提升，当前周安排不改写。
- 目标改期不能直接修改 active goal；先生成同一 `plan_id` 的替代草稿，确认后才废弃旧读模型并保留历史。

## 关联文档

- CHANGELOG: [../CHANGELOG.md](../CHANGELOG.md)
- Product: [../product/training-experience.md](../product/training-experience.md)
- Design: [../design/training-system.md](../design/training-system.md)
