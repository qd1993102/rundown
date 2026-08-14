# Bug: 受限版日报不生成 AI 洞察，有跑步时“今天对计划意味着什么”没有任何跑步分析

- **发现日期**: 2026-08-13
- **修复日期**: 2026-08-13
- **严重程度**: major
- **影响范围**: 日报生成（Web `/api/reports` POST、CLI `neurun daily`）与日报详情结论模块；
  高驰（Coros）等未授权睡眠数据源的用户几乎每次日报都受影响

## 现象

高驰用户 `qd1993102@163.com` 在 08-11 明明有跑步记录，但日报详情的
【今天对计划意味着什么】模块没有任何跑步分析，结论只显示
“该日没有生效训练方案，无法比较计划执行。”（或空）。

本地复现：为该用户生成 2026-07-29（有 Indoor Run 8.22km）受限版日报后，
`/api/dashboard?date=2026-07-29` 返回 `ai_insight: {}`、
`conclusion: ""`、`observations: []`，而 `daily_activities.sessions` 明明有跑步。

## 根因

日报生成链路在报告为 `limited`（任一辅助维度被省略，如高驰未授权睡眠 → `omitted=['sleep']`）
时**整块跳过 AI 洞察生成**：

1. `src/main.py _do_daily_sync`：`if not readiness.omitted_sections: ai_result = _get_ai_insight(...)`，
   omitted 非空时从不调用在线 `review-daily-training` Skill；
2. `src/memory.py generate_daily_report`：`ai_allowed` 门禁在 `omitted_sections` 命中
   {sleep, recovery, trends_7d, training_load} 时强制 `fm["ai_insight"] = {}`，
   确定性兜底 `_generate_ai_insight` 也不会运行。

结果：`ai_insight={}` → 前端结论回退到 `execution.headline`（无方案时是“无法比较计划执行”），
跑步分析完全丢失。这偏离产品契约：`daily-report.md §7` 要求受限版“缺失维度从 AI 输入中移除”
（而非跳过 AI），AI 只解释可用事实（运动概要/当日训练）。

## 修复方案

- `src/main.py`：`_do_daily_sync` 不再按 `omitted_sections` 跳过在线 AI——受限版同样调用
  `_get_ai_insight`。缺失维度已在 Front Matter 标记 `status=unavailable` 并随
  `omitted_sections` 传入 Skill（`coach.py _daily_fact_pack`），模型契约要求保留未知。
- `src/memory.py generate_daily_report`：移除 `ai_allowed` 门禁；调用方提供在线洞察时直接采用，
  未提供时走确定性兜底。
- `src/memory.py _generate_ai_insight`：新增 `omitted_sections` 参数，确定性兜底按省略维度
  跳过对应结论（睡眠/恢复评分/ACWR/趋势均不输出依赖缺失数据的结论），只保留运动概要、强度分布
  与当日训练分析，避免伪造“综合恢复评分 0/100”“ACWR optimal”等。

## 相关文件

- [src/main.py](../../src/main.py) — 受限版同样调用在线 AI 洞察
- [src/memory.py](../../src/memory.py) — 移除 `ai_allowed` 门禁；确定性兜底按 `omitted_sections` 收敛
- [tests/test_report_readiness.py](../../tests/test_report_readiness.py) — 更新断言：受限版洞察非空、
  不伪造恢复评分/ACWR
- [docs/product/daily-report.md](../../docs/product/daily-report.md) — §7 明确受限版同样执行一次
  `review-daily-training` 调用

## 验证

- 复现路径：为该高驰用户重新生成 2026-07-29 受限版日报 → `/api/dashboard` 返回
  `ai_insight.conclusion` = “当日完成一次节奏跑，距离 8.22km，平均配速 5'34\"/km…”，
  `observations` 含运动概要/强度分布/恢复分析；睡眠缺失被如实标注，未伪造恢复结论。
- Playwright 无头浏览器：`/?date=2026-07-29` 的【今天对计划意味着什么】展示完整跑步分析。
- 单元测试：受限版确定性兜底不再输出 `ai_insight={}`，且观察文本不含
  “综合恢复评分”“ACWR”（缺失维度结论被跳过）。
- `pytest` 全量零失败（504 passed, 1 xfailed）。
