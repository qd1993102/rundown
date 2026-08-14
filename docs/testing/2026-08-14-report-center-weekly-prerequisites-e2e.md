# 报告中心周复盘前置链路 E2E 验收

- **日期**: 2026-08-14
- **范围**: 日报/周复盘空状态、周复盘逐日前置事实、质量课总结、统一事实版本与 AI 调用边界
- **环境**: 本地 `http://127.0.0.1:8080`（验收时监听 PID `99412`）
- **结论**: 通过

## 验收场景与证据

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 隐藏的 AI 状态条不占位 | 通过 | 浏览器实测 `#aiTaskStatus` 为 `display:none`、高度 `0`；日报与周复盘均成立。 |
| 关闭的历史周复盘正文不占位 | 通过 | 浏览器实测 `.weekly-review-details:not([open]) > .weekly-review-body` 高度列表为 `[0]`。 |
| 页面没有可见空壳 | 通过 | 浏览器检查 `.report-status`、`.weekly-card`、`.empty-state`、列表容器：可见且无文本的元素为 `[]`。 |
| 当前周不纳入未来日期 | 通过 | 以 2026-08-14 为观测截止：manifest `expected_days=5`、`prepared_days=5`，日期仅为 2026-08-10 至 2026-08-14；2026-08-16 未进入。 |
| 周级 AI 前完成逐日事实和分析 | 通过 | `WeeklyPrerequisiteManifest` 为 `sealed=true`、`status=complete`；每个日条目均有 `fact_version`、`analysis_version` 与状态。 |
| 不自动创建七份日报 | 通过 | 动态运行 `review_week(..., include_ai=False)` 后临时 `memory/auto/daily/*.md` 为空；单元测试同样断言 `memory/reports/daily` 不存在。 |
| 在线 AI 至多一次 | 通过 | 记录 runner 的用例只观察到 `review-training-week` 一次；已结束计划周对应 `review-training-plan` 一次。 |
| 质量课门槛与统一模型 | 通过 | `confidence=0.59` 的 tempo 候选进入 `quality_candidate_gaps`，`0.60` 才进入质量课；输出不含 provider 字段，按 `activity_id` 聚合去重。 |
| ValueOrGap 与不可信分段 | 通过 | 质量课指标均为 `{status,value,field[,reason]}`；`quantity_reliable=false` 时课程仍保留，`reliable_segments={status: gap, reason: quantity_unreliable}`。 |
| 事实版本稳定性 | 通过 | 相同 SessionSummary 原始输入产生相同 `fact_version`，源数据变化则版本变化；TrainingDayFact 同时保留 `summary_version` 兼容别名。 |

## 独立执行的自动化检查

```text
pytest -q \
  tests/test_training.py::test_week_quality_session_keeps_intensity_but_gaps_unreliable_segment_quantity \
  tests/test_training_day_summary.py \
  tests/test_summary_extraction.py::test_session_fact_version_is_stable_and_changes_with_source_detail \
  tests/test_training.py::test_week_review_prepares_daily_analysis_before_quality_summary \
  tests/test_training.py::test_week_review_with_plan_uses_one_primary_ai_call \
  tests/test_web.py::test_daily_templates_are_mobile_first_and_support_local_png_export \
  tests/test_web.py::test_weekly_report_routes_require_explicit_generation -rA

13 passed
```

另执行 `git diff --check` 与 `python3 -m py_compile src/summary_extraction.py src/training_day_summary.py src/training.py src/memory.py src/web.py`，均通过。

## 修复回归

初验发现周报消费端没有把 `session_summary.quantity_gate.quantity_reliable` 传入结构分类，导致不可信分段会被错误显示为可靠。修复后重新以带间歇结构和 `quantity_reliable=false` 的活动运行周复盘：

```text
classification_quantity_reliable: false
quality_type: interval
reliable_segments: {status: gap, value: null, reason: quantity_unreliable,
                    field: reliable_segment_count}
```

## 剩余风险

- 本次 UI 实测使用已有本地归档周，因此只验证了“无满足门槛质量课”的真实页面文案；质量课有数据时的字段展示由上述确定性服务级动态验收与单元测试覆盖。
- 未调用真实在线模型或第三方数据源；“至多一次”由注入 runner 的调用计数验证，不代表供应商端实际可用性。
