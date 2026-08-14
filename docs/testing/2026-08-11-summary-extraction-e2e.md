# Summary Extraction 独立 E2E / 回归验收

- **日期**: 2026-08-11
- **范围**: `src/summary_extraction.py`、`src/activity.py`、`tests/test_summary_extraction.py`、`tests/test_storage.py`，以及对应产品/运营/设计文档同步。
- **职责边界**: 本轮仅做只读代码审查、隔离数据库回放和测试；未修改业务代码或测试文件。

## 结论

**status: superseded（修复后通过）**。初验发现的问题已在主任务中修复并回归；以下原始失败记录保留用于审计。

修复后验收：L0 无强度时 `intensity_basis=null`，偶数分段前后半程正确，evidence 包含 `kind/source/field/observed_at/value/confidence`；MemoryStore 与 TrainingDaySummaryBuilder 读取 compact `session_summary`，报告文本不再读取原始 `detail_json`。`pytest -q` **410 passed**。

原始初验结论：基础单元/回归测试通过，L1/L2、幂等、摘要失败隔离和聚合读取不泄漏 1Hz 原始字段均通过；但尚不能按文档验收通过：

1. L0 只有活动量、没有可用 pace/HR 时，`intensity` 为 `None`，但 `data_quality.intensity_basis` 仍为 `pace`。这违反运营验收“没有强度维度不应统计为 basis=pace”，也会让机器统计把无强度摘要误计为 pace 代理。
2. 设计声明摘要事实要供 `TrainingSessionAnalyzer`、`TrainingDaySummaryBuilder`、日报/周报和 AI 只读消费；当前代码检索显示 `activity_summary_facts` 仅在入库和 `get_activity_summary_facts()` 中出现，未发现这些下游调用方接入。当前通过的是“存储层”验收，不是完整数据流验收。

## 验收结果

| 场景 | 结果 | 证据 |
|---|---|---|
| L0 活动量 | **部分通过** | summary-only 输入返回 `granularity=L0`、`structure/intensity/terrain=None`，量字段正确；但 `data_quality.intensity_basis="pace"`（无 pace/HR）。见 `src/summary_extraction.py:194-205,223-232`。 |
| L1 分段聚合 | **通过** | 3 段输入得到 `L1`、`n_splits=3`、加权 cadence、pace intensity；缺 HR 时 `intensity.basis="pace"`。 `pytest -q tests/test_summary_extraction.py tests/test_storage.py`：19 passed。 |
| L2 1Hz 指标 | **通过（夹具级）** | 1Hz pace/HR/grade 输入得到 `L2`、`basis="hr"` 和 grade profile；对应定向测试通过。 |
| 缺心率 pace 代理 | **通过（L1）/失败（L0 无强度）** | L1 输出 `basis=pace` 且 `hr_bands_pct={}`；L0 无 pace/HR 仍设置 basis 字段，需修复。 |
| 重复入库幂等 | **通过** | 隔离 SQLite 重复 `store_activity_detail()` 两次后 `activity_splits=1`、`activity_summary_facts=1`，版本和核心结果稳定。现有 `tests/test_storage.py::test_activity_detail_resync_replaces_versioned_summary_facts` 亦通过。 |
| 摘要提炼失败隔离 | **通过** | monkeypatch `build_session_summary` 强制抛 `RuntimeError`：`store_activity_detail=True`，`activity_details=1`、`activity_splits=1`、`activity_summary_facts=0`；日志仅输出 activity ID 与异常类型。 |
| 聚合事实不泄漏原始 1Hz | **通过（getter 边界）** | 1Hz 夹具含 `secret=RAW-1HZ`，`get_activity_summary_facts()` 序列化结果不含该值。注意原始 `detail_json` 仍按设计保存在 `activity_details`；完整下游 AI 边界尚未因未接入而完成验证。 |
| 设计/产品/运营文档 | **文档已同步，运行接入不完整** | `docs/design/summary-extraction.md`、`docs/product/daily-report.md:97-115,263-271`、`docs/operations/summary-extraction-launch-acceptance.md`、`docs/CHANGELOG.md` 均已记录契约；实现尚未兑现文档 §4 的下游接入。 |

## 测试证据

- `pytest -q tests/test_summary_extraction.py tests/test_storage.py` → **19 passed in 0.55s**。
- `pytest -q` → **409 passed in 43.84s**。
- 隔离数据库、异常注入和原始字段泄漏检查均使用当前工作区代码直接执行；无真实 Provider、ECS 或 AI Provider 环境，因此不代表线上验收。

## 建议动作

1. 由 developer 修复 L0 `intensity_basis`：没有 `pace_values` 且没有 `hr_values` 时应为 `None`/缺失（不要写 `pace`），并更新现有 L0 测试断言及运营统计口径。
2. 将 `get_activity_summary_facts()` 接入设计声明的 analyzer/day-summary/report/AI 读取路径；为每个调用方增加“只读聚合、不含 `detail_json`/1Hz、版本一致”的测试。
3. 复核 evidence schema。产品文档要求每项 evidence 至少含 `kind/source/field/observed_at|data_as_of/value/confidence`，当前实现的 evidence 只有 `field/source/value`，应明确是后续版本还是当前验收阻断项。

## Handoff

- **status**: `failed`
- **summary**: 存储层与基础聚合回归通过；L0 代理声明错误，且摘要尚未接入设计声明的下游消费链路，完整验收不通过。
- **facts**: 定向 19 passed；全量 409 passed；幂等、失败隔离、getter 聚合边界回放通过；L0 无强度却标记 pace；仅 `src/activity.py` 持久化/读取摘要事实。
- **assumptions**: 未执行真实 Provider/ECS/AI；`build_session_analysis_text` 和 MCP 详情工具不作为本轮 AI 主链路证据，但它们仍直接读取 `detail_json`，需 developer 明确边界。
- **artifacts**: 本报告；测试日志可由命令重现（本轮未修改测试文件）。
- **decisions_needed**: 主 Agent/产品确认 evidence 完整字段（`kind`、时间、confidence）是否为当前版本阻断项；确认是否要求本轮完成 analyzer/day-summary/report/AI 接入。
- **next_agent**: `developer`
- **handoff_packet**: 目标是完成并验收 `SessionSummaryFacts` 全链路；已确认约束为 L0/L1/L2 如实降级、缺 HR 时仅在 pace 可用时声明 `basis=pace`、重复入库幂等、提炼失败不阻断原始入库、AI/报告不得读取完整 `detail_json` 或 1Hz；文件范围为 `src/summary_extraction.py`、`src/activity.py`、下游 analyzer/day-summary/report/AI 读取模块及对应 tests/docs；验证命令为 `pytest -q tests/test_summary_extraction.py tests/test_storage.py` 与 `pytest -q`，并补充下游边界回放；剩余风险为真实 Provider 字段覆盖、线上耗时和生产日志审计未验证。
