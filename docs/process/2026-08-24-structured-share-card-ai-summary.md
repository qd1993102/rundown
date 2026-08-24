# 结构化 AI 分享卡摘要

- **日期**: 2026-08-24
- **类型**: feature / bugfix

## 背景与动机

多次跑步日报的完整教练分析使用 `第N次跑步` 文本前缀，而分享卡按类别前缀首位匹配，导致有效训练分析漏展示。直接放宽为全文展示又会增加隐私泄漏和卡片过长风险。

## 方案选择

采用一次 AI 调用返回两部分结果：完整 `CoachInsight` 供日报使用，结构化 `share_card` 供分享卡使用。每次跑步一个摘要，服务端 Schema 限制条数和长度，前端再次执行禁止词过滤；历史日报没有 `share_card` 时走兼容回退，不重新生成报告。

## 实现步骤

1. 扩展 `CoachInsight` 输出校验，规范化可选 `share_card`。
2. 更新日报 Skill，要求 AI 基于完整训练事实生成短摘要并排除健康隐私与建议。
3. 更新 Canvas Adapter，优先读取结构化摘要，旧日报识别 `第N次跑步` 前缀。
4. 增加 Schema、结构化摘要、禁止词过滤和多 session 回退测试。
5. 同步产品、技术设计、运营验收、Bug 记录和 CHANGELOG。

## 遇到的问题与解决

旧日报没有结构化字段，不能直接要求用户重新生成报告才能分享。因此保留旧观察回退，同时限制文本长度并继续执行禁止词整体过滤。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/share-card.md](../product/share-card.md)
- Design: [docs/design/share-card.md](../design/share-card.md)
- Bugfix: [docs/bugfixes/2026-08-24-share-card-structured-ai-summary.md](../bugfixes/2026-08-24-share-card-structured-ai-summary.md)
