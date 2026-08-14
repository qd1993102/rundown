# Bug: 训练前说明暴露内部依据且等待在线推理

- **发现日期**: 2026-08-03
- **修复日期**: 2026-08-03
- **严重程度**: major
- **影响范围**: 训练页训练前说明、Coach Skill 调用路径

## 现象

训练前说明会直接呈现 `planned_session`、`recovery_snapshot`、`ACWR`、Skill 名称和调用输出等技术内容；
同时需要等待在线 Skill，用户准备开始训练时响应明显变慢。

## 根因

训练前说明复用了面向编排器的原始事实和在线解释结果，没有为运动者界面建立独立、低延迟的输出合同。

## 修复方案

- 训练前说明改为本地即时规则，不等待在线 Skill。
- Web DTO 仅保留目的、可执行步骤、替代方式和停止/收紧信号。
- 恢复与负荷仍只作为服务端决策输入，不暴露字段名、数值、trace 或模型降级原因。

## 相关文件

- [src/training.py](../../src/training.py) — 本地即时训练前说明生成
- [web/templates/training.html](../../web/templates/training.html) — 只渲染运动者可执行内容
- [docs/product/training-experience.md](../product/training-experience.md) — 训练前说明交互规则
- [docs/design/training-system.md](../design/training-system.md) — DTO 与低延迟设计

## 验证

- 训练前说明生成不调用在线 Skill。
- 返回内容不含 `ACWR`、内部证据、trace 或降级原因。
- 运行完整 `pytest`，要求零失败。
