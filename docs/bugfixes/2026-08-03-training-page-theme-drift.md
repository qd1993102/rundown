# Bug: 训练方案页面主题配色与主应用不一致

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: minor
- **影响范围**: 训练方案 Web 页面主题切换与跨页面视觉连续性

## 现象

训练页使用了独立的深绿暗色底和棕橙色运动主题；同名主题切换到报告、同步或我的页时会显示另一套颜色，造成页面视觉不收敛。

## 根因

`web/templates/training.html` 自行维护了一组 `fresh / sport / dark` 主题令牌，而不是复用业务页面已经共享的令牌合同。

## 修复方案

将训练页的三套主题令牌对齐到主应用的共享色值；训练组件仍通过语义令牌表达当前态，不再定义模块专属配色。增加模板级回归测试，校验训练页的核心颜色令牌与共享页面一致。

## 相关文件

- [web/templates/training.html](../../web/templates/training.html) — 统一三套主题令牌。
- [tests/test_web.py](../../tests/test_web.py) — 覆盖主题令牌合同。
- [docs/product/training-experience.md](../product/training-experience.md) — 明确视觉连续性验收。
- [docs/design/training-system.md](../design/training-system.md) — 记录 Web 主题合同。

## 验证

运行 `pytest tests/test_web.py`，确认训练页面的 fresh、sport、dark 核心主题令牌与共享业务页面一致。
