# Bug: 重规划结论"长距离最长仅 20km"与实时能力事实不符

- **发现日期**: 2026-08-15
- **修复日期**: 2026-08-15
- **严重程度**: major
- **影响范围**: 方案重规划（`revise-training-scheme`）、草稿/重规划的可行性结论与长距离安全上限

## 现象

方案重规划后，`data_basis` 结论为"近期周跑量约100km，**但长距离最长仅20km**，远低于马拉松专项需求"。
但用户 08-15 刚完成 30km 长距离（日报明确记录"当日完成 30km 长距离跑"），结论明显与事实不符。

## 根因（两层）

1. **baseline 长距离用错窗口**：`training_service_factory.load_setup` 的 `longest_distance_km` 只统计
   **上一完整自然周**（08-03~08-09，最长 20km）。08-15 的 30km 在本周，被窗口漏掉。
   而能力侧 `AthleteBaselineBuilder` 用 **28 天**窗口——两处口径不一致，导致：
   - AI 可行性结论低估长距离能力；
   - `_confirm_long_run_exceed` 安全上限按 20km 计算（min(20×1.15, 31.6)=23km），限制过严。
2. **active_scheme 注入旧结论**：重规划把当前方案**全文**（180KB/45K tokens，含旧 `data_basis`
   "长距离仅20km"）注入给 AI，AI 在重规划时**复述了旧方案的结论**，即使 baseline 已修复也不生效。

## 修复方案

1. `load_setup`：`longest_distance_km` 改用**近 28 天窗口**（与 `AthleteBaselineBuilder` 一致），
   周量（`previous_week_km`/`distance_km`/`activity_count`）仍取上一完整自然周（产品规则：
   起始周量不引用当前不完整周）；抽出 `_running_distances` 辅助函数。
2. `training.py`：新增 `_active_scheme_structure`——重规划注入当前方案时**只保留结构字段**
   （goal/constraints/periodization/weekly_pattern/first_four_weeks/load_progression/
   weekly_mileage_target/current_phase/activation 等），剔除旧结论字段
   （data_basis/feasibility/review/adjustments/audit/planning_trace/risk_flags/
   baseline_snapshot/user_explanation），AI 无法复述旧结论，载荷同步大幅减小。

## 相关文件

- [src/training_service_factory.py](../../src/training_service_factory.py) — 长距离 28 天窗口
- [src/training.py](../../src/training.py) — active_scheme 结构裁剪
- [tests/test_training_service_factory.py](../../tests/test_training_service_factory.py) — 窗口行为测试
- [tests/test_training.py](../../tests/test_training.py) — 结构裁剪测试

## 验证

- 单元：周量=20km（上一周）、长距离=30km（28 天）；结构裁剪剔除 9 个结论字段、保留结构字段；
- 真实重规划请求（HTTP 201）：
  - baseline.longest = **30.0**；
  - data_basis 结论从"长距离最长仅20km，远低于专项需求"变为"**近期周跑量101.8km，长距离30km，具备较高有氧基础**"；
- pytest 全量零失败。
