# Bug: 历史日报错误套用后来生效的训练方案

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: major
- **影响范围**: 日报计划快照、AI 计划执行评价、训练首页历史日期、Coach/MCP 训练方案工具

## 现象

账户在 2026-08-03 才确认启用训练方案，但重新生成 2026-08-02 日报时，AI 把 8 月 2 日训练与
“方案首周周日恢复跑”比较并判断为“未执行”。真实首周是 8 月 3 日至 8 月 9 日，周日课次日期为
8 月 9 日，因此该评价发生了方案时间穿越。

## 根因

日报写入器仍通过通用 `MemoryStore` 读取 `active-plan.md`。新版文件类型 `training_scheme` 不属于通用
`MemoryType`，解析异常被吞掉后 `plan_context` 固定写成不存在；在线 AI 又通过另一条
`get_training_plan()` 工具读取当前实时方案。该工具不接收报告日期，`TrainingService.home(D)` 也会把
当前周模板物化到任意历史周，导致结构化快照为空、AI 上下文却引用未来方案的矛盾状态。

## 修复方案

- Training Scheme 生效版本保存 `activated_at`、`effective_from` 和 `effective_to`；调整确认后关闭旧版本
  生效区间并保留不可变历史。
- 新增 `TrainingService.resolve_plan_context(D)`，只选择覆盖 `D` 的方案版本，再解析 `D` 的具体课次。
- 日报写入器直接调用训练领域服务，不再让通用 `MemoryStore` 解析 `training_scheme`。
- Coach 与 MCP 的 `get_training_plan` 增加可选 `target_date`；日报 Tool Use 即使模型漏传也由编排层补入
  报告日期。
- 没有生效方案时固化 `no_effective_plan` 和 `comparison_status=not_applicable`，并强制把 AI 的计划执行
  结构改为“不适用”。

## 相关文件

- [src/training.py](../../src/training.py) — 方案生命周期、历史版本解析和日期计划上下文
- [src/memory.py](../../src/memory.py) — 日报按报告日期固化计划快照
- [src/coach.py](../../src/coach.py) — 日期感知工具、Prompt 与无方案执行边界
- [src/mcp_server.py](../../src/mcp_server.py) — `get_training_plan(target_date)` MCP 合同
- [docs/product/daily-report.md](../product/daily-report.md) — 日报计划联动产品规则
- [docs/product/training-experience.md](../product/training-experience.md) — 方案生效与历史执行语义
- [docs/design/training-system.md](../design/training-system.md) — 生命周期和日期解析设计
- [docs/design/memory-system.md](../design/memory-system.md) — 日报快照数据流

## 验证

- 方案在 `D+1` 激活时，`resolve_plan_context(D)` 与日报 Front Matter 都返回
  `no_effective_plan / not_applicable`。
- 构造覆盖 `D` 的历史 v1 和 `D+1` 生效的 v2，`D` 仍解析 v1 及其具体课次。
- Coach `get_training_plan(target_date=D)` 与日报使用同一解析结果。
- 运行完整 `pytest`，要求零失败。
