# Bug: 同步重拉失败覆盖已修复的 lapDTOs（结构判定被降级）

- **发现日期**: 2026-08-12
- **修复日期**: 2026-08-12
- **严重程度**: major
- **影响范围**: `_sync_activity_details`（`needs_relap` 重拉路径）、日报训练结构判定与教练观察

## 现象

用户通过 Web 同步后，08-11 活动原本已修复的间歇结构（4 组快慢交替 · 置信 100%）被降级回“混合结构（1 组快慢交替）”：`activity_summary_facts` 回到旧 schema（无 `segment_sequence`）、`activity_splits` 回到 ×2 异常的 7 段、`activity_details` 丢失 `lapDTOs`。每次重新同步/生成日报都会复现，导致页面显示的分组快慢信息不完整。

## 根因

`_sync_activity_details` 的 `needs_relap` 分支（旧 schema 跑步活动补齐 lapDTOs）逻辑不健壮：

```python
detail = fetch_detail(activity_id)
if is_running and callable(fetch_splits):
    try:
        laps = fetch_splits(activity_id)
    except Exception:
        laps = []
    if laps:
        detail["lapDTOs"] = laps
    # laps 为空时：detail 无 lapDTOs，仍继续 store_activity_detail
if store_activity_detail(...):  # 用无 lapDTOs 的 detail 覆盖原数据
```

当 `/splits` 拉取失败或返回空时，`detail` 不含 `lapDTOs`，`store_activity_detail` 用 `splitSummaries`（×2 异常）重建 `activity_splits` 与 `activity_summary_facts`，覆盖掉已修复的 lapDTOs 数据。

## 修复方案

`_sync_activity_details` 增加三级保护：

1. `fetch_activity_splits` 成功 → 注入 `lapDTOs` 后存储（正常路径）；
2. 失败但**原有 detail 已有 lapDTOs** → 保留原 lapDTOs 再存储（已修复数据不降级，且 summary 基于保留的 lapDTOs 重建回新 schema）；
3. 失败且**原有也没有 lapDTOs**（`needs_relap` 场景）→ 跳过本次存储（`failed += 1`），保持现状等下次同步再试，绝不用旧数据覆盖。

## 相关文件

- [src/main.py](../../src/main.py) — `_sync_activity_details` 拉取失败保护
- [tests/test_main.py](../../tests/test_main.py) — `test_sync_activity_details_preserves_lapdto_when_relap_fails`（两个场景回归）
- [prompts/skills/review-daily-training/SKILL.md](../../prompts/skills/review-daily-training/SKILL.md) — 观察逐组展示每组配速（配合本修复保证数据可用）
- [docs/design/summary-extraction.md](../../docs/design/summary-extraction.md) — §10.5 存量兜底说明

## 验证

- 新增回归测试：重拉失败时已有 lapDTOs 保留且 summary 重建出新 schema（stored=1）；无 lapDTOs 时不覆盖（stored=0, failed=1）。
- 通过 Web 完整链路验证（POST /api/reports → GET /api/dashboard）：08-11 运动概要按照课型结构逐组展示“间歇（4 组快慢交替：第1组 3'44"(hr147)→5'04"(hr139)；第2组…第4组…）”，配速与心率齐全。
