# Bug: 短跑课型未知时日报误报非跑步并隐藏教练分析

- **发现日期**: 2026-08-24
- **修复日期**: 2026-08-24
- **严重程度**: major
- **影响范围**: 日报生成、日报 AI 洞察、Web 仪表盘、静态 HTML、MCP 日报入口

## 现象

2026-08-24 有 3 条原始跑步活动，接口中的活动类型分别为 `treadmill_running`、
`treadmill_running` 和 `running`。由于单次活动时长不足或强度证据不足，训练课型被保守判定为
`primary_type=unknown`。日报本地洞察却输出“当日运动为非跑步类型，未纳入跑步教练分析”，导致跑步概要、
配速、心率和动力学分析未进入教练观察。

同一份日报还曾同时出现日报负荷 ACWR 4.0（高风险）和日报级跑步分析 ACWR 1.00（安全区）。
在线 AI 失败后使用本地规则洞察，但结果没有明确标记来源。

## 根因

1. `primary_type` 代表有氧/节奏/间歇等训练课型，不代表运动模态。兜底逻辑用
   `primary_type != unknown` 判断是否跑步，把“跑步但课型未知”误判为非跑步。
2. `running_analysis_daily` 重新按活动列表计算了另一套 ACWR 口径，与日报 `training_load` 的
   7 天急性负荷 / 28 天周均慢性负荷不一致。
3. 本地兜底结果和在线 AI 结果缺少稳定的机器可读来源字段。
4. MCP 受限日报入口通过传入空 `ai_insight` 静默绕过了本地洞察生成。
5. 在线 CoachInsight 中可选的 `share_card.sessions` 某条摘要返回字符串时，严格校验会让整份教练结果失败。
6. 日报 CoachInsight 包含完整观察、证据和可选分享卡时，`max_tokens=1800` 不足以容纳完整 JSON，Provider 返回截断内容。
7. Dashboard 只渲染 `conclusion` 和 `observations`，接口中已生成的训练效果、逐次活动特点、恢复响应、能力信号、次日约束、证据和数据缺口没有呈现。
8. 2026-08-24 的在线 AI 返回了不完整的 `share_card`（只有 headline 和 conclusion，sessions/takeaway 为空）；分享卡适配器将其视为完整摘要，因此没有使用同一份 CoachInsight 中已有的逐次训练特点和训练效果。

## 修复方案

- 在 `session_analyses` 保存 `is_running`，将运动模态与训练课型分离。
- 本地教练洞察依据 `is_running` 纳入跑步活动；课型未知时明确说明具体课型无法可靠判定，
  仍输出运动概要、强度分布和可用跑步分析。
- `running_analysis_daily` 复用日报 `training_load` 的 ACWR 数值和风险分级，删除重复冲突输出。
- 在线 AI 结果标记 `generation_mode=online_ai` / `semantic_status=available`；本地规则结果标记
  `generation_mode=deterministic_fallback` / `semantic_status=unavailable` / `fallback_reason`。
- Web、静态 HTML 和 CLI 明确显示本地规则兜底；MCP 不再用空洞察跳过确定性分析。
- 可选 `share_card` 的非法条目按条丢弃，保留核心 CoachInsight；分享卡不再阻断日报在线教练分析。
- 将 `review-daily-training` 输出预算从 1800 提高到 4000 tokens，避免日报结构化 JSON 被截断。
- Dashboard 在日报顶部增加默认展开的完整教练分析，呈现已有结构化字段，并保留证据和数据缺口。
- 分享卡遇到稀疏但非空的 `share_card` 时，补充安全的 `session_characteristics` 和 `training_effect`；仍执行跑步活动索引校验及睡眠、恢复、风险、建议等隐私词过滤。
- 更新日报产品契约、模块设计、README、Skill 提示和回归测试。

## 相关文件

- [src/memory.py](../../src/memory.py) — 跑步模态、教练兜底和 ACWR 口径修复
- [src/coach.py](../../src/coach.py) — 在线洞察来源标记
- [src/mcp_server.py](../../src/mcp_server.py) — MCP 统一洞察管线
- [web/templates/dashboard.html](../../web/templates/dashboard.html) — 显示完整结构化洞察与来源
- [src/render.py](../../src/render.py) — 静态 HTML 显示洞察来源
- [docs/product/daily-report.md](../product/daily-report.md) — 同步产品规则
- [docs/design/memory-system.md](../design/memory-system.md) — 同步技术规则

## 验证

- `pytest -q tests/test_memory.py tests/test_render.py tests/test_coach_provider.py tests/test_web.py`
- 结果：136 passed。
- `pytest -q tests/test_coach_runtime.py tests/test_coach_provider.py tests/test_memory.py tests/test_render.py tests/test_web.py`
- 结果：159 passed。
- `python -m py_compile src/memory.py src/main.py src/coach.py src/mcp_server.py src/render.py`
