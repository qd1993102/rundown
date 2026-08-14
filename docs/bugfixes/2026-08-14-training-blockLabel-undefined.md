# Bug: 训练页点击每日课程时 `blockLabel is not defined`

- **发现日期**: 2026-08-14
- **修复日期**: 2026-08-14
- **严重程度**: critical
- **影响范围**: 训练方案页（`web/templates/training.html`），点击每日课程查看训练详情时脚本中断

## 现象

点击草稿的每日课程时，浏览器控制台报 `Uncaught ReferenceError: blockLabel is not defined`，训练详情弹窗无法渲染。

## 根因

`stepsCompactSummary`、`stepMarkup`、`prescriptionMarkup` 三个函数中使用了 `blockLabel` 和 `stimulusLabel` 作为角色/刺激标签的映射表，但这两个变量从未在模板中定义。只有 `intensityZoneLabel` 有定义。

## 修复方案

在 `intensityZoneLabel` 定义之后，补充 `blockLabel` 和 `stimulusLabel` 两个常量定义，覆盖所有已知的 `step.role` 和 `stimuli` 取值。

## 相关文件

- [web/templates/training.html](web/templates/training.html) — 新增 `blockLabel` 和 `stimulusLabel` 定义

## 验证

修复后 `blockLabel` 和 `stimulusLabel` 在引用前已定义，ReferenceError 不再出现。
