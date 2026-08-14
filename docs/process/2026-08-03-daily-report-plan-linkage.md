# 日报与训练方案按日期联动

- **日期**: 2026-08-03
- **类型**: architecture

## 背景与动机

日报已有活动日期锚点，但计划上下文仍混用了旧 Memory 读取和当前实时 TrainingService 两条链路。
方案在报告日之后才激活时，AI 因而会用未来方案评价历史训练。修复需要贯通方案生命周期、历史版本、
日报快照、Coach Tool Use 和 MCP 合同，不能只修改一段提示词。

## 方案选择

- 以用户本地自然日作为日报与方案联动的最小时间粒度。
- 每个确认生效的方案版本保存显式生效区间，当前文件只作为实时读模型，历史解析读取版本快照。
- 由 `TrainingService` 提供唯一日期解析入口；Memory、Coach 和 MCP 不各自实现方案选择。
- Weekly Plan 缺失时只允许从已经覆盖目标日期的历史方案版本确定性物化，禁止当前方案越过
  `effective_from` 反向生成。
- 无生效方案是正常业务状态，使用 `not_applicable`，不把它伪装成未执行或休息日。

## 实现步骤

1. 回写日报和训练体验产品真相源，确认历史时点和无方案状态。
2. 扩展 Training Scheme 生命周期与历史版本读取，新增日期解析服务。
3. 日报写入改用日期解析服务，移除旧通用 Memory 读取路径。
4. Coach/MCP 增加可选目标日期，并在日报 Tool Use 编排层强制注入报告日期。
5. 同步运行时 Coach Skills、README、Bugfix 和 CHANGELOG。
6. 用“8 月 3 日激活、查询 8 月 2 日”和历史 v1/v2 两组回归测试验证。

## 遇到的问题与解决

- `training_scheme` 不是通用 `MemoryType`：不扩张通用记忆枚举，日报直接调用训练领域服务。
- 模型可能继续调用空参数 `get_training_plan({})`：除更新 Schema 和 Prompt 外，日报编排层还会为相关
  工具补入报告日期。
- 部分旧方案没有生命周期字段：读取时按 `activated_at → updated_at → created_at` 回退推导首次生效日，
  但绝不推导到更早日期；新写入全部保存显式字段。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [日报产品方案](../product/daily-report.md), [训练体验产品方案](../product/training-experience.md)
- Design: [训练系统设计](../design/training-system.md), [记忆系统设计](../design/memory-system.md)
- Bugfix: [历史日报错误套用后来方案](../bugfixes/2026-08-03-daily-report-plan-time-travel.md)
