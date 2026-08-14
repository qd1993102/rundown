# Bug: lapDTOs 重拉后 session-summary 重建失败（summary_version 列兼容）

- **发现日期**: 2026-08-12
- **修复日期**: 2026-08-12
- **严重程度**: major
- **影响范围**: 存量活动详情重拉（`_sync_activity_details` needs_relap 路径）与 `store_activity_detail` 的 `activity_summary_facts` 重建

## 现象

对 08-11 跑步活动拉取 Garmin `/splits` lapDTOs（25 段、距离比值 1.0）并重存详情后，`activity_summary_facts` 没有更新：日志出现 `活动摘要提炼失败 activity_id=... error_type=IntegrityError`，`session-summary` 仍是旧 schema（无 `segment_sequence`/`quantity_gate`），结构判定继续返回 `unknown` 或基于 ×2 异常 splitSummaries 的降级结果（只识别 1 组交替，而真实数据是 4 组快慢交替的间歇课）。

## 根因

两个叠加问题：

1. **存量表结构未迁移**：统一收拢前建的表 `activity_summary_facts` 含 `summary_version VARCHAR NOT NULL` 列；`ensure_tables` 里的 `ALTER TABLE ... DROP COLUMN summary_version` 迁移在 try/except 中被**静默吞掉**，真实库的该列一直存在。`store_activity_detail` 的新 INSERT 语句不含该列 → NOT NULL 约束 IntegrityError，且内层 except 只记 warning，调用方误以为存储成功。
2. **兼容判断取错列索引**：修复时用 `PRAGMA table_info(activity_summary_facts)` 判断列是否存在，但 PRAGMA 返回 `(cid, name, type, notnull, dflt_value, pk)`，列名在 `row[1]` 而非 `row[0]`，导致 `has_version_col` 恒为 False，仍走不含 `summary_version` 的 INSERT，IntegrityError 复现。

## 修复方案

- `ensure_tables`：迁移失败时记录 `logger.info`（不再完全静默），保留后续列兼容兜底。
- `store_activity_detail`：按 `PRAGMA table_info` 的 **name 列（`row[1]`）** 判断 `summary_version` 是否存在；存在时 INSERT 带空串占位，不存在时用新结构，保证旧表也能重建摘要。
- 修复后：08-11 重拉 lapDTOs 成功重建 summary，结构判定识别为"间歇结构（4 组快慢交替）· 置信 100% · 成分 fast 16%/slow 16%/body 68%"，`quantity_gate ratio=1.0 reliable`。

## 相关文件

- [src/activity.py](../../src/activity.py) — `ensure_tables` 迁移日志、`store_activity_detail` 列兼容 INSERT、PRAGMA 列索引修正
- [src/memory.py](../../src/memory.py) — `_segment_sequence_fallback`（存量活动无 segment_sequence 时从 activity_splits 重建，本 bug 修复前的过渡兜底）
- [tests/test_storage.py](../../tests/test_storage.py) — `test_store_activity_detail_handles_legacy_summary_version_column` 回归测试
- [docs/design/summary-extraction.md](../../docs/design/summary-extraction.md) — §10.5 存量兜底说明

## 验证

- 新增测试 `test_store_activity_detail_handles_legacy_summary_version_column`：手工建带 `summary_version NOT NULL` 的旧表，`store_activity_detail` 仍能成功重建 `activity_summary_facts`。
- 真实数据：08-11 活动注入 lapDTOs 后 `store_activity_detail` 成功，`segment_sequence` 25 段、`quantity_gate` ratio 1.0；`classify_training_structure` 输出 interval（4 组交替）。
