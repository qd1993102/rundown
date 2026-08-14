# Bug: 周课表无法打开每日完整训练内容

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 训练首页本周安排

## 现象

本周日卡只显示简短课名、距离/时长和体感。原有“查看结构”入口触控面积小，且只能读取训练块，用户无法从周课表查看目标、强度、完成要求和调整边界，只能回到当天训练安排。

## 根因

周课表把完整 `training_prescription` 压缩为狭窄日卡内的 `details` 局部内容，没有提供可发现的完整详情入口。

## 修复方案

每个非休息日卡新增最小 36px（移动端 40px）的“查看训练详情”按钮。点击后在同页周课表下方渲染该日完整处方；用户可收起详情。该操作只读当前周计划，不会生成反馈、调整提案或修改方案。

## 相关文件

- [web/templates/training.html](../../web/templates/training.html) — 日卡操作、详情面板与响应式样式。
- [docs/product/training-experience.md](../product/training-experience.md) — 周课表交互规则。
- [docs/design/training-system.md](../design/training-system.md) — 训练首页读模型交互合同。

## 验证

- 非休息日卡包含“查看训练详情”操作并可定位到对应周课次。
- 详情面板含完整 Training Prescription 和收起操作。
- 休息日不显示无意义详情按钮。
- 相关 Web 回归测试通过。
