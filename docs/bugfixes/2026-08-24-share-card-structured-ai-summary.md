# Bug: 多次跑步日报分享卡漏展示教练分析

- **发现日期**: 2026-08-24
- **修复日期**: 2026-08-24
- **严重程度**: major
- **影响范围**: 日报分享卡 AI 教练区、日报 CoachInsight 输出合同

## 现象

多次跑步的日报中，完整教练分析包含逐次跑步的强度分布、跑步动力学和跑步分析，但分享卡只展示运动概要和教练结论。

## 根因

日报生成器为多次跑步观察添加了 `第N次跑步 ` 前缀，而 Canvas Adapter 只按字符串首位匹配 `强度分布：`、`跑步动力学：` 和 `跑步分析：`，导致逐次观察全部漏匹配；同时旧 Adapter 每类只取第一条，无法表达多 session 内容。

## 修复方案

日报 CoachInsight 增加结构化 `share_card`，由同一次 AI 调用基于完整训练事实生成 AI 精简摘要。摘要按 session 建模并限制条数与长度；前端优先读取该字段并对候选文本执行隐私禁止词二次过滤。对历史日报保留旧观察格式回退，并识别 `第N次跑步` 前缀。

## 相关文件

- [web/static/share-card.js](../../web/static/share-card.js) — 读取结构化摘要并保留旧格式回退。
- [src/coach_runtime/schemas.py](../../src/coach_runtime/schemas.py) — 校验并限制 `share_card` 输出。
- [prompts/skills/review-daily-training/SKILL.md](../../prompts/skills/review-daily-training/SKILL.md) — 明确 AI 精简与隐私规则。
- [tests/test_web.py](../../tests/test_web.py) — 覆盖多 session、禁止词和旧格式回退。
- [tests/test_coach_runtime.py](../../tests/test_coach_runtime.py) — 覆盖字段缺失兼容与长度/条数上限。

## 验证

运行 `pytest -q tests/test_web.py tests/test_coach_provider.py tests/test_coach_runtime.py`，结果为 92 passed。
