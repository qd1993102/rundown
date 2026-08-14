# Bug: 固定不可训练时间（fixed_unavailable）未参与课表排期

- **发现日期**: 2026-08-12
- **修复日期**: 2026-08-12
- **严重程度**: major
- **影响范围**: 训练方案草稿课表排期（确定性兜底 + AI 近两周课表）、现实约束生效性

## 现象

用户填写"固定不可训练时间"（如"周三晚不可训练"）后，课表仍在周三安排训练课。
`constraints.fixed_unavailable` 只在请求中存储，从未被排期逻辑消费；确定性排期
（`_week_workouts`）与 AI 排课只使用 `available_days`。

## 根因

`fixed_unavailable` 是自由文本（如"周三晚"），排期层没有解析与排除逻辑：
- `_week_workouts` 用 `constraints.get("available_days")` 直接排课；
- near-term Skill 的约束上下文包含该文本，但无确定性兜底，AI 不一定遵守。

## 修复方案

1. `training_planning.py` 新增 `_parse_fixed_unavailable(text)`：把自由文本解析为
   排除的星期几集合（"周三晚"→{2}、"每周二、周四"→{1,3}、"周末"→{5,6}）；
2. `_effective_available_days(constraints)`：`available_days` 扣除
   `fixed_unavailable_days` 后的有效排课日（全部被排除时退回原列表，避免空排期）；
3. `training.py` 构造 `constraints` 时注入 `fixed_unavailable_days`（数字列表），
   同时传给 AI 的 `near_term_context.constraints`；
4. `build-near-term-schedule/SKILL.md` 增加必须规则：避开
   `constraints.fixed_unavailable_days` 列出的星期几。

## 相关文件

- [src/training_planning.py](../../src/training_planning.py) — 解析与有效排课日
- [src/training.py](../../src/training.py) — constraints 注入 `fixed_unavailable_days`
- [prompts/skills/build-near-term-schedule/SKILL.md](../../prompts/skills/build-near-term-schedule/SKILL.md) — 避开不可训练日规则
- [tests/test_training_planning.py](../../tests/test_training_planning.py) — 解析与排期避开测试

## 验证

- 单测：`_parse_fixed_unavailable`（周三晚/每周二周四/周末/空）与 `_effective_available_days`
  （排除 + 全排除退回）通过；确定性排期 `_week_workouts` 不生成固定不可训练日的课程。
- 真实草稿端到端（设置"周三晚不可训练"）：`fixed_unavailable_days=[2]`，
  首次 AI 课表在周三排了间歇课，被可执行性审计抓为 critical（与固定不可用时间冲突）→
  打回重生成后周三变为休息，约束生效。
