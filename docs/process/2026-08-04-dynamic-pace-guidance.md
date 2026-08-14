# 个体化动态配速处方 v1

- **日期**: 2026-08-04
- **类型**: feature

## 背景与动机

每日训练已有 Z1–Z5 相对强度，但缺少可直接执行的个人配速。产品要求结合五级模型和近期运动表现动态建议，同时保持默认信息精简：结果和安全行动直接显示，推荐理由由用户主动展开。

## 方案选择

新增独立的 `PaceCalibrationProfileBuilder`，不把普通跑步整体均速机械外推到全部 Zone。Builder 优先读取用户确认阈值，并按 Training Session Analysis 的课程类型、地形、置信度和间歇工作段筛选最近 28–42 天可比样本。`DailyPaceAdjustmentEngine` 在读取训练首页时生成只读覆盖，只允许保持、放慢或降级为体感，不写回方案。

前端直接展示今日区间或“配速按体感”的安全结果，使用原生 `details/summary` 承载依据、事实截止时间和置信度。训练简报沿用同一结构，MCP 保留完整结构化字段供调用方选择展示层级。

v1 不接入天气同场景比较，不因单次表现自动提高基础配速，也不固化日报历史配速快照；这些边界保留在原产品与设计文档中。

## 实现步骤

1. 让训练分析读取路径带回内部 `features`，支持识别间歇工作段，同时避免在活动匹配公共 DTO 中泄露大字段。
2. 建立版本化 Pace Calibration Profile、质量门禁、分区样本和稳定版本哈希。
3. 将恢复、负荷、近期同类表现与疼痛约束应用为当天保守覆盖。
4. 接入训练首页、每周详情、训练前说明、Coach/MCP 描述和折叠解释交互。
5. 增加领域、服务集成和页面合同测试，并同步产品、技术、README、术语与 CHANGELOG。

## 遇到的问题与解决

工作区同时包含尚未提交的报告与训练系统改动，因此本次只定向修改动态配速相关代码和文档，不覆盖其他变更。测试按新增领域测试、训练服务集成、页面静态合同和全量回归分层执行。

## 验证

- 动态配速专项、训练服务与页面合同测试通过；
- `python3 -m compileall -q src` 通过；
- 全量 `pytest -q`：360 passed；
- `git diff --check` 通过。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
