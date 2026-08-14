# Bug: available_days 外的不可训练日仍被排课

- **发现日期**: 2026-08-12
- **修复日期**: 2026-08-12
- **严重程度**: critical
- **影响范围**: 训练方案草稿课表（AI near-term 排课 + 确定性投影）、不可训练日约束强制

## 现象

用户设置可训练日 `[0,1,2,3,5,6]`（周五不可训练），草稿课表仍在周五（08-14）安排轻松跑。
此前修复只覆盖 `fixed_unavailable` 文本（"周三晚"），`available_days` 本身未被确定性层强制
——AI near-term Skill 收到 constraints 但未遵守，也没有确定性兜底。

## 根因

1. `build-near-term-schedule` SKILL 只约束"避开 fixed_unavailable_days"，未明确
   "weekday 必须在 available_days 内"；
2. `_project_draft_near_term_schedule` 投影只做日期映射与配速回填，**不校验 weekday
   是否可训练**；AI 在不可训练日排课直接进入草稿。

## 修复方案

1. **确定性投影强制**：`_project_draft_near_term_schedule` 对非休息课程校验
   `_is_trainable_weekday`（可用 `available_days` 减去 `fixed_unavailable_days`）；
   不可训练日的课程**强制转为休息**（`type=rest`、title="休息"、purpose 标注"该日不可训练"）；
   无 `available_days` 约束时不限制（兼容旧草稿）。
2. **SKILL 规则**：明确"weekday 必须在 `constraints.available_days` 内，可训练日外的
   课程会被确定性层强制转为休息"。

## 相关文件

- [src/training.py](../../src/training.py) — `_is_trainable_weekday`、投影强制校验
- [prompts/skills/build-near-term-schedule/SKILL.md](../../prompts/skills/build-near-term-schedule/SKILL.md) — available_days 规则
- [tests/test_training.py](../../tests/test_training.py) — 不可训练日转休息测试

## 验证

- 单测：available_days 外（周五）与 fixed_unavailable_days 的课程都转休息；无约束时不限制。
- 完整 `pytest` 438 通过（3 个预存环境失败不变）。
