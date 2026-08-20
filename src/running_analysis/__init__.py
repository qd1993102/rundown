"""跑步分析引擎 — 确定性跑步数据分析。

在 SessionSummaryFacts 之上提供纯确定性分析模块：
- S1: 数据清洗与异常修复
- S4: 有氧漂移分析
- S5: 跑步经济性评估
- S6: 疲劳代偿模式识别
- S7: 环境补偿与归一化（坡度，天气不可用）
- S8: 心肺-肌肉解耦检测
- S9: HRV 基线对比与状态判定
- S10: 状态-表现一致性校验
- S11: 急慢性负荷比（ACWR）本地计算
- S13: 伤病风险综合评估（多因子加权）

不引入 AI，不修改方案，不改变已有数据结构。
"""

from ._shared import (
    CleanedSegment,
    CleaningReport,
)
from .cleaning import (
    clean_segment_sequence,
)
from .drift import (
    AerobicDriftResult,
    analyze_aerobic_drift,
)
from .economy import (
    EconomyResult,
    assess_running_economy,
)
from .fatigue import (
    FatigueCompensationResult,
    identify_fatigue_compensation,
)
from .environment import (
    EnvironmentCompensationResult,
    compensate_environment,
)
from .decoupling import (
    DecouplingResult,
    analyze_cardiac_muscle_decoupling,
)
from .hrv_baseline import (
    HrvBaselineResult,
    analyze_hrv_baseline,
)
from .consistency import (
    ConsistencyResult,
    check_recovery_performance_consistency,
)
from .acwr import (
    AcwrResult,
    calculate_acwr,
)
from .injury_risk import (
    InjuryRiskResult,
    assess_injury_risk,
)

__all__ = [
    # S1
    "clean_segment_sequence",
    "CleanedSegment",
    "CleaningReport",
    # S4
    "analyze_aerobic_drift",
    "AerobicDriftResult",
    # S5
    "assess_running_economy",
    "EconomyResult",
    # S6
    "identify_fatigue_compensation",
    "FatigueCompensationResult",
    # S7
    "compensate_environment",
    "EnvironmentCompensationResult",
    # S8
    "analyze_cardiac_muscle_decoupling",
    "DecouplingResult",
    # S9
    "analyze_hrv_baseline",
    "HrvBaselineResult",
    # S10
    "check_recovery_performance_consistency",
    "ConsistencyResult",
    # S11
    "calculate_acwr",
    "AcwrResult",
    # S13
    "assess_injury_risk",
    "InjuryRiskResult",
]