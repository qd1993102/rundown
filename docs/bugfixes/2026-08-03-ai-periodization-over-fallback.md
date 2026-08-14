# Bug: AI 阶段周数偏差导致整份方案不必要降级

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: major
- **影响范围**: 专业训练方案草稿、方案级重规划、训练页生成状态与风险建议

## 现象

DeepSeek 的目标可行性与方案 Skill 均返回 200，但 AI 候选的阶段周数之和与赛事剩余周数不一致时，整份候选被 Validator 拒绝，页面只展示规则兜底。用户无法继续看到 AI 已形成的目标风险、证据、能力缺口与可选路线。

## 根因

运行时使用二元 `generation_mode` 同时表达模型来源、校验结果和推荐结论，任何 Validator 异常都会进入相同兜底分支。已有 Normalizer 只能处理首四周负荷和课程距离算术，阶段周数精确求和仍由语言模型负责；即使阶段顺序、目的和减量结构完整，一个可机械修正的整数偏差也会丢弃全部 AI 结论。

## 修复方案

新增 AI 来源、校验状态、推荐状态、综合状态和修正清单五组字段，保留 `generation_mode` 兼容旧客户端。Normalizer 在阶段结构完整时按 AI 原始相对权重将总周期分配到真实赛事周数，每阶段至少一周，六周及以上周期的减量期至少两周；阶段顺序、名称和目的保持不变。页面分别展示 AI 原样通过、AI 已安全规范化、AI 风险待选择和规则兜底，并公开确定性修正、证据、缺口和可选路线。

## 相关文件

- [src/training_planning.py](../../src/training_planning.py) — 阶段规范化与分级结果元数据
- [src/training.py](../../src/training.py) — 草稿和重规划提案字段透传
- [web/templates/training.html](../../web/templates/training.html) — 分级状态、修正与路线展示
- [tests/test_training_planning.py](../../tests/test_training_planning.py) — 周期规范化与风险状态测试
- [tests/test_training.py](../../tests/test_training.py) — 草稿元数据测试
- [tests/test_web.py](../../tests/test_web.py) — API 与页面状态文案测试
- [docs/product/training-experience.md](../product/training-experience.md) — 分级结果产品规则
- [docs/design/training-system.md](../design/training-system.md) — Normalizer 算法与数据合同
- [docs/design/ai-coaching.md](../design/ai-coaching.md) — AI 来源、校验与推荐职责拆分

## 验证

单元测试覆盖 13 周 AI 阶段按原顺序规范化为 11 周、减量期不少于两周、调整 trace 和推荐状态保留；领域和 Web 聚焦测试通过。使用完全虚构的 11 周赛事数据在线调用 DeepSeek，返回 `generation_mode=skill`、`validation_status=repaired`、`decision_status=ai_risk_advisory`、无降级原因，总周期为 11 周。完整 `pytest` 必须零失败。
