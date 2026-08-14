# Bug: 草稿 AI 返回成功但被完成标准校验误判失败

- **发现日期**: 2026-08-08
- **修复日期**: 2026-08-08
- **严重程度**: major
- **影响范围**: 训练方案草稿生成、Workout Steps v2 Normalizer 和 Validator

## 现象

Provider 返回 HTTP 200 且 JSON 可解析，但草稿任务最终失败，日志提示 `Workout Steps v2 缺少结构化完成标准`。同一输入再次由用户重试时复现。

## 根因

模型返回了带有 `structure_version=2` 和步骤的半成品处方，但没有返回 `completion_criteria`。Normalizer 对已有步骤只保留了非结构化完成字段，Validator 随后仍要求完成标准必须是字典并包含完整分类，因此把可由本地确定性逻辑修复的字段当成了硬失败。

## 修复方案

Prompt 明确要求：如果 AI 输出完整处方，必须一次性包含完成标准；否则省略整个处方字段。Normalizer 对半成品 v2 步骤补齐最小的 `completed`、`partially_completed`、`stopped` 分类并记录调整；Validator 继续保留结构、剂量、强度和安全边界等不可执行底线，但不再单独阻断可修复的完成分类缺失。

## 相关文件

- [src/training_planning.py](../../src/training_planning.py) — 处方补全和最小校验
- [prompts/skills/draft-training-scheme/SKILL.md](../../prompts/skills/draft-training-scheme/SKILL.md) — 单次 AI 输出合同
- [docs/product/training-experience.md](../product/training-experience.md) — 训练草稿行为边界
- [docs/design/training-system.md](../design/training-system.md) — Normalizer/Validator 技术合同

## 验证

覆盖“带 v2 步骤但缺少完成标准”的回归测试，并运行训练规划、草稿运行时和 Web 回归测试；随后重启 8080 进行健康检查。
