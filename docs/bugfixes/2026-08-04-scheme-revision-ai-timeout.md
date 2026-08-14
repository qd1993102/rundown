# Bug: 方案级重规划容易在在线 AI 阶段超时

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 方案级重规划、Coach Skill 调用链、账户级 AI 任务占位

## 现象

用户生成方案级重规划后得到“在线 AI 重规划未完成：AI 服务响应超时”。系统返回的候选经过安全校验，但只能标记为确定性兜底，无法获得在线 AI 的专业重规划结果。

## 根因

`propose_scheme_revision()` 为取得 ExecutionSummary 先调用完整 `review_week()`，间接触发一次周复盘模型请求；随后 `revise-training-scheme` 又串行运行目标可行性、计划执行、恢复三个贡献 Skill，最后才生成主候选。一次用户操作最坏包含五次串行模型调用，任一前置调用超时都会使整个重规划降级。与此同时，复杂四周候选与简短 finding 共用 20 秒单次读取上限，无法适配较长的首字节等待和输出间隔。

## 修复方案

- 重规划通过 `review_week(include_ai=False)` 只读取本地确定性周执行事实；
- `revise-training-scheme` 直接消费已有目标、能力、执行、恢复和原方案事实，不再运行贡献 Skill；
- 保留 90 秒端到端预算，将该复杂主 Skill 的单次读取上限调整为 45 秒；
- 继续使用 Normalizer、Validator 和显式确定性兜底，超时不会改变当前生效方案或伪装成 AI 推荐。

## 相关文件

- [src/training.py](../../src/training.py) — 重规划读取无 AI 的本地周事实
- [src/training_planning.py](../../src/training_planning.py) — 重规划收敛为一次主 Skill 调用
- [src/coach_runtime/runner.py](../../src/coach_runtime/runner.py) — 为复杂重规划设置独立读取预算
- [prompts/skills/revise-training-scheme/SKILL.md](../../prompts/skills/revise-training-scheme/SKILL.md) — 明确一次性消费本地结构化事实
- [docs/design/ai-coaching.md](../design/ai-coaching.md) — 同步调用链与超时合同

## 验证

- 单元测试证明重规划不会调用周复盘 Skill；
- Planner 测试证明 `revise-training-scheme` 不再选择贡献 Skill；
- 模型适配层测试证明重规划使用 45 秒读取上限且仍受 90 秒端到端预算约束；
- 完整 `pytest` 零失败。
