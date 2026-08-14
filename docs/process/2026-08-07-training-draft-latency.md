# 训练方案草稿延迟收敛

- **日期**: 2026-08-07
- **类型**: refactor

## 背景与动机

草稿生成时要求模型展开四周内所有课程的 Workout Steps v2，输出正文很大，Provider 读取时间容易触及单次读超时。用户希望先看到新的配速与课程效果，训练流程保持简单，不引入后台任务、自动重试或新页面。

## 方案选择

保留一次 AI 请求和现有五步确认流程。AI 只生成周期、当前周与下一周的紧凑课程骨架、训练目的、刺激和强度意图；本地 `TrainingSchemeCandidateNormalizer` 负责延展后两周，并生成确定性 Workout Steps v2、完成标准和 `blocks` 投影。完整 AI 处方仍可被兼容读取，但不再作为草稿生成的必填输出。草稿与改期请求的 Provider 单次读取上限统一为 45 秒，输出上限收敛为 2600 tokens，并限制课程字段短句化。

## 实现步骤

1. 更新产品方案、技术设计和两个训练 Skill 的输出边界。
2. 放宽 `TrainingSchemeCandidate` Schema，允许非休息课程缺少 `training_prescription`，并把 AI 输出窗口收敛到两周；完整处方和历史四周候选继续兼容。
3. 让 Normalizer 从紧凑课程意图确定性延展后两周并补全 v2 处方，保留 AI 已提供的完整处方。
4. 更新 Provider 示例、输出规则、超时和 token 配置，并补充回归测试。

## 遇到的问题与解决

旧测试把“模型必须输出完整步骤”当作合同。测试改为验证紧凑候选可通过 Schema，且进入 Normalizer 后仍通过最终 Validator；历史完整 v2 候选继续保持兼容。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
