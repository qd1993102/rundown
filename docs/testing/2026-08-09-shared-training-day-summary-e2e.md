# E2E 验收报告：共享单日训练摘要串联

- **日期**: 2026-08-09
- **范围**: `training_day_summary` 在草稿、日报、周复盘及可选语义 Skill 间的串联
- **执行角色**: E2E / 质量负责人
- **状态**: ready

## 结论

本轮共享摘要改动通过当前代码与隔离服务级验收。未发现需要回退或降低断言的业务层失败；工作区原有改动未被覆盖。

## 验收结果

| 场景 | 结果 | 证据 |
|---|---|---|
| 草稿长期参考仅使用上一完整自然周的 4–8 周摘要，最近两日仅用于衔接 | PASS | `TrainingService._training_summary_context()` 的边界场景：注入当前周 99 km、上一完整周 11 km、最近两日 22 km，结果为 8 个完整周、基线取 11 km，最近两日单独返回且当前周 99 km 被排除；`tests/test_training.py::test_draft_training_summary_uses_previous_complete_weeks_and_recent_days` |
| 日报 front matter 与日报教练事实包包含 `training_day_summary` | PASS | 隔离 `MemoryWriter.generate_daily_report(..., persist=False)` 断言摘要 schema、活动类型、`facts_cutoff`，并断言 `src.coach._daily_fact_pack()` 原样携带该摘要 |
| 周复盘按自然周聚合摘要 | PASS | 隔离 `TrainingService.review_week(target=<上一自然周周一>, include_ai=False)` 断言 `window_start/window_end` 为周一至周日、包含 7 个 `daily_summaries`、跑量 29 km；`tests/test_training.py` 周复盘/周报场景通过 |
| `summarize-training-day` 可选，AI 不可用不阻断 | PASS | `tests/test_coach_runtime.py` 校验 Skill 注册与输出合同；未设置 `NEURUN_AI_API_KEY` 时隔离 `get_coach_insight()` 返回 `None`；确定性摘要仍可生成 |
| 不新增页面或设置 | PASS | 改动链路只新增共享事实/运行时合同；Web 页面路由仍为现有报告、训练、同步、我的等入口，未发现摘要专用页面或设置项 |
| 回归 | PASS | `pytest -q` → `396 passed in 44.40s`；`pytest -q --disable-warnings --maxfail=1` 无失败输出 |

## 失败层级与风险

- **业务层失败**: 无。
- **测试层失败**: 无。
- **环境/外部依赖**: 未执行真实 AI Provider 或外部账号同步；本报告结论限于本地代码、隔离 loader 和现有测试，不等同于线上 Provider/ECS 验收。
- **职责边界说明**: Web 训练报告的 `TrainingService.review_week()` 已复用聚合摘要；CLI `summary` 仍调用历史 `MemoryStore.generate_weekly_summary()` 活动摘要入口。本轮产品范围中的“周报/周复盘”指 Web 报告中心的自然周复盘，CLI 旧摘要命令不属于该用户旅程，因此不纳入共享摘要一致性验收。
- **未验证假设**: 真实用户数据中活动日期时区、同步覆盖状态和历史分析字段仍需在目标环境抽样确认；当前已验证未知覆盖不会被确定性摘要误判为休息。

## 建议动作

保持当前实现并进入主 Agent 的开发→E2E 交付闭环。上线前补做目标环境的只读健康检查、同步覆盖抽样和一次真实 AI Provider 合同验收；若 Provider 不可用，应保持确定性事实摘要与日报/周报主链路可用。

## Handoff

- `status`: ready
- `summary`: 共享单日训练摘要的草稿边界、日报事实包、自然周聚合及 AI 降级均通过本地 E2E/回归验收。
- `facts`: 见“验收结果”；全量 396 个测试通过；未修改业务代码或测试文件。
- `assumptions`: 真实 Provider、ECS、第三方同步和线上时区数据未在本轮验证。
- `artifacts`: [本报告](2026-08-09-shared-training-day-summary-e2e.md)；证据日志 `/private/tmp/rundown-shared-summary-pytest.log`。
- `decisions_needed`: 无；上线前是否安排真实 Provider/ECS 抽样验收由主 Agent 决定。
- `next_agent`: 主 Agent（随后按需交给 developer 或发布/运营验收）。
- `handoff_packet`: 目标为验收共享单日摘要串联；已确认约束为未知覆盖不等于休息、保留结构化 evidence、不得增加第二次串行 AI 调用、无新页面/设置；范围覆盖 `src/training_day_summary.py`、`src/memory.py`、`src/coach.py`、`src/training.py`、`src/training_planning.py`、`src/coach_runtime/`、相关 prompts/tests/docs；验证命令为 `pytest -q`（396 passed）及本报告中的两个隔离 Python 服务级场景；剩余风险为真实 Provider/ECS/第三方数据未验收。
