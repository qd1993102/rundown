# Bug: 旧同步活动缺 activity_summary_facts，日报强度分布拿不到分段配速分析

- **发现日期**: 2026-08-14
- **修复日期**: 2026-08-14
- **严重程度**: major
- **影响范围**: 日报【今天对计划意味着什么】的“强度分布的解释与分析”观察块；所有旧同步（detail 已入库但分段摘要未生成）的活动

## 现象

Garmin 用户 `qd1993102@gmail.com` 07-28 有 18.3km 跑步（detail 含 4 条 splitSummaries），
但日报【今天对计划意味着什么】的强度分布观察只输出
“配速 4'17"/km，为个人阈值配速的 81%……由于未提供心率带分布、配速分位数、步频和步幅数据，
无法进一步分析强度分布细节”——拿不到任何分段级配速分析。

## 根因

`activity_summary_facts`（含 intensity 配速带 / pace_profile 分位数 / structure 步频步幅）
只在同步时由 `store_activity_detail` 从 detail 生成。`_sync_activity_details` 的跳过逻辑：

```python
if existing_detail and (existing_splits or not is_running) and not needs_relap:
    continue
```

旧同步存下的 detail（分段摘要逻辑上线前）已有 splits 且不满足 needs_relap 时**永远被跳过**，
不会重建分段摘要。实测该用户 60 条 activity_details 只有 3 条 activity_summary_facts。

`build_session_summary(detail)` 用现有 detail 即可生成完整分段分析（配速带/心率带/分位数/CV/步频），
缺失纯粹是同步重建逻辑的覆盖缺口。

## 修复方案

`_sync_activity_details` 增加 `needs_summary` 分支：

- `has_summary = _has_summary_facts(storage, activity_id)`
- `needs_relap` 仅在 **有 summary 但缺 segment_sequence**（旧 schema 需升级 + lapDTOs）时触发；
- `needs_summary = bool(existing_detail) and not has_summary`：有 detail 但无分段摘要时，
  用已有 detail 直接重建（不强制重拉 detail、不依赖 fetch_splits），两分支互斥。

## 相关文件

- [src/main.py](../../src/main.py) — `_sync_activity_details` 增加 needs_summary 重建分支、
  `_has_summary_facts` 辅助
- [tests/test_main.py](../../tests/test_main.py) — 新增重建缺摘要活动的测试
- [docs/design/summary-extraction.md](../../docs/design/summary-extraction.md) — 分段摘要由同步
  按 detail 生成、缺则重建的说明

## 验证

- 用真实 detail 重建 07-28 主课 summary_facts 后重新生成日报，强度分布观察输出：
  “跑步以心率 145–160 区间为主（占 99.89%），配速以 4'00"–5'00" 为主（占 99.89%），
  平均步频 173 spm、步幅 1.35m，节奏稳定；前后半程配速差仅 0.1 秒，整体均匀。”
- 新增单测：有 detail 缺 summary_facts → 不重拉 detail、重建摘要（含配速带/structure）；
- `pytest` 全量零失败（518 passed, 1 xfailed）。
