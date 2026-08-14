# Bug: 训练方案确认页无法修改草稿

- **发现日期**: 2026-08-02
- **修复日期**: 2026-08-02
- **严重程度**: major
- **影响范围**: 训练方案五步建立向导、草稿 API 与最终确认交互

## 现象

用户进入第 5 步确认时，只能返回只读草稿、继续讨论或直接启用，无法明确修改目标、当前训练基础和现实约束。即使退回约束步骤再次提交，也会创建新的草稿，用户无法确认自己修改的是哪一份。

## 根因

产品文档规定了“返回修改”，但 Web 只实现了通用“上一步”；训练基础没有用户纠正输入，训练服务也只有创建和激活草稿接口，没有草稿原地更新能力。

## 修复方案

在最终确认页提供目标、训练基础和现实约束三个明确入口；目标通过共享 Training Goal 接口编辑，基础和约束保存后通过草稿更新接口重新计算建议与周结构。更新保留原 `plan_id` 且仍为 `draft`，只有显式确认才激活。

## 相关文件

- [src/training.py](../../src/training.py) — 增加草稿更新和重新计算
- [src/web.py](../../src/web.py) — 增加草稿更新 API
- [web/templates/training.html](../../web/templates/training.html) — 增加可编辑确认流程
- [docs/product/training-experience.md](../product/training-experience.md) — 明确确认前编辑规则与验收标准
- [docs/design/training-system.md](../design/training-system.md) — 记录接口、状态与数据边界

## 验证

领域测试验证草稿原地更新且生效方案不可修改；Web API 测试覆盖用户隔离的更新流程；页面测试和本地浏览器走查覆盖三个编辑入口、重新预览和最终启用。完整 `pytest` 必须零失败。
