# 强度锚定改为档案推导（不再用近期训练猜测阈值）

- **日期**: 2026-08-14
- **类型**: refactor（训练内容识别 / 个人阈值锚定）

## 背景与动机

用户反馈：某节课平均心率 155bpm 被判定为“个人阈值心率的 98%”，但实际体感强度没那么高。
数据证实根因：`AthleteBaselineBuilder` 用“近期跑步平均心率的 85 分位”（无档案时）当阈值心率，
有两个系统性偏差：

1. **用工作平均心率当基准**：平均心率天然低于阈值段瞬时心率，基准系统性偏低；
2. **基准随训练构成漂移**：大量轻松跑会把分位进一步拉低——同一个 149 的心率，在轻松周被判
   “100% 阈值”、在有质量课周被判“95% 阈值”，而真乳酸阈值不会每周变。

即把“近期训练强度的分布”（结果）当成了“个人能力锚点”（约束），方向反了。

## 方案选择

- **放弃**：继续用近期平均心率/配速分位（训练构成敏感，虚高）；
- **选择**：个人档案锚定——`AthleteBaseline` 阈值只来自档案：
  ① 用户已确认的阈值（`threshold_heart_rate` / `threshold_pace_sec_per_km`）→
  ② 档案最大心率 + 静息心率（Karvonen：阈值 ≈ 静息 + 0.88 × (最大 − 静息)）→
  ③ 仅最大心率 × 0.88 → ④ 档案 PB 推导阈值配速（半马最接近阈值，10K/5K 按经验差折算，
  来源标记 `personal_bests`）→ ⑤ 都没有：阈值为空，**不猜测**，输出“缺少个人阈值心率/配速，强度评估不可用”。
- 档案新增选填“静息心率”“最大心率”字段；PB 字段（5k/10k/半马/全马）已有，直接复用。

## 实现步骤

1. `AthleteBaselineBuilder.build()`：移除 `_percentile(heart_rates, 0.85)` / `_percentile(paces, 0.25)`
   猜测；改为档案推导（显式阈值 > Karvonen > 最大心率×0.88 > PB 推导阈值配速 > 空）；
   `source=profile|personal_bests|unavailable`。
2. `TrainingSessionAnalyzer.classify()`：无个人阈值但有平均心率/配速时，强度证据输出
   “缺少个人阈值心率/配速，强度评估不可用”，不再落入默认“保守返回未知”的误导文案。
3. 修复 `MemoryWriter._load_profile(self)` 传参错误（误传 writer 实例，导致档案从未被加载），
   统一改为 `self._memory_store`；`_analyze_training_sessions`、草稿/周报两处
   `AthleteBaselineBuilder().build()` 传入档案。
4. `/api/profile` POST 与 `profile.html`：新增 `resting_heart_rate` / `max_heart_rate` 字段。

## 遇到的问题与解决

- 直接调用分析路径正常、Web 生成却不用档案：定位到 `MemoryWriter._load_profile(self)` 把 writer
  传入静态方法（writer 无 `.get()`），被 try/except 吞掉返回 `None`——这是既有隐藏 bug，
  本次一并修复为 `self._memory_store`。
- 无档案时证据文案被 `classify()` 最终兜底覆盖：在兜底前优先返回 `intensity_evidence`。

## 验证

- 有档案（静息 55 / 最大 190）→ 阈值 174：07-29 课（avg 161）由旧的“104% 阈值”变为
  “93%”（有氧），与用户体感一致；
- 仅填 PB（半马 1:40:00）→ 阈值配速 ≈ 4:44/km：07-29 课（5:33）→ “平均配速强度为个人阈值配速的
  85%”（有氧），配速侧有锚点；
- 无档案 → 证据为“缺少个人阈值心率/配速，强度评估不可用”，不再给精确百分比；
- `pytest` 全量零失败（510 passed, 1 xfailed），新增 6 个档案推导/PB 兜底单测。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/summary-extraction.md](../design/summary-extraction.md)、
  [docs/design/training-system.md](../design/training-system.md)
- Product: [docs/product/daily-report.md](../product/daily-report.md)、
  [docs/product/training-experience.md](../product/training-experience.md)
