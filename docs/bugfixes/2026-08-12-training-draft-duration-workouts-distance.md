# Bug: 定时跑距离未估算导致周量全摊给距离型课程、长距离被放大

- **发现日期**: 2026-08-12
- **修复日期**: 2026-08-12
- **严重程度**: major
- **影响范围**: 训练方案草稿周量对齐（`TrainingPlanReconciler.reconcile`）、草稿课表可执行性、前端课程距离展示

## 现象

真实草稿（全马 230，MrGrass）出现离谱课表：周日长距离 **43.5km**、轻松跑 **21.7km**，
超过 `max_session_minutes=180` 的硬约束（按个人配速约需 4-4.6 小时）。同时课表里
`interval 60min`、`tempo 50min` 等**定时跑没有距离**，且这些课程的距离被"算到其他日子里"——
周目标跑量全部摊给有距离的课程，导致距离型课程被等比放大。

## 根因

`TrainingPlanReconciler.reconcile` 做周量对齐时，把无 `distance_km` 的定时跑课程按 0 计算：

```python
distances.append(max(0.0, float(workout.get("distance_km") or 0)))  # 定时跑 = 0
```

周目标 `target_km` 与距离型课程累计的差值通过 `factor = target / current` 等比放大
**只有距离的课程**（定时跑贡献 0），于是长距离、轻松跑被放大到不合理数值，
而定时跑实际会跑出的距离（时长 × 个人配速）完全没有参与累加。

## 修复方案

`reconcile(candidate, facts)` 现在接收 `facts` 提取个人近期配速（`athlete_profile.recent_running_pace_sec_per_km`），
对无距离的定时跑课程先用 `_estimate_duration_distance` 按**个人配速 + 强度区间比例**估算距离：

- 各 Z 区相对 Z2 配速的比例：Z1=1.10 / Z2=1.00 / Z3=0.93 / Z4=0.85 / Z5=0.78；
- 质量课整课含热身/恢复段，按课型折扣：interval/fartlek/repetition=0.85、tempo/threshold=0.90；
- 估算值写入 `workout.distance_km` 并标记 `distance_estimated=true`，调整说明记录
  "定时跑 x 分钟按个人配速估算约 y km"。

估算后再做统一周量对齐，定时跑与距离型课程一起参与缩放，周目标不再全摊给距离型课程。
`TrainingSchemeCandidateNormalizer.normalize` 调用处同步传入 `facts`。

## 相关文件

- [src/training_planning.py](../../src/training_planning.py) — `_estimate_duration_distance`、`reconcile` 定时跑估算
- [tests/test_training_planning.py](../../tests/test_training_planning.py) — 复现测试：定时跑估距离并参与对齐、距离型课程不被过度放大
- [docs/design/training-system.md](../design/training-system.md) — Normalizer 定时跑估算规则

## 验证

- 新增测试 `test_reconcile_estimates_duration_only_workouts_in_weekly_total`：修复前失败
  （定时跑无估算距离、距离型被放大超 0.5×target），修复后通过。
- `tests/test_training_planning.py` / `test_training_pace.py` / `test_training.py` /
  `test_coach_runtime.py` 共 129 项全部通过（修复中顺带恢复 `_personal_pace_reference`
  配速范围门禁，未引入回归）。
- 真实草稿端到端：长距离 43.5→34.5km、轻松跑 21.7→13.8km、定时跑有估算距离参与周量对齐。

## 遗留

长距离 34.5km 仍超过 `max_session_minutes=180` 推算上限（约 3 小时），
需后续增加**单课剂量门禁**（距离×配速 vs 单次时长约束）彻底收敛；本次先修复距离累加错误。
