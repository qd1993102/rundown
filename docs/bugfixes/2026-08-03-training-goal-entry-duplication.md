# Bug: 训练目标在“我的”和训练页重复维护

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: minor
- **影响范围**: Web 个人页、训练建立向导与方案级重规划入口

## 现象

用户既能在“我的”页增删改训练目标，也能在训练建立向导中选择、新建和编辑目标，两个入口的职责重叠，难以判断何处才是建立和调整训练方案的入口。

## 根因

早期个人资料页把 Training Goal 当作个人资料的一个字段；训练主界面上线后复用了同一目标实体，但没有移除旧的交互入口。

## 修复方案

移除“我的”中的目标列表及目标增删改。训练建立向导成为唯一的目标选择、新建与编辑入口；已启用方案的目标变化从训练页发起方案级重规划，提案确认前不改写当前方案。

## 相关文件

- [web/templates/profile.html](../../web/templates/profile.html) — 移除重复目标表单与请求逻辑。
- [web/templates/training.html](../../web/templates/training.html) — 明确目标变化进入训练页重规划入口。
- [docs/product/training-experience.md](../product/training-experience.md) — 更新模块职责与交互规则。
- [docs/design/training-system.md](../design/training-system.md) — 更新仓库与 API 使用边界。

## 验证

模板回归测试确认“我的”不再包含目标表单或 `/api/goals` 调用，训练页仍保留目标创建、编辑与重规划入口。
