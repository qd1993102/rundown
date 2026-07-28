# Bug: 移动端日报列表评分被挤到第二行

- **发现日期**: 2026-07-28
- **修复日期**: 2026-07-28
- **严重程度**: minor
- **影响范围**: Web 日报列表移动端卡片布局

## 现象

在 320px 等手机视口下，日报列表卡片中的 `report-scores` 整体换到训练摘要下方，日期、摘要和
睡眠/恢复评分无法保持同一行，卡片内容高度从约 44px 增加到约 109px。

## 根因

移动端重构时为 `.report-scores` 设置了 `grid-column:1/-1`，明确要求评分区跨越整行，因此 Grid
只能把它放入第二行。评分并非内容溢出后自然换行，而是布局合同本身错误。

## 修复方案

日报列表改用“44px 日期 / `minmax(0,1fr)` 摘要 / 固定评分”三列 Grid。训练标题和洞察在中间
列内使用省略号；评分区使用 `min-width:max-content`、`flex-wrap:nowrap`，每项至少 36px，
将有限宽度优先分配给不可拆分的评分。桌面断点继续使用更宽的日期列、评分和间距。

## 相关文件

- [web/templates/reports.html](../../web/templates/reports.html) — 修正移动端列表三列布局和溢出规则
- [tests/test_web.py](../../tests/test_web.py) — 增加评分单行布局契约测试
- [docs/design/04-modules.md](../design/04-modules.md) — 补充日报列表布局合同
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 同步移动端响应式设计

## 验证

- 回归测试先在旧样式下失败，再在修复后通过。
- 320×720 浏览器视口下，日期、摘要、睡眠和恢复评分保持同一行，评分分别保持 36px 最小宽度。
- 卡片内容高度约 44px，页面 `scrollWidth` 等于 320px，无横向滚动。
