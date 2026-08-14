# Bug: 周复盘缺少显式逐日前置分析和质量课总结

- **发现日期**: 2026-08-14
- **修复日期**: 2026-08-14
- **严重程度**: major
- **影响范围**: 周复盘、共享单日事实、质量课展示、周级 AI 输入

## 现象

周复盘虽然会临时聚合单日活动，但没有封口的逐日前置清单，也没有统一的确定性日分析和质量课事实；活动级
训练类型不足时，周报只能提示“训练类型识别不足”。

## 根因

`TrainingDayFact` 到周级消费之间缺少明确的 `TrainingDayAnalysis` 与 `WeeklyPrerequisiteManifest`，质量课
判定仍散落在活动名称或活动级分析可用性中，无法证明日报、周报和草稿消费同一版本事实。

## 修复方案

新增确定性日分析和版本字段；周复盘在周级 AI 前为每个已发生日期生成或复用分析并封口清单。质量课只从统一
结构事实提取，执行置信度、主证据和数量门禁，所有指标表达 value 或 gap。整个周任务仍最多一次周级 AI，
不自动创建日报归档。

## 相关文件

- [src/training_day_summary.py](../../src/training_day_summary.py) — 统一日分析、质量课事实和聚合
- [src/training.py](../../src/training.py) — 周前置清单、确定性质量课总结及周报快照
- [src/memory.py](../../src/memory.py) — 日报消费同一日分析模型
- [web/templates/reports.html](../../web/templates/reports.html) — 展示质量课总结
- [docs/design/training-system.md](../design/training-system.md) — 前置流水线技术合同

## 验证

单元测试覆盖质量课门槛、低置信度排除、逐日前置版本和单次周级 AI；全量 pytest 与本地报告页回放验证。
