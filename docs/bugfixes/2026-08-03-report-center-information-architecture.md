# Bug: 报告中心与日报详情把多个任务和层级混在一起

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: major
- **影响范围**: 报告中心、日报详情、训练/同步跨页引导、周复盘归档

## 现象

报告入口同时平铺日报生成、周报生成、日报列表和调整记录，用户难以判断当前任务。日报详情又让计划状态、
恢复指标、AI 洞察、训练事实和负荷并列展示，无法快速理解当天对训练计划意味着什么。

## 根因

报告域已经按日、周和调整记录分开存储，但页面仍按一次性仪表盘渲染；周中请求也会落入正式周报路径，
使“本周进度”和“完整周复盘”的决策边界不清晰。

## 修复方案

- 报告中心新增日报、周复盘和调整记录二级导航，三个范围互斥展示。
- 日报详情固定为“今日结论 → 计划执行与目标进展 → 当日训练事实 → 可展开的数据依据”。
- 当前自然周返回不持久化的 `weekly_checkpoint / not_final`；只有已结束周可写正式周复盘和进程决定。
- 同步与训练的深链携带 `tab`、日期或周范围；同步不生成报告，报告不确认调整。

## 相关文件

- [src/training.py](../../src/training.py) — 周中检查与正式周复盘写入边界
- [web/templates/reports.html](../../web/templates/reports.html) — 报告中心二级导航
- [web/templates/chat.html](../../web/templates/chat.html) — 日报详情层级
- [web/templates/training.html](../../web/templates/training.html) — 训练到周复盘引导
- [web/templates/sync.html](../../web/templates/sync.html) — 同步回跳上下文
- [docs/product/daily-report.md](../product/daily-report.md) — 报告中心规则

## 验证

- 报告页同一时刻只显示一个二级页，日报默认显示。
- 当前自然周请求不写周报且状态为 `not_final`；已结束自然周才可归档。
- 同步回到原日报日期；训练周链接直达周复盘。
- 运行完整 `pytest`，要求零失败。
