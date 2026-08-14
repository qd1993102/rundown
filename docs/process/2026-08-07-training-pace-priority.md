# 训练配速优先级调整

- **日期**: 2026-08-07
- **类型**: feature

## 背景与动机

动态配速 v1 直接把高 ACWR 映射为当天配速放慢，容易让负荷指标压过运动者当前真实能力、近期执行表现和目标导向。用户确认配速决策应先尊重当前能力和当下状态，再允许目标小幅推进，最后由恢复与 ACWR 做安全修正。

## 方案选择

采用现有 `PaceCalibrationProfile` 和 `DailyPaceAdjustmentEngine`，不新增 Skill、页面或配置模型。固定决策顺序为：

1. 当前可持续能力与近期同类表现确定基础区间；
2. 当下状态决定是否允许目标导向的小幅推进；
3. 有生效目标且状态稳定时，连续低/中强度课程最多比基础区间快 1%，只生成当天只读覆盖；
4. 恢复异常、疼痛或课程冲突优先取消推进并降级；
5. ACWR 只作次级风险校验，高 ACWR 单独不再改变配速，只有与恢复或表现恶化同时出现时才进一步放慢。

Skill 负责让 AI 按同一顺序编排和解释，确定性运行时负责强制边界，避免模型输出绕过安全约束。基础配速区间仍需新增可靠事实重新校准，目标推进不写回 Profile 或 Scheme Version。

## 实现步骤

1. 更新训练产品真相源和技术设计，明确能力/状态优先及 ACWR 次级边界。
2. 更新 `draft-training-scheme` 和 `revise-training-scheme` 的方法学规则，不创建新的 Skill。
3. 扩展当天配速引擎接收目标上下文，加入有条件的 `progressed` 状态与 `goal_progression` 解释。
4. 移除高 ACWR 单独的 2% 放慢；只有恢复或近期表现恶化时才保守修正。
5. 增加目标推进、高 ACWR 稳定状态、高 ACWR 伴随恢复恶化和无目标兼容测试。

## 遇到的问题与解决

现有测试和旧产品描述把当天配速限定为“不快于基础区间”。本次将其收敛为“目标推进只允许当天只读、最多 1%，且必须有生效目标和稳定状态”；基础区间本身仍不被静默改写。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
- Skills: [draft-training-scheme](../../prompts/skills/draft-training-scheme/SKILL.md), [revise-training-scheme](../../prompts/skills/revise-training-scheme/SKILL.md)
