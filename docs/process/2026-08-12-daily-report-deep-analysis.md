# 日报训练深度分析与 session-summary 统一收拢

- **日期**: 2026-08-12
- **类型**: feature

## 背景与动机

用户反馈日报“训练细节分析”太简单——目前只有 `TrainingSessionAnalyzer` 的 4 行 evidence
（课型/地形/置信度 + 2-3 条依据）。期望达到“整体水平表 + 强度分布 + 逐段配速节奏”的深度
（如全马配速分析示例）。产品确认：采用 **L1 分段粒度**（现有数据即可，不需要秒级/逐公里）；
**TE 缺失时允许本地估算，但必须显式标记“估算”**。

## 数据可用性调研（基于真实 0811 数据验证）

- `detail_json`（`activity_details` 表）含 `summaryDTO`（TE、强度分钟、海拔范围、最快配速、
  最大步频、moving/elapsed 时长）与 `splitSummaries`（7 段 lap、splitType、逐段指标）——
  全部可确定性消费，此前未解析。
- **无秒级数据**：`detail_json` 无 chartData/metrics 数组；`timeseries` 表是健康序列
  （body_battery 等），非活动 1Hz。因此“record 秒级 2848 采样”与逐公里配速本版不做，
  需同步层新增拉取能力（另立需求）。

## 方案选择

- **扩展 `SessionSummaryFacts` 到 v2**（`summary_extraction.py`），新增 effect /
  elevation_profile / pace_profile / structure_profile，全部可选默认 None 兼容 v1 旧记录；
  版本号 `session-summary-v2`，历史报告保留引用版本。
- **原始 TE 入库、估算在分析层**：同步层 `build_session_summary(detail)` 无个人阈值，只解析
  Provider 原始 TE；日报分析层 `_analyze_training_sessions` 在原始 TE 缺失且
  `AthleteBaseline` 有阈值时调用 `estimate_training_effect()` 合并进展示副本，不改
  `activity_summary_facts` 表。
- **TE 估算**：逐段强度比（心率优先，配速代理）→ zone 分钟加权刺激分 →
  `TE = clamp(1 + 刺激分/40, 1, 5)`；输出带 `estimated=true`、`estimate_method`、
  `estimate_basis`，展示“≈ 估算，依据心率/配速”。
- **结构性课型**（interval/fartlek/structured/steady）由 splitType + work/recovery 识别，
  与 `TrainingSessionAnalyzer` 的强度课型（tempo/aerobic）并存展示。
- **展示**：`render._detail` 从字符串解析重写为 dict 三块卡片（顺带修复其与
  `session_analyses` dict 结构脱节、HTML 详情区实际渲染失败的问题）；Markdown 正文同步三块。

## 实现步骤

1. 产品文档：`daily-report.md` 新增 3.3.2（三块内容、规则、验收 46–49）。
2. 设计文档：`summary-extraction.md` 升级 v3，追加 §9（v2 schema、数据流、估算、复合识别）。
3. `summary_extraction.py`：v2 dataclass + 解析（effect/elevation/pace_profile/structure_profile
   + volume.elapsed_duration_s + structure.max_cadence）+ `estimate_training_effect`。
4. `memory.py`：`_analyze_training_sessions` 合并 TE 估算；`_render_daily_body` 训练细节分析
   升级三块（`_deep_analysis_lines`）。
5. `render.py`：`_detail` dict 版三块卡片 + CSS。
6. `review-daily-training` SKILL 增加引用 v2 事实与估算标记规则。
7. 测试：summary_extraction v2（15 项）、render 深度分析（4 项）、storage 版本号升级。

## 遇到的问题与解决

- **估算 TE 爆表**：初版 `刺激分/1.5` 把 89min 轻松跑算出 5.0；校准为 `1 + 刺激分/40`，
  0811（89min × 0.6 权重 = 53.4）→ ≈3.8，接近“提升课”量级，合理。
- **band key 解析**：`-inf-130` 以 `-` 开头，`split("-")` 产生空串；统一 `_band_edges` 解析。
- **心率带误当配速格式化**：hr_bands 曾显示成 `2'10"`（实为 130 bpm）；心率带用数值标签。
- **结构性误判**：设备自动 lap 的 `INTERVAL_WARMUP/COOLDOWN` 曾被计为 work 段，20km 轻松跑
  误判“间歇结构”；warmup/cooldown/walk/stand 不计 work、recovery 优先匹配后不再计 work、
  interval 需 ≥2 组，0811 最终正确显示 `structured`。
- **误导性“半程总用时差”**：`structure.half_diff_s`（按段序切半的时长差）对不均匀分段无意义，
  删除展示；保留按累计时长切半的 `pace_profile.half_pace_diff_s`（配速差 sec/km）。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/daily-report.md](../product/daily-report.md)
- Design: [docs/design/summary-extraction.md](../design/summary-extraction.md)

---

## 追加：统一收拢（session-summary 单一 schema，三流程同源）

### 架构修正（用户确认）

初版把深度分析设计成 session-summary-v2 并与 v1 并存，制造了版本分裂。修正为：

1. **单一 schema**：版本号统一回 `session-summary-v1`，深度分析字段直接并入，不产生
   v1/v2 并行结构；存量旧记录缺失字段按 dataclass 默认值兼容读取。
2. **统一流向**：`training_service_factory.load_week` 把 `activity_summary_facts`
   附加到每个活动行（此前只有日报路径附加）——草稿、周报、训练首页从此与日报
   同源消费同一份 session-summary。
3. **消费侧压缩**：新增 `compact_session_summary_for_planning` 投影函数（granularity/
   volume/effect/intensity.basis/pace_profile/structure_profile 概要子集）；草稿
   `_training_summary_context.recent_days` 与周报 `review_week.daily_summaries` 在
   组装事实包时应用（`_compact_day_session_summaries`），日报保持全量。

### 验证（真实 0811 数据）

- `load_week` 返回的活动带 session_summary（8-10/8-11 两条），training_analysis 同样附带；
- 草稿 recent_days、周报 daily_summaries 均为压缩键集合
  （effect/granularity/intensity/pace_profile/structure_profile/volume）；
- 存量 activity_summary_facts（旧结构无新字段）读取降级为空，新同步记录含全字段。


---

## 追加：移除 session-summary 版本号

统一收拢后版本号是死参数：常量从不递增、无法区分新旧结构（同值 v1 下新旧字段并存）、
重算判断实际只靠 `detail_hash`。移除 `SessionSummaryFacts.version` 字段与
`activity_summary_facts.summary_version` 列（存量表 ALTER DROP COLUMN 迁移），
演进原则改为：字段直接并入（默认值兼容）、重算以 `detail_hash` 为准、
粒度由 `granularity` 自描述。


---

## 追加：训练结构识别（阶段 1，加权确定性判定）

用户确认：不用 AI 判断结构，用确定性代码从原始数据做特征提取；步频/步幅参与判定但权重低于配速/心率。

- **同步层**：`segment_sequence`（逐段配速/心率/步频/步幅；Garmin splitSummaries 用 averageSpeed
  换算配速——×2 异常下 speed 与 duration 同源比值抵消仍真实）+ `quantity_gate`
  （段距合计 vs summary 比值 ∈[0.85,1.15] 才可信）。
- **分析层**：`classify_training_structure` 加权判定（配速 0.40/心率 0.35/步频 0.15/步幅 0.10），
  快段=配速快 ≥12% + 心率抬升 ≥8%；交替 ≥2 → 变速/间歇（设备 INTERVAL 标记或快段达阈值 → 间歇）；
  无交替按强度带分节奏（Z3）/有氧（Z2）；主证据不足 → unknown。
- **0811 真实验证**：quantity_gate=ratio 2.0 不可信 → 判定"混合（1 组快慢交替）+ 仅强度模式有效"；
  疲劳信号排除热身段后不再误报；步频一致性只统计跑步段（176.7 spm / CV 1.87%）。
- **已知边界**：4×1000+200 变速还原依赖阶段 2（Coros graphList / Garmin charts / 公里级 splits）。


---

## 追加：Garmin /splits lapDTOs 数据源落地（阶段 2 探测成果）

用户确认：不做滑动窗口分割；只对跑步活动拉取 `/splits`。

- **探测成果**：`/activity-service/activity/{id}/splits` 返回 `lapDTOs`（0811 25 段），
  距离/时长合计与 summary 比值 1.0000（可信），四维度齐全 + `intensityType=INTERVAL`；
  detail 的 `splitSummaries`（×2）与 lapDTOs 是两个不同数据源。
- **实现**：`activity.fetch_activity_splits`（跑步活动注入 `detail["lapDTOs"]`）；
  `_extract_splits` 与 `build_session_summary` 均优先 lapDTOs；`intensityType` 映射 split_type。
- **验证**：lapDTOs 注入后 0811 → quantity_gate ratio=1.0 reliable → 25 段 →
  “间歇（4 组快慢交替）· 置信 100% · fast 16%/slow 16%/body 68%”。
- **暂缓**：滑动窗口分割（用户确认不做）；Coros graphList/Huawei（token 过期/无凭证）。
