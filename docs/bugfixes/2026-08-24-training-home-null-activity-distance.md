# Bug: 无距离活动导致训练首页返回 503

- **发现日期**: 2026-08-24
- **修复日期**: 2026-08-24
- **严重程度**: major
- **影响范围**: 训练首页、个人配速区间近期活动降级计算

## 现象

已登录用户请求 `GET /api/training/home` 时返回 `503 training_service_unavailable`。用户 SQLite 数据库完整，但近期活动中存在力量训练等合法的无距离活动，`distance_meters` 为 `NULL`。

底层异常为：

```text
TypeError: '>=' not supported between instances of 'NoneType' and 'int'
```

## 根因

`pace_zones.fallback_from_recent_avg()` 使用 `dict.get("distance_meters", 0)` 和 `dict.get("duration_seconds", 0)` 过滤近期活动。该写法只在字段不存在时返回 `0`；字段存在但值为 `None` 时仍返回 `None`，随后直接与数字比较而抛出 `TypeError`。训练首页在 PB 无有效近期基线时会执行该降级路径，因此整个读接口被异常中断。

## 修复方案

在近期平均配速降级路径中统一把距离和时长转换为有限浮点数；`None`、非法字符串、NaN 和无穷值按无效数据处理。只有距离不少于 3 km 且时长大于 0 的活动参与最近三次平均配速计算，力量训练等无距离活动直接忽略，不修改源数据库。

## 相关文件

- [src/pace_zones.py](../../src/pace_zones.py) — 安全规范化距离和时长后再筛选、计算近期平均配速
- [tests/test_pace_zones.py](../../tests/test_pace_zones.py) — 新增距离或时长缺失活动的回归测试
- [docs/design/04-modules.md](../design/04-modules.md) — 明确近期配速降级的缺失值处理合同

## 验证

- 先运行新增测试，确认旧实现稳定复现 `TypeError`。
- 修复后运行 `pytest -q tests/test_pace_zones.py`，25 个测试全部通过。
- 使用触发问题的本地用户数据重新构建 `TrainingService` 并调用 `home()`，确认训练首页读模型可正常生成。
- 训练相关回归集 `pytest -q tests/test_pace_zones.py tests/test_training.py tests/test_training_service_factory.py`：105 passed、1 skipped。
- 完整 `pytest -q`：681 passed、1 skipped、1 xfailed；另有 1 个开始排查前已存在于未提交分享卡改动中的长文本渲染断言失败（`tests/test_web.py::test_share_card_renders_long_coach_text_without_ellipsis`），与本修复路径无关，未覆盖该并行改动。
