# Bug: 训练草稿未充分衔接近期训练且周期阶段可能倒置

- **发现日期**: 2026-08-09
- **修复日期**: 2026-08-09
- **严重程度**: major
- **影响范围**: 训练草稿生成、确定性排课、周期审阅和草稿 Prompt

## 现象

草稿排课没有明确消费最近两天的训练事实，可能在长跑或质量课次日安排强度；确定性兜底还可能把长距离放在非周末。周期阶段虽然有默认顺序，但 AI 输出缺少明确的顺序约束，倒置时没有审阅提示。

## 根因

上一完整自然周是周量基线，但短期衔接事实尚未作为独立输入传入排课层；兜底排课直接选择最后一个可训练日作为长距离日；Validator 只做结构底线，未对阶段倒置提供非阻断审阅。

## 修复方案

- `PlanningFactPack` 增加独立的 `short_term_training`，仅用于最近两天的排课顺序，不改变固定周基线。
- Normalizer 和兜底排课在长跑/质量课次日将强度课降为衔接恢复跑；兜底长距离优先使用可训练周末。
- Normalizer 对周期阶段倒置写入 `PERIODIZATION_ORDER` attention，不静默重排、不阻止草稿，交由用户确认或重新生成。
- 在草稿和重规划 Skill 中明确短期衔接和基础/适应→专项强度→高峰→减量顺序。

## 相关文件

- [src/training.py](../../src/training.py) — 采集最近两个已结束自然日的训练事实
- [src/training_planning.py](../../src/training_planning.py) — 衔接保护、周末长距离和周期顺序审阅
- [docs/product/training-experience.md](../product/training-experience.md) — 用户可见排课与周期规则
- [docs/design/training-system.md](../design/training-system.md) — PlanningFactPack 和技术验收合同

## 验证

- `pytest -q tests/test_training_planning.py`
- `python3 -m py_compile src/training.py src/training_planning.py`
- `git diff --check`
