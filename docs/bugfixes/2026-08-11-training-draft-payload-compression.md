# Bug: 训练草稿 AI 载荷包含重复事实和运行诊断元数据

- **发现日期**: 2026-08-11
- **修复日期**: 2026-08-11
- **严重程度**: minor
- **影响范围**: 训练框架和当前/下一周课表的 AI 调用延迟与输入 token

## 现象

训练草稿的两个 AI 阶段会收到任务标识、调用序号、trace 等运行信息；近期课表还同时携带近期日摘要和同一批活动列表，增加输入长度和上下文噪声。

## 根因

通用 `CoachRunContext.to_payload()` 面向所有 Skill 保留完整运行上下文，阶段级事实投影没有进一步区分“决策事实”和“运行诊断”。近期课表的短期活动窗口与最近日摘要也存在重复。

## 修复方案

- 为训练框架和近期课表增加模型载荷白名单，仅发送 `facts`；请求 ID、调用序号、策略和 trace 继续留在本地运行上下文与日志。
- 近期课表只保留最近两个已结束自然日的会话级摘要、短期窗口元数据、恢复状态和缺口，不重复发送活动明细列表。
- 保留目标、周量目标、最近训练衔接、恢复和安全边界；载荷压缩不改变 Schema 或本地安全校验。

## 相关文件

- [src/coach_runtime/context.py](../../src/coach_runtime/context.py) — 增加阶段级模型载荷投影。
- [src/coach_runtime/runner.py](../../src/coach_runtime/runner.py) — 两个草稿 Skill 使用压缩载荷。
- [src/training_planning.py](../../src/training_planning.py) — 压缩近期课表当前状态。
- [docs/product/training-experience.md](../product/training-experience.md) — 记录最小事实集边界。
- [docs/design/training-system.md](../design/training-system.md) — 增加设计与验收约束。

## 验证

`.venv/bin/pytest -q tests/test_coach_runtime.py tests/test_training_planning.py` 通过；`git diff --check` 通过。新增测试验证运行诊断不进入模型载荷、近期活动不重复发送，以及最近两天的质量课/长距离信息仍保留。
