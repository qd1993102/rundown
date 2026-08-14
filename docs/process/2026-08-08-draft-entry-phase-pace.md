# 草稿起始阶段与配速执行信息

- **日期**: 2026-08-08
- **类型**: feature / UX correction

## 背景与动机

草稿虽然已经保存 `entry_review`，但预览没有告诉用户应该从哪个阶段开始；课程卡片也只展示距离，用户无法知道有可靠事实时应该使用什么配速，或在配速证据不足时如何执行。

## 方案选择

- 在现有草稿预览中增加“建议从哪个阶段开始”区块，展示阶段、目的、依据和置信度；它是待确认建议，不自动改变当前方案阶段。
- 草稿课程读取时复用 `PaceCalibrationProfile` 与 `DailyPaceAdjustmentEngine`，写入 `pace_guidance`；可靠区间展示建议配速，数据不足明确降级为可对话体感。
- Prompt 要求所有用户可见自然语言使用简体中文，训练页继续用现有轻量词典处理常见英文课型和阶段名，不增加翻译服务或第二次 AI 调用。

## 实现步骤

1. 在草稿和方案级重规划输出中保存 `entry_phase_recommendation`。
2. 为草稿周课程补全配速校准和当天安全执行信息。
3. 在训练页展示阶段建议与课程配速/体感目标。
4. 补充领域、模板和文档同步测试。

## 验证

- `pytest -q tests/test_training.py tests/test_training_planning.py tests/test_coach_runtime.py tests/test_web.py`
- 全量 `pytest -q`
- 重启 8080 后检查 `/healthz`。

## 关联文档

- 产品：[docs/product/training-experience.md](../product/training-experience.md)
- Design：[docs/design/training-system.md](../design/training-system.md)
- Changelog：[docs/CHANGELOG.md](../CHANGELOG.md)

