# Bug: 训练首页混淆能力参考、计划时间线与训练推进

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 训练首页的活跃训练方案

## 现象

首页把“当前可持续周跑量”命名为“训练事实与推进”，容易被理解为本周实际跑量或已经确认的训练进程；教练定位和该卡又位于今日训练之前。长期方案只显示单个当前阶段，看不到完整阶段路线和时间位置。

## 根因

首页直接渲染 `capacity_profile` 的一个估算字段，并复用了方案中初始化的 `current_phase`，没有将阶段路线与启用日期计算出的时间位置作为独立、受限的展示模型。

## 修复方案

TrainingService 新增只读 `plan_timeline`：它计算完整阶段路线、首个完整训练周、阶段周位置和计划时间线百分比；Bridge Week 不计入阶段进度。前端将核心顺序恢复为今日、本周、长期方案，完整阶段路线使用加权进度图展示。能力卡改名为“训练基础与数据状态”，标注能力估算、事实截止时间和同步覆盖；教练定位移至辅助区。页面明确实际训练推进仍由周复盘和用户确认决定。

## 相关文件

- [src/training.py](../../src/training.py) — 计划时间线读模型。
- [web/templates/training.html](../../web/templates/training.html) — 首页信息层级、阶段图和数据边界文案。
- [docs/product/training-experience.md](../product/training-experience.md) — 首页交互规则。
- [docs/design/training-system.md](../design/training-system.md) — 时间线读模型合同。

## 验证

单元测试验证 Bridge Week 不进入阶段进度、首个完整周从第一阶段开始；模板测试覆盖阶段时间线、能力数据边界及辅助信息顺序。
