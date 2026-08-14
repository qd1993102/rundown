# Bug: 专业训练方案 Skill 被 DeepSeek JSON 协议拒绝

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: major
- **影响范围**: 专业训练方案草稿、Coach Skill DeepSeek 运行时、降级原因展示

## 现象

训练页生成或重新计算方案草稿时显示“当前没有使用在线 AI 专业推理”，降级原因为“DeepSeek 返回 400”。请求虽然安全降级为确定性方案，但贡献 Skill 和主方案 Skill 都没有完成在线推理。

## 根因

Coach Skill 客户端启用了 DeepSeek `response_format=json_object`，但贡献 Skill 的 Prompt 没有包含供应商要求的小写 `json` 输出指令，导致第一个 `assess-goal-feasibility` 请求被 DeepSeek 以 400 拒绝。客户端同时丢弃了标准错误类型，只保留 HTTP 状态码。修复 400 后，在线回放进一步发现模型会返回不符合 `SkillFinding` 的字段名、字符串星期以及不满足精确周量算术的候选，原实现会对这些可机械规范化的差异直接整体降级。

## 修复方案

在 `OpenAICompatibleSkillModel` 统一追加包含小写 `json`、目标输出模型和精确字段类型示例的供应商协议，不要求十二个业务 Skill 重复维护；非 200 响应只保留脱敏状态与错误类型。方案 Skill 补充 Validator 对应的负荷边界，新增受限的确定性候选规范化：只向下夹紧首周容量、周增长和恢复周跑量，并机械对齐周课程距离与长距离占比，随后仍必须通过完整 Validator 才能标记为在线 AI 方案。

## 相关文件

- [src/coach_runtime/runner.py](../../src/coach_runtime/runner.py) — 统一 JSON Output 协议与脱敏错误分类
- [src/training_planning.py](../../src/training_planning.py) — 增加候选负荷与距离算术规范化
- [prompts/skills/draft-training-scheme/SKILL.md](../../prompts/skills/draft-training-scheme/SKILL.md) — 明确安全边界与星期编码
- [tests/test_coach_runtime.py](../../tests/test_coach_runtime.py) — 覆盖贡献 Skill JSON 指令和安全错误信息
- [tests/test_training_planning.py](../../tests/test_training_planning.py) — 覆盖规范化后仍通过独立 Validator
- [docs/product/training-experience.md](../product/training-experience.md) — 明确在线 AI 成功与降级验收
- [docs/design/training-system.md](../design/training-system.md) — 记录方案规范化和校验顺序
- [docs/design/ai-coaching.md](../design/ai-coaching.md) — 记录 DeepSeek 模型适配合同

## 验证

回归测试先复现贡献 Skill Prompt 缺少 `json` 和错误信息仅保留 400，再验证修复后请求包含精确 `SkillFinding` / `TrainingSchemeCandidate` 合同且不会传播上游正文。使用完全虚构的运动者与赛事数据在线调用 DeepSeek，完整通过 `assess-goal-feasibility`、`draft-training-scheme`、确定性规范化和 Validator，返回 `generation_mode=skill`、`fallback_reason=None`。提交前完整 `pytest` 必须零失败。
