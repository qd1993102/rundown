# Bug: 训练页“查看本周进度”按钮字体偏大且在手机上与日期标题挤压

- **发现日期**: 2026-08-14
- **修复日期**: 2026-08-14
- **严重程度**: minor
- **影响范围**: 训练页（`web/templates/training.html`）“本周节奏”卡片的 `查看本周进度` 按钮；手机端窄屏布局

## 现象

手机（如 375px 宽）打开训练页，“本周节奏”卡片的日期区间
（`2026-08-11 — 2026-08-17`，h2 18px）与右上角“查看本周进度”按钮
挤在同一行互相挤压，按钮字体明显大于其他页面（报告页按钮 13px）。

## 根因

1. `training.html` 的通用 `.btn` 没有显式 `font-size`，`body` 也未设置
   字号，按钮继承浏览器默认 **16px**（`reports.html` 的 `.btn` 明确为 13px），
   导致训练页所有按钮字体偏大。
2. `.card-head` 是 `display:flex; justify-content:space-between` 且不换行，
   手机窄屏下长日期标题（18px）与 6 字按钮无法并存，发生挤压。

## 修复方案

- `.btn` 补 `font-size:13px;white-space:nowrap`，与报告页按钮一致，文字不换行；
- `@media(max-width:600px)` 下 `.card-head`、`.week-session-detail-head`
  增加 `flex-wrap:wrap`，窄屏时长标题与操作按钮自动换行，消除挤压；
  桌面端布局不受影响。

## 相关文件

- [web/templates/training.html](../../web/templates/training.html) — 按钮字号与窄屏换行
- [docs/product/training-experience.md](../product/training-experience.md) — 新增移动端适配决策

## 验证

- Playwright 无头浏览器在 375px / 393px 视口验证：按钮计算字号为 13px；
  按钮与“本周节奏”日期标题不再同一行（自动换行），无挤压。
- `pytest tests/test_web.py` 54 项零失败。
