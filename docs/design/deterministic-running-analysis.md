# 设计方案 — 确定性跑步分析引擎（S1/S4/S5/S6）

> 属于 [设计方案索引](index.md) · 专题设计 · 版本 v1 · 2026-08-20
> 状态: 设计阶段，待确认
> 关联模块: `src/summary_extraction.py`、`src/training_analysis.py`、`src/training_day_summary.py`
> 关联文档: [summary-extraction.md](summary-extraction.md)、[training-system.md](training-system.md)、[daily-report.md](../product/daily-report.md)

---

## 1. 背景与目标

当前系统在数据清洗、有氧漂移、经济性评估和疲劳代偿四个维度完全缺位或只有雏形，
依赖 AI Coach Skill 的 prompt 做"推理"而非"计算"。这导致：

- 脏数据（GPS 漂移、HR 跳变）直接进入下游，AI 可能基于错误数据给出错误结论
- 有氧漂移只有二值检测（`_fatigue_signal`），不计算漂移率、不分级、不关联因素
- 跑步经济性完全缺失，无法量化"同等配速下的心脏成本"变化趋势
- 疲劳代偿只检测 HR 漂移+步幅下降组合，不区分 A/B/C 三种代偿模式、不定位衰减起点

本设计在现有 `SessionSummaryFacts` 和 `segment_sequence` 基础上，新增四个纯确定性
分析模块，不引入 AI、不修改方案、不改变已有数据结构。

### 设计原则

1. **确定性优先**：所有分析基于数学规则，输出可回放、可审计
2. **消费现有数据**：输入来自 `SessionSummaryFacts` 和 `segment_sequence`，不新增数据源
3. **退化友好**：数据缺失时降级而非报错，输出明确标记 `unavailable` 和原因
4. **不改变已有模块**：作为独立分析层叠加在摘要提取之上，不修改 `build_session_summary` 签名
5. **AI 只读**：分析结果作为结构化 fact 喂给 AI Coach Skill，AI 只负责解释和生成建议文本

---

## 2. S1 — 数据清洗与异常修复

### 2.1 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `segment_sequence` | `SessionSummaryFacts` | 逐段配速/心率/步频/步幅/距离/时长 |
| `pace_profile` | `SessionSummaryFacts` | 整体配速画像（分位数、CV） |
| `data_quality` | `SessionSummaryFacts` | 已有质量标记 |

### 2.2 清洗规则

#### 2.2.1 GPS 漂移检测

```
对每个 segment：
  if segment.pace_sec_per_km < 120:  # 配速快于 2:00/km（物理极限）
    → 标记为 gps_drift，用相邻段线性插值替换
  if segment.distance_m > 0 and segment.duration_s > 0:
    computed_pace = segment.duration_s / (segment.distance_m / 1000)
    if |computed_pace - segment.pace_sec_per_km| / computed_pace > 0.3:
      → 标记为 gps_inconsistent，优先使用 computed_pace
```

**阈值依据**：2:00/km 是世界纪录级配速，业余跑者不可能达到；距离/时长自洽性偏差 >30%
说明 Provider 返回的 pace 字段与 distance/duration 矛盾。

#### 2.2.2 心率信号丢失/跳变检测

```
对相邻 segment 对 (i, i+1)：
  if segment[i].avg_hr and segment[i+1].avg_hr:
    delta = |segment[i+1].avg_hr - segment[i].avg_hr|
    if delta > 40:  # 单段跳变 >40bpm
      → 标记为 hr_spike
      → 用前后两段均值插值替换（边界段用相邻段值）
  if segment.avg_hr < 60 or segment.avg_hr > 220:
    → 标记为 hr_outlier，替换为 None
```

**阈值依据**：单段（通常 1km）内心率跳变 >40bpm 在稳定运动中不可能，
说明光电传感器信号丢失或腕部松动。

#### 2.2.3 步频/步幅异常值剔除

```
对每个 segment：
  if segment.avg_cadence and (segment.avg_cadence < 120 or segment.avg_cadence > 220):
    → 标记为 cadence_outlier，替换为 None
  if segment.stride_length_cm and (segment.stride_length_cm < 50 or segment.stride_length_cm > 200):
    → 标记为 stride_outlier，替换为 None
```

**阈值依据**：步频 120-220 spm 覆盖步行到冲刺；步幅 50-200cm 覆盖走路到竞速。

### 2.3 输出

```python
@dataclass(frozen=True)
class CleanedSegment:
    """清洗后的单段数据，保留原始值引用。"""
    index: int
    pace_sec_per_km: float | None
    avg_hr: float | None
    avg_cadence: float | None
    stride_length_cm: float | None
    duration_s: float | None
    distance_m: float | None
    split_type: str | None
    # 质量标记
    quality_flags: tuple[str, ...]  # gps_drift, hr_spike, cadence_outlier, ...

@dataclass(frozen=True)
class CleaningReport:
    """清洗报告：整体质量评估。"""
    total_segments: int
    cleaned_segments: int
    flags_summary: dict[str, int]  # flag_type → count
    overall_quality: str  # good / degraded / poor
    segments: tuple[CleanedSegment, ...]
```

### 2.4 函数签名

```python
def clean_segment_sequence(
    segment_sequence: Iterable[dict[str, Any]],
    *,
    data_quality: dict[str, Any] | None = None,
) -> CleaningReport:
    """清洗分段序列，返回 CleaningReport。

    不修改输入，不清洗 provider 原始数据。
    缺失数据直接继承为 None，不编造。
    """
```

---

## 3. S4 — 有氧漂移分析

### 3.1 定义

有氧漂移（Cardiac Drift）指长时间稳态运动中，同等配速下心率随时间上升的幅度。
反映心脏为维持同等输出需要额外做功的程度，是耐力基础的重要指标。

### 3.2 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `CleanedSegment[]` | S1 输出 | 清洗后的分段序列 |
| `SessionSummaryFacts` | 已有 | 整体配速/心率画像 |

### 3.3 分析逻辑

#### 3.3.1 稳态段筛选

```
从清洗后的 segment 中筛选"配速稳定"的连续段：
  1. 排除 split_type 为 WARMUP/COOLDOWN/WALK/REST 的段
  2. 只保留有配速且有 HR 的段
  3. 计算所有候选段的配速均值 μ 和标准差 σ
  4. 保留配速在 [μ - 2σ, μ + 2σ] 内的段
  5. 取这些段中最大的连续块作为稳态段
  6. 如果稳态段 < 3 个或总时长 < 20min → 返回 insufficient_data
```

#### 3.3.2 漂移率计算

```
将稳态段按时间等分为前后两半：
  first_half = steady_segments[:len//2]
  second_half = steady_segments[len//2:]

  first_hr = 时长加权平均心率(first_half)
  second_hr = 时长加权平均心率(second_half)
  first_pace = 时长加权平均配速(first_half)
  second_pace = 时长加权平均配速(second_half)

  # 配速归一化：如果后半程配速有变化，修正心率漂移
  # 假设配速每快 1s/km，心率升高约 1bpm（经验值）
  pace_correction = (second_pace - first_pace) * 1.0
  corrected_drift = (second_hr - first_hr) - pace_correction

  drift_rate_pct = corrected_drift / first_hr * 100
```

#### 3.3.3 分级

| 漂移率 | 等级 | 含义 |
|--------|------|------|
| < 3% | excellent | 有氧基础优秀 |
| 3-5% | normal | 正常范围 |
| 5-8% | elevated | 偏高，注意补水/降温/有氧基础 |
| > 8% | high | 异常，可能存在脱水、过热或过度疲劳 |

### 3.4 输出

```python
@dataclass(frozen=True)
class AerobicDriftResult:
    """有氧漂移分析结果。"""
    status: str  # ok | insufficient_data | no_steady_segment
    drift_rate_pct: float | None
    grade: str | None  # excellent | normal | elevated | high
    first_half_hr: float | None
    second_half_hr: float | None
    first_half_pace: float | None
    second_half_pace: float | None
    steady_segment_count: int
    steady_duration_minutes: float
    pace_correction_applied: bool
    note: str | None
```

### 3.5 函数签名

```python
def analyze_aerobic_drift(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: SessionSummaryFacts | None = None,
) -> AerobicDriftResult:
    """分析有氧漂移。

    只在存在连续稳态段（≥3 段，≥20min）时计算漂移率。
    配速有明显变化时做配速归一化修正。
    """
```

---

## 4. S5 — 跑步经济性评估

### 4.1 定义

跑步经济性 = 同等配速下的"心脏成本"。用配速(m/s) / 心率(bpm) × 1000 作为经济性指数。
指数越高说明同等配速下心率越低、越省力。

### 4.2 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `CleanedSegment[]` | S1 输出 | 清洗后的分段序列 |
| `SessionSummaryFacts` | 已有 | 整体配速/心率/步频/步幅 |
| `AthleteBaseline` | `training_analysis.py` | 个人阈值心率/配速 |

### 4.3 分析逻辑

#### 4.3.1 经济性指数计算

```
对稳态段（同 S4 筛选逻辑）：
  pace_ms = 1000 / pace_sec_per_km  # 配速转换为 m/s
  economy_index = pace_ms / avg_hr * 1000

整体经济性（全段）：
  overall_pace_ms = 1000 / session.avg_pace_sec_per_km
  overall_economy = overall_pace_ms / session.avg_hr * 1000
```

#### 4.3.2 历史基线对比

```
从近期活动（最近 7/30 天）中筛选同类型（有氧跑）的活动：
  1. 过滤：同类型、配速在 ±15% 范围内
  2. 计算每个活动的经济性指数
  3. 基线 = 这些指数的中位数
  4. 变化 = (当前 - 基线) / 基线 × 100%
     - 正值 → 经济性改善（同等配速下心率更低）
     - 负值 → 经济性下降（同等配速下心率更高）
```

**注意**：首次运行无历史数据时返回 `baseline_unavailable`，不编造基线。

#### 4.3.3 步态模式标签

```
if avg_cadence >= 180 and avg_stride < 110:
  gait_label = "高步频省力型"
elif avg_cadence < 170 and avg_stride >= 115:
  gait_label = "大步幅低步频型"
elif avg_cadence >= 175 and avg_stride >= 110:
  gait_label = "均衡高效型"
else:
  gait_label = "低效型"
```

**阈值依据**：180spm 为公认高效步频基准；110cm 为中等身高跑者常见步幅。
步频/步幅缺一不可时返回 `unknown`。

### 4.4 输出

```python
@dataclass(frozen=True)
class EconomyResult:
    """跑步经济性评估结果。"""
    status: str  # ok | no_hr | insufficient_data
    economy_index: float | None
    economy_index_unit: str  # "(m/s)/bpm × 1000"
    avg_pace_sec_per_km: float | None
    avg_hr: float | None
    avg_cadence: float | None
    avg_stride_cm: float | None
    gait_label: str | None  # 高步频省力型 | 大步幅低步频型 | 均衡高效型 | 低效型 | unknown
    # 历史对比
    baseline_available: bool
    baseline_economy: float | None
    baseline_sample_count: int
    trend_pct: float | None  # 正=改善，负=下降
    trend_days: int
    note: str | None
```

### 4.5 函数签名

```python
def assess_running_economy(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: SessionSummaryFacts | None = None,
    baseline: AthleteBaseline | None = None,
    historical_economies: Iterable[EconomyResult] = (),  # 近期同类活动经济性
) -> EconomyResult:
    """评估跑步经济性。

    需要心率数据；无心率时返回 no_hr。
    历史基线从最近 7/30 天同类活动中取中位数。
    """
```

---

## 5. S6 — 疲劳代偿模式识别

### 5.1 定义

疲劳代偿是身体在疲劳时"硬撑"的方式。不同代偿模式指向不同的训练短板：

- **模式 A（肌肉耐力瓶颈）**：配速掉 + 步幅显著缩短 + 步频勉强维持 + 心率稳定或略降
- **模式 B（心肺输出瓶颈）**：配速掉 + 心率顶在高位 + 步频步幅同步下降
- **模式 C（神经控制衰减）**：配速勉强维持 + 步频波动加大 + 步幅稳定性下降

### 5.2 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `CleanedSegment[]` | S1 输出 | 清洗后的分段序列 |
| `SessionSummaryFacts` | 已有 | 整体画像 |
| `AthleteBaseline` | `training_analysis.py` | 个人阈值 |

### 5.3 分析逻辑

#### 5.3.1 前后段对比

```
取跑步主体段（排除 WARMUP/COOLDOWN/WALK/REST）：
  running_segments = [s for s in cleaned if s.split_type not in NON_RUNNING]

如果 running_segments < 4 → insufficient_data

按时间三等分：
  first_third = running_segments[:len//3]
  last_third = running_segments[2*len//3:]

计算各指标的前后变化：
  pace_change_pct = (last_pace - first_pace) / first_pace * 100
  hr_change_pct = (last_hr - first_hr) / first_hr * 100
  cadence_change_pct = (last_cadence - first_cadence) / first_cadence * 100
  stride_change_pct = (last_stride - first_stride) / first_stride * 100
  cadence_cv_change = last_cv - first_cv  # 步频变异系数变化
  stride_cv_change = last_cv - first_cv  # 步幅变异系数变化
```

#### 5.3.2 模式判定

```
if pace_change_pct >= 3%:  # 明显掉速
  if stride_change_pct <= -3% and cadence_change_pct >= -2% and hr_change_pct <= 2%:
    → 模式 A（肌肉耐力瓶颈）
  elif hr_change_pct >= 2% and cadence_change_pct <= -2% and stride_change_pct <= -2%:
    → 模式 B（心肺输出瓶颈）
  else:
    → 混合模式（不做强制判定）
elif pace_change_pct < 3%:  # 配速基本维持
  if cadence_cv_change >= 0.5 or stride_cv_change >= 0.5:
    → 模式 C（神经控制衰减）
  else:
    → no_significant_fatigue
else:
  → no_significant_fatigue
```

#### 5.3.3 衰减起点定位

```
从 running_segments 中找第一个满足以下条件的段作为衰减起点：
  1. 该段配速比前两段均值慢 ≥5%
  2. 且后续段配速持续低于前 1/3 均值
返回该段的序号（从 1 开始）和对应距离
```

### 5.4 输出

```python
@dataclass(frozen=True)
class FatigueCompensationResult:
    """疲劳代偿分析结果。"""
    status: str  # ok | insufficient_data
    # 前后变化
    pace_change_pct: float | None
    hr_change_pct: float | None
    cadence_change_pct: float | None
    stride_change_pct: float | None
    cadence_cv_change: float | None
    stride_cv_change: float | None
    # 模式判定
    pattern: str | None  # A | B | C | mixed | no_significant_fatigue
    pattern_label: str | None  # 肌肉耐力瓶颈 | 心肺输出瓶颈 | 神经控制衰减 | ...
    # 衰减起点
    decay_onset_segment: int | None
    decay_onset_distance_km: float | None
    decay_onset_time_minutes: float | None
    # 建议方向
    training_suggestion: str | None
    note: str | None
```

### 5.5 函数签名

```python
def identify_fatigue_compensation(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: SessionSummaryFacts | None = None,
    baseline: AthleteBaseline | None = None,
) -> FatigueCompensationResult:
    """识别疲劳代偿模式。

    需要 ≥4 个跑步主体段；不足时返回 insufficient_data。
    模式判定是确定性规则，不是 AI 推断。
    """
```

---

## 6. 模块组织

### 6.1 文件位置

```
src/running_analysis/
  __init__.py        # 公开 API
  cleaning.py        # S1: clean_segment_sequence, CleaningReport
  drift.py           # S4: analyze_aerobic_drift, AerobicDriftResult
  economy.py         # S5: assess_running_economy, EconomyResult
  fatigue.py         # S6: identify_fatigue_compensation, FatigueCompensationResult
  _shared.py         # 内部共享工具（加权平均、分段筛选等）
```

### 6.2 与现有模块的关系

```
Provider 原始数据
  → build_session_summary (summary_extraction.py, 已有)
    → SessionSummaryFacts (已有)
      → clean_segment_sequence (cleaning.py, 新增)
        → CleaningReport
      → analyze_aerobic_drift (drift.py, 新增)
        → AerobicDriftResult
      → assess_running_economy (economy.py, 新增)
        → EconomyResult
      → identify_fatigue_compensation (fatigue.py, 新增)
        → FatigueCompensationResult
  → 以上结果作为结构化 fact 输入 AI Coach Skill
  → 或直接用于日报/周报的确定性展示
```

### 6.3 不修改的文件

- `src/summary_extraction.py` — 不修改 `build_session_summary`
- `src/training_analysis.py` — 不修改 `TrainingSessionAnalyzer`
- `src/training_day_summary.py` — 不修改 `TrainingDaySummaryBuilder`
- `src/coach_runtime/` — 不修改 Skill 注册表

新增模块作为独立分析层，叠加在已有摘要之上。

---

## 7. 数据流与接入点

### 7.1 日报接入

在 `src/training_service_factory.py` 或 `src/memory.py` 的日报组装流程中，
在 `build_session_summary` 之后、AI Skill 调用之前插入分析调用：

```python
from src.running_analysis import (
    clean_segment_sequence,
    analyze_aerobic_drift,
    assess_running_economy,
    identify_fatigue_compensation,
)

session_summary = build_session_summary(detail)

# 新增分析层
cleaning = clean_segment_sequence(session_summary.segment_sequence)
drift = analyze_aerobic_drift(cleaning.segments, summary=session_summary)
economy = assess_running_economy(cleaning.segments, summary=session_summary)
fatigue = identify_fatigue_compensation(cleaning.segments, summary=session_summary)

# 将分析结果作为额外 fact 注入 daily_facts
daily_facts["running_analysis"] = {
    "cleaning": asdict(cleaning),
    "aerobic_drift": asdict(drift),
    "economy": asdict(economy),
    "fatigue_compensation": asdict(fatigue),
}
```

### 7.2 MCP 接入

不新增独立 MCP tool，分析结果随日报 report 一起返回。
后续可考虑在 `daily/latest` 资源中增加 `running_analysis` 字段。

---

## 8. 阈值与常量

所有阈值集中在 `_shared.py` 中，不散落各处：

```python
# ── S1 清洗阈值 ──
PACE_MIN_SEC_PER_KM = 120       # 2:00/km
PACE_CONSISTENCY_RATIO = 0.3    # 自洽性偏差 30%
HR_SPIKE_DELTA_BPM = 40         # 相邻段跳变
HR_MIN = 60
HR_MAX = 220
CADENCE_MIN = 120
CADENCE_MAX = 220
STRIDE_MIN_CM = 50
STRIDE_MAX_CM = 200

# ── S4 漂移阈值 ──
STEADY_MIN_SEGMENTS = 3
STEADY_MIN_DURATION_MINUTES = 20
DRIFT_EXCELLENT = 3.0
DRIFT_NORMAL = 5.0
DRIFT_ELEVATED = 8.0
PACE_HR_CORRECTION = 1.0        # 配速每快 1s/km，心率修正 1bpm

# ── S5 经济性阈值 ──
ECONOMY_SCALE = 1000
HIGH_CADENCE = 180
LOW_CADENCE = 170
LONG_STRIDE_CM = 115
EFFICIENT_STRIDE_CM = 110

# ── S6 代偿阈值 ──
DECAY_PACE_PCT = 3.0            # 掉速 ≥3% 视为明显
STRIDE_DROP_PCT = -3.0
CADENCE_DROP_PCT = -2.0
HR_RISE_PCT = 2.0
CV_CHANGE = 0.5
DECAY_ONSET_PACE_PCT = 5.0      # 单段比前两段均值慢 ≥5%

# ── 非跑步段标记 ──
NON_RUNNING_SPLIT_MARKERS = (
    "walk", "stand", "warmup", "cooldown", "cool",
    "rest", "recovery", "jog", "transition", "stop",
)
```

---

## 9. 测试策略

| 模块 | 测试重点 |
|------|---------|
| S1 cleaning | GPS 漂移边界、HR 跳变相邻/边界、全脏数据退化、正常数据不变 |
| S4 drift | 稳态段不足退化、配速修正逻辑、分级边界值、全有氧/全间歇 |
| S5 economy | 无心率退化、首次运行无基线、步态标签边界、经济性=0 保护 |
| S6 fatigue | 三段过少退化、三种模式典型场景、衰减起点不触发、混合模式 |

所有测试使用 `pytest`，不依赖外部服务。测试数据使用手工构造的 segment 序列。

---

## 10. 验收标准

1. **S1**：给定含 GPS 漂移（pace=90s/km）、HR 跳变（160→80→160）的 segment 序列，
   清洗后漂移段被插值、跳变段被修正、正常段不变
2. **S4**：给定 10km 有氧跑（配速 5:30±5s，心率 140→150），
   漂移率 ≈ (150-140)/140 = 7.1%，分级 elevated
3. **S5**：给定配速 5:00/km、心率 150bpm 的活动，
   经济性指数 = (1000/300)/150 × 1000 = 22.2
4. **S6**：给定后程步幅 -4%、配速 +4%、心率 +1% 的序列，判定为模式 A
5. 所有模块在数据不足时返回 `insufficient_data` 而非抛异常
6. 不修改任何已有模块的公开接口

---

## 11. 后续扩展

本设计覆盖 S1/S4/S5/S6。以下模块留待后续设计：

- **S7 环境补偿**：需要气象数据源（温度/湿度/风力），当前无可用数据源
- **S8 心肺-肌肉解耦**：需要配速-心率滚动相关系数，可在 S4/S6 稳定后叠加
- **S9 HRV 基线**：需要 7/30 天 HRV 历史数据，fetcher 已有数据但未做基线计算
- **S11 ACWR 本地计算**：当前依赖 Garmin 平台值，本地计算需要训练负荷定义
- **S13 伤病风险**：依赖 S6/S9/S11 的输出，应在三者稳定后再做多因子加权


## 12. S8 — 心肺-肌肉解耦检测（2026-08-20 已实现）

### 12.1 定义

通过滚动窗口配速-心率皮尔逊相关系数 + 步幅衰减曲线，判断"掉速"的根源。

- 后半程配速降但心率不降 → 心肺已到极限（cardiac）
- 配速降且心率同步降 → 肌肉/能量系统先衰竭（muscular）
- 配速稳定但滚动相关性逐渐减弱 → 渐进解耦（mixed）

### 12.2 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `CleanedSegment[]` | S1 输出 | 清洗后的分段序列 |

### 12.3 输出

```python
@dataclass(frozen=True)
class DecouplingResult:
    status: str  # ok | insufficient_data | no_hr
    decoupling_type: str | None  # cardiac | muscular | none | mixed
    decoupling_label: str | None
    decoupling_onset_segment: int | None
    decoupling_onset_distance_km: float | None
    decoupling_onset_time_minutes: float | None
    overall_pace_hr_correlation: float | None
    second_half_pace_change_pct: float | None
    second_half_hr_change_pct: float | None
    second_half_stride_change_pct: float | None
    attribution: str | None
    note: str | None
```

### 12.4 函数签名

```python
def analyze_cardiac_muscle_decoupling(
    cleaned_segments: Iterable[CleanedSegment],
    *,
    summary: Any = None,
    baseline: Any = None,
) -> DecouplingResult:
```

---

## 13. S9 — HRV 基线对比与状态判定（2026-08-20 已实现）

### 13.1 定义

将昨晚 HRV、睡眠时长和静息心率与 7 天滚动基线对比，计算恢复指数（0-100）。

- HRV 分量（50%）：偏离基线 ±1σ 内满分，超过 ±2σ 为 0 分
- 睡眠分量（30%）：基于 7.5h 基准
- RHR 分量（20%）：升高 = 差，降低 = 好

### 13.2 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `today: dict` | `DailyHealth` | hrv_last_night_avg, resting_heart_rate, sleep_duration_hours |
| `historical: list[dict]` | `DailyHealth[]` | 最近 7/30 天健康数据 |

### 13.3 分级

| 恢复指数 | 状态 | 训练风险 |
|----------|------|---------|
| ≥70 | 充分恢复 | 低风险 |
| 50-70 | 轻微欠恢复 | 注意 |
| 30-50 | 明显欠恢复 | 警告 |
| <30 | 严重疲劳 | 高风险 |

### 13.4 函数签名

```python
def analyze_hrv_baseline(
    today: dict[str, Any],
    *,
    historical: Iterable[dict[str, Any]] = (),
    baseline_days: int = 7,
) -> HrvBaselineResult:
```

---

## 14. S11 — 急慢性负荷比（ACWR）本地计算（2026-08-20 已实现）

### 14.1 定义

从每日训练负荷计算 7 天急性负荷 / 28 天慢性负荷。不依赖 Garmin 平台值。

### 14.2 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `daily_loads: list[dict]` | 活动表 | 每日 training_load 值 |

### 14.3 分级

| ACWR | 等级 | 建议 |
|------|------|------|
| <0.8 | 减量区 | 适当增加训练量 |
| 0.8-1.3 | 安全区 | 维持当前量 |
| 1.3-1.5 | 警戒区 | 降低 10-20% |
| >1.5 | 高风险区 | 降低 20-30% |

### 14.4 函数签名

```python
def calculate_acwr(
    daily_loads: Iterable[dict[str, Any]],
    *,
    acute_days: int = 7,
    chronic_days: int = 28,
) -> AcwrResult:
```

---

## 15. 文件清单（2026-08-20 更新）

```
src/running_analysis/
  __init__.py        # 公开 API（7 个分析函数）
  _shared.py         # 常量、CleanedSegment、CleaningReport、工具函数
  cleaning.py        # S1: data cleaning
  drift.py           # S4: aerobic drift
  economy.py         # S5: running economy
  fatigue.py         # S6: fatigue compensation
  decoupling.py      # S8: cardiac-muscle decoupling
  hrv_baseline.py    # S9: HRV baseline
  acwr.py            # S11: ACWR calculation
```


## 16. S10 — 状态-表现一致性校验（2026-08-20 已实现）

### 16.1 定义

对比昨晚恢复状态（S9 HRV 恢复指数）与今日实际运动表现（S4 漂移等级 + S5 经济性趋势），
识别异常模式：

- 恢复好 → 表现好：正常一致
- 恢复差 → 表现差：正常一致
- 恢复差 → 表现好：肾上腺素代偿（透支未来）
- 恢复好 → 表现差：隐性疲劳（潜在伤病/营养/心理）

### 16.2 输入

| 字段 | 来源 | 说明 |
|------|------|------|
| `hrv` | S9 输出 | HRV 基线分析结果（恢复指数） |
| `drift` | S4 输出 | 有氧漂移等级 |
| `economy` | S5 输出 | 经济性趋势 |
| `performance_grade` | 外部 | 可选的外部表现评级 |

### 16.3 函数签名

```python
def check_recovery_performance_consistency(
    hrv: HrvBaselineResult | None = None,
    drift: AerobicDriftResult | None = None,
    economy: EconomyResult | None = None,
    *,
    performance_grade: str | None = None,
) -> ConsistencyResult:
```


## 17. S7 — 环境补偿与归一化（2026-08-20 已实现）

### 17.1 定义

基于 Naismith 规则消除坡度对配速和心率的干扰：
- 每 100m 爬升/km 增加约 45s/km
- 每 100m 下降/km 减少约 15s/km（上限 30s/km）
- 心率修正约为配速影响的 60%

天气补偿（温度/湿度/风力）当前不可用，预留接口。

### 17.2 地形分类

| gain_per_km | 分类 |
|-------------|------|
| <10 m/km | 平路 |
| 10-30 m/km | 起伏 |
| 30-60 m/km | 丘陵 |
| 60-100 m/km | 山地 |
| >100 m/km | 陡峭山地 |

### 17.3 函数签名

```python
def compensate_environment(
    avg_pace_sec_per_km: float | None,
    avg_hr: float | None,
    *,
    total_ascent_m: float | None = None,
    total_descent_m: float | None = None,
    distance_m: float | None = None,
    gain_per_km: float | None = None,
    grade_profile: dict[str, float] | None = None,
) -> EnvironmentCompensationResult:
```

---

## 18. S13 — 伤病风险综合评估（2026-08-20 已实现）

### 18.1 定义

多因子加权打分系统，综合 5 个维度：

| 因子 | 权重 | 满分 |
|------|------|------|
| ACWR 负荷 | 30% | 30 |
| 连续恢复不足 | 25% | 25 |
| 步态代偿持续 | 20% | 20 |
| 步幅下降 | 15% | 15 |
| 步频稳定性 | 10% | 10 |

### 18.2 分级

| 总分 | 等级 | 行动 |
|------|------|------|
| <25 | 低风险 | 正常训练 |
| 25-50 | 注意 | 降低强度/时长 |
| 50-75 | 警告 | 减少训练量，优先休息 |
| >75 | 必须休息 | 立即停止训练 |

### 18.3 函数签名

```python
def assess_injury_risk(
    acwr: AcwrResult | None = None,
    hrv: HrvBaselineResult | None = None,
    hrv_history: list[dict[str, Any]] | None = None,
    fatigue: FatigueCompensationResult | None = None,
    fatigue_history: list[FatigueCompensationResult] | None = None,
    economy: EconomyResult | None = None,
    *,
    recent_fatigue_patterns: list[str] | None = None,
) -> InjuryRiskResult:
```

---

## 19. 最终文件清单（2026-08-20）

```
src/running_analysis/
  __init__.py        # 公开 API（10 个分析函数）
  _shared.py         # 常量、CleanedSegment、CleaningReport、工具函数
  cleaning.py        # S1: data cleaning
  drift.py           # S4: aerobic drift
  economy.py         # S5: running economy
  fatigue.py         # S6: fatigue compensation
  environment.py     # S7: environment compensation (elevation/grade)
  decoupling.py      # S8: cardiac-muscle decoupling
  hrv_baseline.py    # S9: HRV baseline
  consistency.py     # S10: recovery-performance consistency
  acwr.py            # S11: ACWR calculation
  injury_risk.py     # S13: injury risk assessment
```

### 覆盖对照

| Skill | 名称 | 实现状态 |
|-------|------|---------|
| S1 | 数据清洗与异常修复 | ✅ `cleaning.py` |
| S2 | 稳态与非稳态分段 | ⚠️ 部分被 S4 稳态筛选覆盖 |
| S3 | 特征工程与指标计算 | ✅ 已在 `build_session_summary` 中 |
| S4 | 有氧漂移分析 | ✅ `drift.py` |
| S5 | 跑步经济性评估 | ✅ `economy.py` |
| S6 | 疲劳代偿模式识别 | ✅ `fatigue.py` |
| S7 | 环境补偿与归一化 | ✅ `environment.py`（坡度，天气不可用） |
| S8 | 心肺-肌肉解耦检测 | ✅ `decoupling.py` |
| S9 | HRV 基线对比与状态判定 | ✅ `hrv_baseline.py` |
| S10 | 状态-表现一致性校验 | ✅ `consistency.py` |
| S11 | 急慢性负荷比预警 | ✅ `acwr.py` |
| S12 | 训练课表动态调整 | ⚠️ 已在 `src/training.py` 和 AI Coach Skill |
| S13 | 伤病风险综合评估 | ✅ `injury_risk.py` |
| S14 | 教练式自然语言生成 | ⚠️ 由 AI Coach Skill 负责 |
