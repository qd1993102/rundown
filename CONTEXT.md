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

## Reports and Reflection

**Report**:
报告中心的核心回顾快照：对某个固定时间范围内的事实、数据质量、计划上下文、运动表现、恢复表现、目标进程和教练解释形成只读结论；它可以提出下一周或下一阶段建议并引导到训练调整，但不能直接改写 Training Scheme。
_Avoid_: Training Scheme, sync task, automatic plan update

**Daily Report**:
以一个本地自然日为范围的 Report，解释当日活动、恢复、数据截止时间和当日计划匹配；用户界面称为“日报”。
_Avoid_: Weekly Review, current plan, live activity feed

**Weekly Review**:
以一个 Natural Week 为范围的 Report，作为报告中心的主要决策材料，解释执行、能力、表现、恢复、同步完整性和 Progression Decision；它可以提出下一周建议，阶段边界上可以提出下一阶段建议，但确认调整属于 Training。
用户界面称为“周复盘”，且只从报告中心进入；训练页只提供带日期的深链，不再提供并列复盘入口。
_Avoid_: Weekly Plan, Adjustment Proposal, automatic progression, Training Week Review

**Observed Rest Day**:
Activity Data Coverage 已确认且没有 Actual Activity 的自然日；它描述已观察事实，不等于 Weekly Plan 中安排的休息日。
_Avoid_: unsynced day, missing data, planned rest day

**Athlete Baseline**:
由运动者近期 Actual Activity、身体状态和有效档案形成的当前能力参照，用于解释一次训练的相对强度；它不是固定的通用配速表。
_Avoid_: Athlete Capacity Profile, population threshold, static pace zone, daily report history

**Athlete Capacity Profile**:
由当前可持续能力、历史已证明能力、表现上限、中断背景和数据置信度组成的跨时间能力画像；它用于决定训练方案从哪里开始以及可以多快推进，不等于当天恢复状态或历史峰值。
_Avoid_: Athlete Baseline, current readiness, personal best alone, latest weekly mileage

**Athlete Habit Profile**:
由长期 Actual Activity、Execution Record 和 Training Feedback 观察出的训练频率、常见训练日、训练连续性、关键课执行习惯和中断/恢复模式；它由系统推断，不要求运动者另填一套能力表单，供报告解释和训练方案制定共同使用。
_Avoid_: declared availability only, calendar editor, adherence score, user-maintained profile

**Training Session Analysis**:
基于 Actual Activity、Athlete Baseline 和数据质量形成的可追溯训练解释，包含 Training Type、Terrain Attribute、置信度和判断证据。
_Avoid_: raw activity, provider label, AI prose

**Training Type**:
Training Session Analysis 中描述主要训练结构与生理刺激的分类，当前跑步结构分类为有氧、节奏、间歇、变速、混合或未知；它与 Terrain Attribute 相互独立。
_Avoid_: activity type, terrain, workout name

**Training Day Analysis**:
从同版本 Training Day Fact 和活动级 Training Session Analysis 生成的确定性单日分析，包含覆盖状态、结构证据、质量课候选与 gaps；日报、周复盘和方案草稿共享，且不等同于日报正文或逐日 AI 解释。
_Avoid_: Daily Report, AI prose, provider-specific analysis

**Quality Session Fact**:
满足统一结构、置信度和主证据门槛的质量课事实；只允许间歇、节奏、变速，或带有效快慢组的混合结构，所有指标逐项表达 value 或 gap。
_Avoid_: workout title keyword, provider label, long run by distance alone

**Terrain Attribute**:
Training Session Analysis 中描述路线环境和爬升特征的独立维度，允许平路、坡地、越野、山地或未知；它不替代 Training Type。
_Avoid_: Training Type, total elevation alone, provider sport type

## Training Planning

**Training Goal**:
运动者希望在明确日期完成的目标赛事结果，至少包含赛事、目标距离、目标日期和 `goal_intent=completion|performance`；`completion` 表示以安全完赛为首要结果，`performance` 表示以目标成绩或可验证突破为首要结果。它是目标信息的唯一真相源，定义方向，但不包含周跑量或具体训练安排。第一版同一时间只保留一个 active Training Goal，不创建无赛事的一般运动目标；已有 active goal 或 Current Training Scheme 时不得创建第二个目标，目标日期变化必须走 Goal Rescheduling。
_Avoid_: Training Scheme, weekly target, weekly mileage target, workout

**Feasibility Recommendation**:
当 Training Goal 无法安全形成训练方案时，AI 基于差距与证据给出的唯一目标修改建议；它只包含一项对原目标偏离最小的修改，不是训练方案，也不自动改写目标。
_Avoid_: alternative route list, parallel draft, automatic goal mutation, Training Scheme Draft

**Training Scheme**:
为达成一个目标赛事 Training Goal 而形成的长期训练策略，引用该目标并在确认时保存目标快照，包含阶段、周结构、负荷方向、约束和安全边界；完整草稿必须来自通过 Validator 的 AI 候选，不存在规则生成的第二套长期方案。它可以处于待确认、已排期或执行中，用户界面称为“训练方案”。
_Avoid_: 运动大纲, deterministic fallback scheme, parallel plan, secondary plan, Weekly Plan, Daily Workout, generic advice

**Current Training Scheme**:
占用账户唯一训练主线的非终态 Training Scheme，状态只包含 `draft`、`scheduled`、`active`。一个 neurun Account 同一时间最多一份，草稿也不能并行创建；第一版没有暂停与恢复生命周期。
_Avoid_: active plan only, scheme history, multiple drafts, paused scheme, successor queue

**Weekly Training Availability**:
运动者建立方案时确认的常规每周可训练星期集合和统一单次最长时间；它不表达早晚时段、逐日时长、日期例外、请假或旅行安排。方案生效后的临时变化通过 Training Feedback 提交；长期星期集合变化也复用该入口，并在同一 Training Scheme 下形成从下一个自然周开始生效的新 Scheme Version，当前周不回溯或重排。
_Avoid_: calendar editor, time slot, per-day duration, exception date, Temporary Unavailability setup field

**Temporary Unavailability**:
运动者有明确起止日期的暂时不可训练约束，例如出差或事务冲突；它作为调整方案的输入，在同一 active Training Scheme 内形成休息或降载安排，不是方案生命周期状态。无法给出恢复日期且不再继续备赛时，运动者结束并归档方案。
_Avoid_: paused scheme, injury management, indefinite hold, new training mode

**Goal Rescheduling Preview**:
运动者提出新目标日期后、确认改期前生成的只读替代安排，包含新目标、候选训练草稿、新旧差异和风险；它不占用 Current Training Scheme，不提供今日训练，生成失败或过期都不改变当前目标与方案。
_Avoid_: second current scheme, scheduled successor, active plan, committed goal change

**Goal Rescheduling**:
运动者查看 Goal Rescheduling Preview 后明确确认目标日期变化，并原子更新同一 Training Goal、废弃当前 Training Scheme、保存替代草稿的过程；旧方案继续作为历史事实保留，替代草稿再次启用确认前不成为训练依据。
_Avoid_: new goal, parallel scheme, silent target-date edit, in-place active plan mutation, generate-after-commit

**Target Date Closure**:
Training Goal 的本地目标日期结束后，系统在次日首次读写时幂等结束 Current Training Scheme 的生命周期；已匹配赛事活动时记为 completed，否则以 `target_date_reached` 原因 archived，不继续排课、不伪造完赛，并释放唯一方案主线。
_Avoid_: post-race training scheme, indefinite active state, assumed race completion, background-job dependency

**Scheme Setup Journey**:
在没有当前 Training Scheme 时，通过“目标赛事 → 现实约束 → 方案预览与开始确认”三个页面建立方案的流程；能力档案由系统自动生成并只读展示，运动者只确认目标、Weekly Training Availability 和最终开始日期。
_Avoid_: five-step wizard, mandatory capacity review, capacity editor, long form, automatic activation, goal duplication

**Activation Preview**:
运动者启用 Training Scheme 前对唯一推荐开始安排、草稿后新增训练、能力变化、本周剩余安排和风险的待确认说明；运动者只确认或修改开始日期，它不会自行使草稿生效。
_Avoid_: activation, active plan, Adjustment Proposal

**Activation Schedule**:
运动者确认 Training Scheme 的开始日期后形成的启用安排；起始负荷路径由系统依据能力与恢复事实确定，不作为用户选择项。未来开始时保持 scheduled，只有到 `effective_from` 才允许方案成为当日训练依据。
_Avoid_: Activation Preview, Scheme Version, calendar reminder

**Activation Cancellation**:
运动者撤销尚未生效的 Activation Schedule，并回到同一 Training Scheme 的草稿/启用预览；它保留目标、方案内容和历史确认记录，继续占用唯一方案主线，不等于放弃或归档方案。
_Avoid_: scheme archival, release current scheme, delete draft, active-plan cancellation

**Training Journey Abandonment**:
运动者明确放弃目标与方案时，将 active Training Goal 和可空 Current Training Scheme 一起归档并保留历史，释放目标与方案唯一主线；草稿页称为“放弃目标与方案”，执行中称为“结束备赛”。
_Avoid_: Activation Cancellation, physical deletion, goal-only orphan, keep editable draft

**Partial First Week**:
Training Scheme 在自然周中途生效时，从生效日到当周周日的本周剩余安排；它只覆盖剩余日期，不补排此前课程，也不计作完整阶段周。
_Avoid_: Bridge Week, Week 1, recovery week, backfilled weekly plan

**Weekly Plan**:
Training Scheme 在一个自然周内的具体安排，包含每天的计划训练、休息日和周目标。
_Avoid_: Training Scheme, weekly summary

**Progression Decision**:
由报告中心基于本周执行、身体适应、当前可持续能力、历史已证明能力、运动习惯、恢复和数据置信度形成的内部下一步判断，固定为加速、正常推进、保持、降载、重规划或等待数据；它是报告向训练传递的结构化建议，重大变化仍需运动者在训练页确认。它是规则与审计语言，不直接作为用户界面的状态名称。
_Avoid_: user-facing result, calendar rollover, completion score, automatic plan rewrite

**Progression Result**:
Progression Decision 面向运动者的简化结果，只显示“按原方案继续”“建议调整方案”或“数据不足，请先补充或同步”；具体内部判断和证据收纳在“为什么这样建议”中。
_Avoid_: Progression Decision enum, raw status code, automatic confirmation

**Plan Execution Summary**:
一个 Natural Week 内计划量、实际量，以及完成、部分完成、跳过、未匹配和计划外训练数量的原始事实汇总；它不生成综合完成率、权重分数或单一表现评分。
_Avoid_: completion score, weighted adherence rate, readiness score

**Natural Week**:
按运动者本地日期从周一到周日的固定统计周期；计划量与实际量比较只使用所属 Natural Week 内的 Actual Activity，不使用滚动七天窗口。
_Avoid_: rolling 7 days, recent week

**Daily Workout**:
Weekly Plan 中某一天可执行的 Training Prescription；用户界面称为“今日训练”。它是计划事实，不是实际活动或日报结论。
_Avoid_: Daily report, generic recommendation, actual activity

**Training Prescription**:
面向跑者的可执行课程说明：以训练目的、训练块、完成目标、个体化强度目标、可观察的体感校验、完成标准和调整边界组成。配速与心率只在个人事实足够时出现；RPE 是辅助校验，不是默认主语言。课程由训练刺激和结构组合而成，不是固定的课程名称枚举。
_Avoid_: RPE-only instruction, generic quality workout, immutable course library

**Pace Calibration Profile**:
由用户确认强度锚点和近期可信同类 Training Session Analysis 形成的版本化个人 Z1–Z5 配速证据视图；它为 Daily Workout 提供基础区间，但不等于当天状态、通用配速表或生效 Training Scheme。
_Avoid_: static pace zone, target-race pace table, current readiness, automatic plan update

**Execution Record**:
一项 Daily Workout 与最多一条同日 Actual Activity 及用户完成反馈之间的版本化关联判断，允许完成、部分完成、跳过或未匹配；它只记录发生了什么。第一版只接受高置信度自动匹配，不提供人工关联、跨日关联或多活动拼接，也不把不同训练自动判为替代完成。没有匹配活动始终是 unmatched，只有运动者明确选择“今天没练”才是 skipped；之后到达的高置信度活动可以通过新记录更正该判断并保留原确认。部分完成或跳过不会自动创建补课、顺延后续训练或转移未完成训练量。
_Avoid_: Activity, completion guess, manual activity linking, multi-activity merge, makeup queue, automatic rescheduling

**Unplanned Activity**:
无法归入当天 Planned Workout，或在已完成计划课之外发生的 Actual Activity；用户界面标记为“计划外训练”。它计入实际运动与负荷，但不抵扣未来课次、不自动生成调整提案或重排方案；需要变化时由运动者显式发起“重新生成安排”。
_Avoid_: substituted workout, future-workout credit, automatic replan

**Training Feedback**:
运动者在训练前通过“时间或安排变了”“身体状态不好”“疼痛或不适”三个入口提交的主观状态或现实约束，或在训练完成记录中补充的训练后事实；天气和场地属于安排变化的原因，不是独立入口。它是调整方案或执行判断的输入，不是已经生效的修改。
_Avoid_: seven-way quick menu, Adjustment Proposal, Execution Record

**Same-day Safety Guidance**:
疼痛反馈产生的当日只读安全指引，直接把原课程收紧为停止、仅低强度活动或建议进一步评估；它不修改 Training Scheme 或 Weekly Plan，也不创建 hold、pause 或伤病状态。拒绝后续 Adjustment Proposal 不能解除该指引。
_Avoid_: Safety Hold, scheme version, medical diagnosis, user-approved plan change

**Local Schedule Adjustment**:
用户确认只影响今天或当前自然周的 Adjustment Proposal 后形成的不可变局部安排，覆盖 Weekly Plan 中指定课次但不产生新的 Scheme Version；它保留原安排、调整后安排和提案依据，并参与本周执行比较。临时改日期走该路径，不打开新设置页、不废弃 Training Goal 或当前 Training Scheme。
_Avoid_: Training Scheme revision, silent workout edit, future phase change, reusable weekly template

**Adjustment Proposal**:
基于当前方案版本和一条 Training Feedback 生成、尚未生效的候选修改，包含差异、原因、影响和风险；用户只能确认调整或保持原计划。确认局部范围时形成 Local Schedule Adjustment，改变长期边界时才形成新 Scheme Version。补充事实必须提交为新的结构化 Training Feedback，并以新提案原子替代同范围的旧待确认提案；它不是可持续对话。
长期可训练日变化生成失败时保持当前方案和本周安排不变，不写入半份 Scheme Version；只返回失败状态并允许用户主动重试，不自动重试、排队或补写迟到结果。
_Avoid_: active plan, direct AI write, feedback, discussion thread

**Scheme Version**:
一次用户确认后形成的不可变 Training Scheme 快照，用于确定当前生效内容并解释历史安排。
_Avoid_: draft, proposal, edit timestamp
