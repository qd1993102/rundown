# Workout Steps v2 逐段训练处方

- **日期**: 2026-08-04
- **类型**: feature

## 背景与动机

原训练处方能够展示课名、总时长、主体 Zone 和热身/主体/放松 blocks，但变速、间歇与法特莱克仍可能只写“工作段与恢复段交替”。运动者无法知道何时快、何时慢、每段多长、重复几组，也无法判断个人配速事实不足时应该怎样安全执行。

## 方案选择

在既有 `training_prescription` 内新增 `structure_version=2` 和规范 `steps`，使用顺序叶子步骤与可嵌套 `repeat` 表达课程。AI 只负责训练刺激、组数、工作—恢复关系和相对强度意图；`StepTargetResolver` 使用个人配速证据逐段解析数字目标，不足时降级为可观察体感；Validator 负责结构、单位、总量、高强度边界和完成分类。

旧 `blocks` 保留为从 v2 单向生成的兼容投影。没有从历史模糊 blocks 反向推断组数，避免把猜测写成教练处方；历史生效方案也不被静默改写。

## 实现步骤

1. 在 `training_planning.py` 增加 v2 步骤构建、摘要、blocks 投影、总量展开与确定性校验；保守兜底也生成完整步骤。
2. 将 `draft-training-scheme`、`revise-training-scheme` 的 Prompt、输出示例、Schema 和版本同步升级；缺少步骤、组数或工作/恢复段的 AI 结果会被拒绝并进入明确兜底。
3. 在 `training_pace.py` 增加逐叶子步骤目标解析；Z5 额外校验历史样本与工作段时长是否可比。
4. 训练首页、周课程详情和训练前说明复用同一结构；Web 展示热身、重复快慢段、放松、分段目标、转场和完成门槛。
5. 日报计划执行摘要保存逐段计划结构，并在同步摘要缺少逐段完成事实时明确说明证据不足；真实 Provider 到逐组 `ExecutionRecord` 的匹配留给后续实现。
6. MCP 描述和返回 DTO 同步增强，不新增写入路径，也不改变用户确认边界。

## 遇到的问题与解决

- 在线 AI 可能超时或继续输出旧 v1 blocks：Schema 与领域 Validator 双层拒绝，确定性兜底走同一 v2 校验，来源继续显示为 `deterministic_fallback`。
- 数字配速不能由模型编造：AI 只输出 `intensity_intent`；个人证据不匹配当前步骤时只将该步骤解析为 `feel_only`。
- 旧方案缺少可恢复的组数语义：保持 v1 只读兼容，通过新草稿或待确认调整进入 v2，不做破坏性迁移。

## 验证

- 覆盖确定性变速/间歇重复组、AI 缺少 steps 拒绝、总量不一致拒绝、Z5 工作段时长证据不匹配降级、工作/恢复段独立目标、训练前说明和 Web 时间线合同。
- 针对性测试通过；全量 `.venv/bin/pytest -q` 通过，共 370 项、零失败。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
