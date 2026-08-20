# Pace Zones — 个人配速区间计算

## 概述

基于个人最佳成绩（PB）、心率数据、近期活动和环境条件，计算 Z1-Z5 配速与心率区间。

## 计算流程

1. **PB 清洗**：时效衰减（>90天权重50%，>180天20%）、VDOT 一致性校验（半马 VDOT 比 5K 低 >=2.0 则权重归零）、5K/10K 一致性合并
2. **心率区间**：Karvonen 储备心率法，心率区间不可被配速或天气覆盖
3. **配速基线**：从有效 PB 通过 VDOT 映射推算 Z1-Z5
4. **近期校准**：筛选 Z2 心率区间的活动，检测配速偏移或心率偏高
5. **环境补偿**：高温高湿/疲劳累积动态修正配速（不修改心率）
6. **降级处理**：PB 过期/缺失时的 fallback 逻辑

## 使用方式

调用 `src/pace_zones.py` 中的 `compute_all_zones()` 函数。

```python
from src.pace_zones import compute_all_zones

result = compute_all_zones(
    personal_bests={"5k": {"time": "24:18"}, ...},
    hr_rest=51,
    hr_max=190,
    age=34,
    activities=[...],  # 近期活动列表
    temp_c=28,         # 可选
    humidity=75,       # 可选
)
```

输出包含 `zones`（Z1-Z5 配速与心率）、`effective_pb`、`warnings`、`meta` 等字段。
