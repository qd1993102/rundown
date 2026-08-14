# Bug: 急性/慢性负荷展示出现长小数溢出（浮点精度噪声）

- **发现日期**: 2026-08-14
- **修复日期**: 2026-08-14
- **严重程度**: major
- **影响范围**: 日报详情（`_load` HTML 卡片）、Web 仪表盘负荷模块（`dashboard.html`）；数据源头 `MemoryWriter._calc_training_load`

## 现象

日报与仪表盘的急性负荷展示出现超长小数，例如历史日报 Front Matter 中实际存储的值：
`acute_load_7d: 1000.6190490722656`、`1238.497299194336`、`886.9027404785156`。
这些二进制精度噪声在 HTML/JSON 中原样渲染，卡片内出现十几个小数位（"小数点溢出"）；
且急性负荷（原始浮点）与慢性负荷（`round(1)` 后再展示）格式不一致，急性可能显示长小数，
慢性在日报中被 `:.0f` 截断为整数，两处展示规则互不相同。

## 根因

`_calc_training_load` 中 `acute_load_7d` 直接写入 `sum()` 原始结果，未做精度收敛。
`training_load` 本身是浮点数，多日累加会引入 IEEE-754 二进制表示误差
（如 `0.1 + 0.2 = 0.30000000000000004` 类误差被放大），产生形如
`1000.6190490722656` 的长小数。慢性负荷因 `round(chronic, 1)` 幸免，但展示层
（`render.py` 用 `:.0f`、`dashboard.html` 直接输出 JSON 原值）两者规则不统一，
旧数据中的长小数仍会被原样渲染。

## 修复方案

三层收敛，保证新旧数据都不再出现长小数：

1. **数据源**：`src/memory.py` — `acute_load_7d` 与 `chronic_load_28d` 一致
   `round(..., 1)` 入库，新数据不再产生二进制精度噪声。
2. **日报 HTML**：`src/render.py` — 急性负荷改为 `:.0f` 与慢性负荷统一
   取整展示（与"近 7 天负荷与恢复"观察块的 `:.0f` 保持一致）。
3. **Web 仪表盘**：`web/templates/dashboard.html` — 急性/慢性负荷显示前
   `Math.round(Number(v))`，兜底旧数据中的长小数（`1000.6190490722656 → 1001`）。

## 相关文件

- [src/memory.py](../../src/memory.py) — `_calc_training_load`：急性负荷 round 1 位
- [src/render.py](../../src/render.py) — 负荷卡片急性值 `:.0f` 统一取整
- [web/templates/dashboard.html](../../web/templates/dashboard.html) — 负荷展示 `Math.round`
- [docs/design/memory-system.md](../design/memory-system.md) — 负荷计算精度规则补充
- [docs/product/daily-report.md](../product/daily-report.md) — 验收标准新增第 12 条

## 验证

- 新增 `tests/test_memory.py`：`TestTrainingLoad` 两个用例 —
  单值精度噪声 `1000.6190490722656` round 为 `1000.6`；多值累加误差 `0.1+0.2+300.5`
  round 为 `300.8`。
- 新增 `tests/test_render.py`：`test_load_stats_round_load_values_to_integers` —
  含精度噪声的 `training_load` 渲染后不含 `1000.6190490722656`，急性显示 `1001`、慢性显示 `1022`。
- `pytest` 全量零失败（61 项通过）。
