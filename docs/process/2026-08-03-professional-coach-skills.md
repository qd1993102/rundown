# 目标赛事专业教练与 Coach Skills 落地

- **日期**: 2026-08-03
- **类型**: feature / architecture

## 背景与动机

原训练方案虽然已经可查看、反馈和确认，但草稿主要由固定比例规则生成，用户看不到目标可行性、完整周期、负荷演进和推理依据。日报、普通对话和课程制定又集中在单体 Prompt 中，无法独立演进。产品进一步明确第一阶段定位为“目标赛事备赛教练”，同时允许无赛事用户先使用持续跑步陪伴并在未来双向转换。

## 方案选择

采用仓库内轻量 Coach Skill runtime，不引入 LangChain。Skill 维护单一教练职责和 Prompt；Python registry 维护版本、事实要求、允许工具、副作用级别与输出模型。课程方案前后分别由确定性 PlanningFactPack/TrainingLoadEnvelope 和 TrainingSchemeValidator 保护，模型失败时返回明确标记的周期化规则兜底。

模式转换由独立 `CoachingModeTransitionProposal` 表达，不让 Skill 或目标日期直接切换。局部调整继续保留确定性安全差异，方案级变化使用独立 `revise-training-scheme`，两者共享原有版本确认门禁。

## 实现步骤

1. 建立 12 个 Coach Skill 包、`coach-core.md`、registry、runner、Context、Schema 和 ToolPolicy。
2. 实现 PlanningFactPack、训练负荷容量、专业草稿生成、四周周期化兜底和安全 Validator。
3. 将训练建立向导切换到专业方案管线，展示生成方式、可行性、证据、完整阶段、首四周和不确定性。
4. 实现持续陪伴、赛事备赛与恢复过渡的持久化状态机和确认/拒绝流程。
5. 接入训练前说明、自然周复盘、局部调整 Skill、完整方案重规划和赛前比赛策略。
6. 同步 Web、MCP 与聊天 Coach 工具，并补充领域、规划、runtime、Web 和 MCP 回归测试。

## 遇到的问题与解决

- 单个可训练日无法满足多日方案的长距离占比约束：将唯一课程定义为受控有氧课，仍由单次时长和周容量保护。
- 旧简单规则默认首周上浮 5%：专业管线改为从已观察负荷保守起步，不无依据加量，并把最近 7 天、28 天和用户确认跑量写回可见证据链。
- Skill 模型输出与安全合同可能漂移：Prompt 明确 JSON 合同，runner 先做输出校验，领域 Validator 再校验增长、恢复周、长距离、强度间隔、可训练日和时长。
- DeepSeek JSON Output 要求提示词显式包含小写 `json`，贡献 Skill 最初因此返回 400；将供应商协议、精确 Schema 示例和脱敏错误分类统一下沉到模型适配层。在线回放继续暴露字符串星期和负荷算术误差后，增加只向下夹紧负荷、机械对齐距离的确定性 Normalizer，Validator 的最终否决权保持不变。
- 二元降级把可修正的阶段周数偏差和真正的安全失败混为一谈：将模型来源、校验状态、推荐状态和页面综合状态拆开；阶段结构完整时用最大余数法对齐赛事时间线并公开调整，挑战目标继续保留 AI 风险与候选路线，无法机械修复时才兜底。
- 模式与方案可能静默分叉：创建草稿时绑定待确认模式提案，只有激活草稿才能确认进入备赛；退出时先关闭 active 读模型并写入历史，再更新模式上下文。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/training-experience.md](../product/training-experience.md)
- Design: [docs/design/training-system.md](../design/training-system.md)
- AI Design: [docs/design/ai-coaching.md](../design/ai-coaching.md)
