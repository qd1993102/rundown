# Bug: 报告页隐藏元素仍占据空白布局

- **发现日期**: 2026-08-14
- **修复日期**: 2026-08-14
- **严重程度**: minor
- **影响范围**: 报告中心日报和周复盘页、已收起周复盘归档

## 现象

没有 AI 任务时，Tab 下仍出现无文字圆角状态条；收起周复盘详情后，正文子区块仍占据高度，形成多个空白模块。

## 根因

组件 CSS 的 `.report-status { display:grid }` 覆盖了原生 `hidden` 行为；`.weekly-review-body { display:grid }`
也让关闭的原生 `details` 正文继续参与布局。

## 修复方案

为 `hidden` 增加全局退出布局规则，并明确关闭的周复盘详情正文为 `display:none`。保留有文字和下一步动作的
单一空状态，不创建无内容卡片。

## 相关文件

- [web/templates/reports.html](../../web/templates/reports.html) — 修正隐藏与折叠布局
- [docs/product/daily-report.md](../product/daily-report.md) — 增加空状态验收合同
- [docs/design/memory-system.md](../design/memory-system.md) — 记录报告渲染约束

## 验证

模板测试断言 `hidden` 和关闭 `details` 的退出布局规则；本地 8080 分别检查日报、周复盘及收起归档。
