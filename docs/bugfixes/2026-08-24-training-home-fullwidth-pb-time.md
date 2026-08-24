# Bug: PB 使用全角冒号导致训练首页返回 503

- **发现日期**: 2026-08-24
- **修复日期**: 2026-08-24
- **严重程度**: major
- **影响范围**: 训练首页、个人最佳成绩清洗、配速区间计算

## 现象

用户请求 `GET /api/training/home` 时返回 `503 training_service_unavailable`。用户 SQLite 数据库完整，服务日志记录的底层异常类型为 `ValueError`。

## 根因

用户的 `fitness-assessment.md` 中个人最佳成绩使用了中文全角冒号：

```yaml
personal_bests:
  5k:
    time: 18：50
```

`pace_zones._parse_time()` 只按半角冒号 `:` 分割；全角冒号未被分割后又被传入 `float()`，触发：

```text
ValueError: could not convert string to float: '18：50'
```

训练首页计算配速校准档案时未能完成，Web 路由将该异常统一返回为 503。

## 修复方案

- PB 时间解析前将 Unicode 全角冒号 `：` 规范化为半角冒号 `:`，兼容 `18：50` 和 `1：30：00`。
- 对无法解析、NaN、无穷或非正数的单条 PB 忽略并追加 warning，其他有效 PB 和近期活动仍可继续用于配速计算。
- 不修改用户原始档案，保留其原始输入以便后续用户编辑时查看。

## 相关文件

- [src/pace_zones.py](../../src/pace_zones.py) — PB 时间规范化与无效成绩容错
- [tests/test_pace_zones.py](../../tests/test_pace_zones.py) — 新增全角冒号 PB 回归测试
- [docs/design/04-modules.md](../design/04-modules.md) — 同步 PB 清洗契约

## 验证

- 新增测试在修复前稳定复现 `ValueError`。
- `pytest -q tests/test_pace_zones.py`：26 个测试全部通过。
- 使用问题用户 `rd_34253af73114774e930e05015b6fc37d` 的原始数据库和 memory 重放 `TrainingService.home()`，成功返回训练首页读模型。
- 完成服务重启后，使用该用户 Cookie 重新请求 HTTP 接口，确认返回 200。
