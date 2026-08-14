# 配速校准增强：天气归一化 + PB 交叉验证 技术设计

- **日期**: 2026-08-12
- **类型**: feature（校准算法增强）
- **状态**: 待实现

## 背景与动机

用户（VO2max 66、全马 PB 2:32:49、目标 230）的配速校准区间（Z2 4:58–5:44、Z4 4:08–4:36）
明显慢于 PB 推算能力（Z2 ≈4:25–4:40、Z4 ≈3:23–3:30）。趋势分析证明差异主要来自
**天气热（8 月上海高温）+ 当下状态变动**（07-27 快周 3:57 / 08-03 慢周 5:08），而非能力下降。
当前校准把高温慢周与状态好周混合进 28 天百分位，导致区间偏保守。

## 方案（确定性层，不进 AI）

全部在 `PaceCalibrationProfileBuilder.build()` 内完成；AI 只消费校准后的 `pace_reference`，
不计算配速。

### 1. 天气归一化（`_weather_adjusted_pace`）

- 活动级季节估算：按活动月份估算温度并折算配速回标准温度（15°C）：
  - 夏季（7–8 月）：系数 0.94（高温下配速折算后更快）
  - 温和（3/6/11 月）：1.00
  - 春/秋（4/5/9/10 月）：0.99
  - 冬季（12–2 月）：1.03
- 用户补充信息（`supplement`）提到"高温/炎热/酷暑"→ 该时段样本用更强修正 0.92；
  提到"凉快/低温"→ 1.05。
- 折算后样本进入百分位计算；`basis_refs` 与说明标注"经天气归一化"。

### 2. PB 交叉验证（`_cap_and_fallback_by_pb`）

- 数据源：`fitness-assessment.md` 的 `personal_bests`（5K/10K/半马/全马）+ `goal.target_time`。
- 推算阈值配速 `T`：`min(半马配速, 10K 配速+10s, 5K 配速+20s)`（用户 = 207s/km，半马 3:27）。
- 用途：
  a. **上限封顶**：Z4 `max_sec_per_km` 不得超过 `T`（防近期样本虚快）；
  b. **样本不足兜底**：Z 区样本不足（unavailable）时，用 PB 推算做 `available` 兜底
     （T→Z4、T+60~75s→Z2、T-10~15s→Z3），避免"无配速事实"；
  c. **能力差距标记**：近期样本中位数比 PB 推算慢超过 12% 时，`pace_profile` 标记
     `state_gap="current_below_pb"`，供 framework/审计参考（不强制改配速）。
- 兜底区间必须标记 `source="pb_derived"`，与训练样本来源区分。

### 3. 状态分层（后续迭代，本次不做）

能力上限（峰值周）与当前下限（近 14 天）分离，需要更多样本与测试，列入后续。

## 数据来源

| 数据 | 来源 | 现状 |
|---|---|---|
| PB | `memory/profile/fitness-assessment.md`（`personal_bests`） | 已有（5K 16:08/10K 33:17/半马 1:12:48/全马 2:32:49） |
| 目标成绩 | `goal.target_time` | 已有（2:30:00） |
| 天气 | 季节估算（月份）+ 用户补充信息关键词 | 无 API，先不接 |

## 涉及文件

- `src/training_pace.py` — `_weather_adjusted_pace`、`_cap_and_fallback_by_pb`、build 增强
- `src/training.py` — `pace_calibration_profile` 传入 PB/目标/补充信息
- `tests/test_training_pace.py` — 天气归一化、PB 兜底/封顶/差距标记测试
- `docs/design/training-system.md` — 校准规则同步

## 验收标准

1. 夏季（7–8 月）样本折算后 Z 区间快于未折算（验证天气归一化生效）；
2. 样本不足时 PB 兜底生成 `available` 区间且 `source=pb_derived`；
3. 近期样本快于 PB 推算 T 时 Z4 上限封顶到 T；
4. 近期样本显著慢于 PB 时标记 `state_gap`，不改配速；
5. 现有校准测试全部通过（无回归）。
