# Bug: 轻松跑的距离、时长与强度约束互相矛盾

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 训练方案候选、旧方案兼容、训练首页与训练前说明

## 现象

一节标记为 Easy Run 的课程同时显示 13.7 km 与 60 分钟，并把主体拆为 40 分钟。该组合隐含约 4:23/km 的总平均配速；在用户近期稳定跑步参照约 5:22/km 时，不可能作为轻松跑执行，却被展示为正常课程。

## 根因

旧方案兼容层只根据 `primary_completion` 展示距离或时长，没有验证二者的隐含配速。规则兜底和时间容量计算还残留固定 `5 min/km`、`6 min/km` 换算，既不是个人事实，也没有阻止 AI 候选输出矛盾的双目标。

## 修复方案

新增 `training_prescription.pacing_guard`。当轻松/恢复课同时有总距离和总时长时，系统以至少三条近期有效跑步样本的中位配速作为参照；隐含配速快于参照 8% 以上即标记 `requires_review`。页面和训练前说明不再要求同时追逐距离与时长，而是收紧为可对话的单一时长执行，保留原距离作为重规划依据。Candidate Normalizer 与 Validator 共享同一规则，拒绝不一致的 AI 课程；缺少个人事实时不再以固定速度补造容量或第二硬目标。

## 相关文件

- [src/training_planning.py](../../src/training_planning.py) — 配速一致性合同、个人化时间容量与 Validator 拦截。
- [src/training.py](../../src/training.py) — 近期跑步配速参照、规划事实传递与训练前说明收紧。
- [web/templates/training.html](../../web/templates/training.html) — “安排待修正”安全呈现。
- [docs/product/training-experience.md](../product/training-experience.md) — 用户可见执行规则与 AI 方法学边界。
- [docs/design/training-system.md](../design/training-system.md) — `pacing_guard` 数据合同与方法学参考层。

## 验证

- 13.7 km / 60 分钟、近期参照 5:22/km 的 Easy Run 被标记 `requires_review`，并显示约 4:23/km 的冲突依据。
- Validator 拒绝同样的不一致 AI 候选。
- 训练前说明把该课程收紧为单一可对话时长目标。
- 完整测试在交付前执行。
