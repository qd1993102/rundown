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

## Activity Understanding

**Actual Activity**:
运动者实际完成并同步到 neurun 的一次运动事实记录；它独立于计划安排，也不等同于系统对训练内容的解释。
_Avoid_: Daily Workout, Execution Record, provider label

**Activity Data Coverage**:
neurun 已完成指定日期运动记录同步的证据；没有 Actual Activity 只有在该日期覆盖已确认时才能解释为 Observed Rest Day。
_Avoid_: health data presence, empty activity list, generated daily report

**Observed Rest Day**:
Activity Data Coverage 已确认且没有 Actual Activity 的自然日；它描述已观察事实，不等于 Weekly Plan 中安排的休息日。
_Avoid_: unsynced day, missing data, planned rest day

**Athlete Baseline**:
由运动者近期 Actual Activity、身体状态和有效档案形成的当前能力参照，用于解释一次训练的相对强度；它不是固定的通用配速表。
_Avoid_: population threshold, static pace zone, daily report history

**Training Session Analysis**:
基于 Actual Activity、Athlete Baseline 和数据质量形成的可追溯训练解释，包含 Training Type、Terrain Attribute、置信度和判断证据。
_Avoid_: raw activity, provider label, AI prose

**Training Type**:
Training Session Analysis 中描述主要训练结构与生理刺激的分类，当前跑步分类为有氧、节奏、间歇或未知；它与 Terrain Attribute 相互独立。
_Avoid_: activity type, terrain, workout name

**Terrain Attribute**:
Training Session Analysis 中描述路线环境和爬升特征的独立维度，允许平路、坡地、越野、山地或未知；它不替代 Training Type。
_Avoid_: Training Type, total elevation alone, provider sport type

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

**Natural Week**:
按运动者本地日期从周一到周日的固定统计周期；周目标完成度只使用所属 Natural Week 内的 Actual Activity，不使用滚动七天窗口。
_Avoid_: rolling 7 days, recent week

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
