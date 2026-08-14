# Bug: 报告日活动被误称为前一日训练

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: major
- **影响范围**: 日报详情、AI 洞察、聊天上下文、CLI、MCP、HTML/PNG 导出和 Front Matter 合同

## 现象

用户选择 2026-08-02 生成日报，报告实际汇总了 2026-08-02 的三次活动，但详情标题和 AI 洞察
仍显示“昨日训练”“昨天完成”，使用户误以为这些活动属于 2026-08-01。报告 Markdown 正文同时
显示“今日训练”，同一份报告内部口径互相矛盾。

## 根因

日报最初按“早晨回顾昨日”的信息架构设计，Front Matter 使用 `yesterday_activities`，页面和本地
洞察也写死“昨天”。后续生成逻辑已经改为按 `target_date` 查询 `[D, D]` 当天活动，产品也明确为
“报告日活动”，但旧字段和展示文案没有一起迁移，形成数据正确、语义错误的半迁移状态。

## 修复方案

- 将报告 `date=D` 定义为活动归属的唯一时间锚点，继续严格查询 `[D, D]`。
- 新增规范字段 `daily_activities`；新报告同时写入旧字段兼容别名，读取器优先规范字段并回退旧字段。
- 页面、AI Prompt、本地洞察、聊天上下文、CLI、MCP、Markdown、HTML 和 PNG 统一为“当日训练”
  或明确报告日期；`last_night_sleep` 的睡眠周期含义保持不变。
- 在线 AI 返回后递归规范化文本值：“今天/今日”改为“当日”，“昨天/昨日”和“明天/明日”改为
  报告日期前后对应的 ISO 日期；结构化字段名保持不变。
- 使用 `D-1` 与 `D` 不同活动的回归测试，证明 `D` 日报只包含 `D` 的活动。

## 相关文件

- [src/memory.py](../../src/memory.py) — 规范字段、兼容读取、日期查询和确定性文案
- [src/web.py](../../src/web.py) — Dashboard 与聊天上下文使用报告日语义
- [web/templates/chat.html](../../web/templates/chat.html) — 页面和图片导出统一为当日训练
- [docs/product/daily-report.md](../product/daily-report.md) — 补充日期语义与验收标准
- [docs/design/memory-system.md](../design/memory-system.md) — 补充字段迁移和兼容策略

## 验证

- 构造 2026-08-01 室内跑与 2026-08-02 户外跑，生成 2026-08-02 日报后仅包含户外跑。
- 新报告同时包含内容一致的 `daily_activities` 和 `yesterday_activities`，旧报告仍可读取。
- 确定性洞察、Markdown、Web 模板与 Canvas 导出不再显示“昨日训练”或“昨天完成”。
- 运行完整 `pytest`，要求零失败。
