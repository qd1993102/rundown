# Bug: 草稿生成失败 "goal_demand_summary 必须是对象"（可选映射字段过度严格）

- **发现日期**: 2026-08-15
- **修复日期**: 2026-08-15
- **严重程度**: major
- **影响范围**: 训练草稿生成（`build-training-framework` 阶段）；提示"AI 返回内容无法解析为可执行结构，已完成阶段可复用；请点击重新生成"

## 现象

生成训练草稿失败，日志：

```
AI output rejected: skill=build-training-framework output_model=TrainingFramework
  reason=goal_demand_summary 必须是对象
training scheme stage failed: stage=training_framework failure_type=schema_invalid
```

AI 返回了可解析 JSON（`json_decoded` 成功），但 `goal_demand_summary` 字段
偶发返回字符串/null/数字，`validate_training_framework` 的 `_mapping` 严格校验抛错，
整个草稿失败（已完成阶段可复用，需点击重新生成，可能重复失败）。

## 根因

`validate_training_framework`（`src/coach_runtime/schemas.py`）对可选映射字段
`ability_summary` / `goal_demand_summary` / `weekly_principles` 使用严格的
`_mapping`（非对象即抛 `SkillSchemaError`）。这些字段是辅助性说明，模型偶发
类型不规范，属于**可确定修复的小瑕疵**——按产品原则（"不能因为可确定修复的
问题让整份草稿失败"）应宽容降级，而不是失败。同文件其他位置（如
`validate_scheme_audit`）已用 `isinstance(dict) else {}` 宽容处理，此处不一致。

## 修复方案

`validate_training_framework` 中三个可选映射字段改为宽容降级（非 dict → `{}`），
与已有降级模式一致；核心结构（feasibility / periodization / load_progression）
保持严格校验不变。

## 相关文件

- [src/coach_runtime/schemas.py](../../src/coach_runtime/schemas.py) — 可选映射字段宽容降级
- [tests/test_coach_runtime.py](../../tests/test_coach_runtime.py) — 新增容错测试

## 验证

- 新增测试：`goal_demand_summary="字符串"`、`ability_summary=None`、
  `weekly_principles=123` 时校验通过并降级为 `{}`；核心结构（空 periodization）仍失败；
- pytest 全量零失败（544 项）。
