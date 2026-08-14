# Bug: 训练课程仅以 RPE 和课名表达，无法直接执行或准确评价

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 训练方案生成、训练首页、训练前说明、日报计划执行分析与 Coach Skill

## 现象

今日训练和本周安排只展示课名、距离、时长和 `RPE 2–3` 等内部强度文本。用户看不到热身、主体、放松、完成标准、调整边界或配速/心率数据是否可用；日报也只能粗略判断当天是否有跑步，不能据课程处方解释执行质量。

## 根因

`Planned Workout` 只持久化了扁平 `intensity` 字段。规则兜底把 RPE 直接写为用户文本，Coach Skill 没有要求结构化训练块；日报执行模型没有读取课程完成目标。

## 修复方案

新增兼容旧方案的 `training_prescription` 读模型，包含完成优先级、训练刺激、训练块、强度目标可用性、通俗体感、完成要求、调整边界和方法依据。Coach Skill 升级为以体系原则组合课程，Validator 阻止缺少个人事实时伪造精确配速或心率，并按训练刺激而非仅课程名检查高负荷间隔。训练首页展示处方和本周重点；日报复用处方比较距离/时长目标。

## 相关文件

- [src/training_planning.py](../../src/training_planning.py) — 处方归一化、规则兜底课程与校验。
- [src/training.py](../../src/training.py) — 旧方案兼容、衔接周和训练前说明。
- [src/memory.py](../../src/memory.py) — 日报计划执行比较。
- [web/templates/training.html](../../web/templates/training.html) — 今日训练与本周处方展示。
- [web/templates/chat.html](../../web/templates/chat.html) — 日报执行对比与课程结构。
- [docs/design/training-system.md](../design/training-system.md) — 处方数据合同与测试策略。

## 验证

- 规则兜底方案含 blocks、通俗体感和不伪造的强度数据可用性。
- 首三周关键训练刺激可形成变速、阈值和间歇处方，医疗限制仍禁止高负荷刺激。
- 日报能够给出计划距离/时长与实际值的比较。
- 相关测试通过，完整测试在交付前执行。
