# neurun Domain Language

neurun 为运动者提供训练数据同步、训练记忆与 AI 教练能力。本词汇表定义产品讨论和代码设计中使用的统一领域语言。

## Access and Registration

**Invitation**:
一份由系统随机生成、不预先绑定邮箱、仅允许创建一个 neurun 账号的一次性 bearer 注册资格；新 Invitation 使用 6 位无歧义大写字母与数字，已发布的旧版长码继续有效；由首个成功使用者获得，只有账号创建成功后才算被使用，并在使用或管理员停用前持续有效。
_Avoid_: Shared invite, email-bound invite, campaign code, promo code

## Accounts and Connections

**neurun Account**:
运动者登录 neurun 并拥有其训练数据与设置的应用身份；当前最多拥有一个活跃 Platform Connection，但领域关系允许未来扩展为多个。
_Avoid_: User key, provider account, Garmin account

**Login Email**:
经格式校验并在 neurun 内唯一的账号登录标识；第一版创建后不可修改且不验证邮箱归属，因此它不是可信联系方式或账号所有权证明。
_Avoid_: Verified email, contact email, recovery email

**Nickname**:
可重复且可修改的账号展示名称，不参与登录、唯一性判断或账号所有权证明。
_Avoid_: Username, account ID, Login Email

**Platform Account**:
运动者在 Garmin、Coros、Huawei 等外部运动平台上的身份，与 neurun Account 相互独立。
_Avoid_: neurun account, application account

**Platform Connection**:
一个 neurun Account 获得持续读取某个 Platform Account 数据授权的关系；第一版每个 neurun Account 最多一个活跃连接。
_Avoid_: Login, provider field, token directory

## Training Planning

**Training Goal**:
运动者希望通过训练达到的明确结果，可包含目标距离、成绩、赛事或一般运动目的；它定义方向，但不包含具体训练安排。
_Avoid_: Training Scheme, weekly target, workout

**Training Scheme**:
为达成一个 Training Goal 而生效的长期训练策略，包含阶段、周结构、负荷方向、约束和安全边界；用户界面称为“训练方案”。
_Avoid_: 运动大纲, Weekly Plan, Daily Workout, generic advice

**Weekly Plan**:
Training Scheme 在一个自然周内的具体安排，包含每天的计划训练、休息日和周目标。
_Avoid_: Training Scheme, weekly summary

**Daily Workout**:
Weekly Plan 中某一天可执行的训练处方，包含训练目的、类型、距离或时长和强度范围；用户界面称为“今日训练”。
_Avoid_: Daily report, generic recommendation, actual activity

**Execution Record**:
一项 Daily Workout 与实际运动数据及用户完成反馈之间的关联判断，允许完成、部分完成、替代完成、跳过或暂未匹配。
_Avoid_: Activity, completion guess

**Training Feedback**:
运动者提交的主观感受或现实约束，例如疲劳、疼痛、时间不足和日程冲突；它是调整方案的输入，不是已经生效的修改。
_Avoid_: Adjustment Proposal, Execution Record

**Adjustment Proposal**:
基于当前方案版本和 Training Feedback 生成、尚未生效的候选修改，包含差异、原因、影响和风险；只有用户确认后才能产生新版本。
_Avoid_: active plan, direct AI write, feedback

**Scheme Version**:
一次用户确认后形成的不可变 Training Scheme 快照，用于确定当前生效内容并解释历史安排。
_Avoid_: draft, proposal, edit timestamp
