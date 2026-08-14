# Bug: 训练草稿只展示相对星期而没有具体日期

- **发现日期**: 2026-08-09
- **修复日期**: 2026-08-09
- **严重程度**: minor
- **影响范围**: 训练草稿日期合同、草稿预览、当前周与下一周课程投影

## 现象

产品规范要求草稿的当前周和下一周逐日展示具体日期，但候选只包含 `weekday`，Web 预览在没有 `date` 时回退显示“周一/周二”。技术设计的旧验收条目还错误地要求预览不存在训练日期，与新的强日期安排相冲突。

## 根因

AI 候选的相对星期合同与用户读模型没有明确分层：生效后的 Weekly Plan 会在 `_materialize_week()` 中确定性补充具体日期，但待确认草稿缺少对应的只读日期投影。同时，历史“课程不可编辑”约束被误写成“日期不展示”。

## 修复方案

- 保留 AI `weekday` 相对槽位，不让模型生成 ISO 日期；
- 草稿服务按推荐开始日期和自然周边界生成 `near_term_schedule`，为当前周、下一周课程补充具体 `date`、星期名称和周边界；
- Web 预览优先读取该投影并显示日期范围与逐日日期；
- 修正产品、技术设计和 Skill 约束，明确“展示具体日期”和“不得编辑日期”可以同时成立；
- 生效后继续由正式 Weekly Plan 生成稳定 `workout_id`，临时日期变化仍走 Local Schedule Adjustment。

## 相关文件

- [src/training.py](../../src/training.py) — 新增草稿近期课程的确定性日期投影。
- [web/templates/training.html](../../web/templates/training.html) — 草稿预览展示当前周与下一周的具体日期。
- [docs/product/training-experience.md](../product/training-experience.md) — 明确强日期安排的产品边界。
- [docs/design/training-system.md](../design/training-system.md) — 修复相互冲突的 Presenter 和验收合同。

## 验证

- 草稿领域测试验证两周投影的 `week_start`、`week_end`、`date` 与 `weekday` 一致；
- Web 模板测试验证预览读取 `near_term_schedule`；
- Coach Runtime 测试验证模型仍只输出相对星期，不生成具体 ISO 日期；
- 运行完整 `pytest`，确认零失败。
