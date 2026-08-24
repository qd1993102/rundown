# Bug: 近期反馈校准遇到 NULL 距离时崩溃

- **发现日期**: 2026-08-25
- **修复日期**: 2026-08-25
- **严重程度**: major
- **影响范围**: 个人配速区间的近期训练反馈校准、训练首页配速刷新

## 现象

用户 `activities` 表中有 9 条活动的 `distance_meters` 为 `NULL`（包括
`activity_id` 631496731、629413450 等）。近期活动被传入
`calibrate_from_recent_activities()` 后，遍历活动时抛出：

```text
TypeError: '>' not supported between instances of 'NoneType' and 'int'
```

训练首页或配速区间刷新因此可能在近期活动校准阶段失败。

## 根因

`calibrate_from_recent_activities()` 的心率偏高统计和实际配速采样都使用
`dict.get("distance_meters", 0)` / `dict.get("duration_seconds", 0)`。当字段存在但数据库值为
`NULL` 时，`dict.get()` 返回 `None` 而不是默认值，随后 `d > 0` 或 `t > 0` 直接触发类型错误。

## 修复方案

新增共享的 `_finite_number()` 数值规范化 helper，将 `None`、无法转换的值、NaN 和无穷值转换为无效数值
`0.0`。近期反馈校准的心率统计和配速采样均先完成规范化，再只处理距离和时长为正数的活动；近期平均配速 fallback
复用同一 helper，保持所有近期活动计算路径的缺失值处理一致，不修改源活动数据。

## 相关文件

- [src/pace_zones.py](../../src/pace_zones.py) — 统一活动距离/时长的有限数值校验
- [tests/test_pace_zones.py](../../tests/test_pace_zones.py) — 新增 `NULL` 距离校准回归测试
- [docs/design/04-modules.md](../design/04-modules.md) — 同步近期校准与 fallback 的缺失值处理合同

## 验证

- 回归测试在修复前稳定复现 `TypeError`。
- 修复后运行 `pytest -q tests/test_pace_zones.py`：27 passed。
- 运行完整 `pytest -q`：676 passed、1 xfailed。
