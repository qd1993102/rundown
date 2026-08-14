# Coros/Garmin 下游消费一致性独立验收

- **日期**: 2026-08-14
- **范围**: Coros 原始详情与统一摘要、日报/周报/草稿消费投影、Garmin `lapDTOs` 回归、非 Garmin 重同步字段更新。
- **方式**: 独立只读代码路径、pytest 与真实 SQLite（`mode=ro`）回放；未修改业务代码、测试断言或已有工作树改动。
- **结论**: **通过**。受控本地重建后的真实 Coros 记录、持久化摘要/分段、日报及周报/草稿共享投影均满足本次消费一致性契约；未发现原始 Hz 泄漏。

## 执行证据

| 检查 | 命令/方式 | 结果 |
| --- | --- | --- |
| 修复后聚焦回归 | `pytest -q tests/test_summary_extraction.py tests/test_storage.py tests/test_main.py tests/test_training_day_summary.py tests/test_training.py tests/test_providers.py -k 'coros or lap_dto or lapdto or recent_two_days or day_summary or compact_planning or draft_training_summary_compacts_session_summary_for_planning or weekly_report'` | 38 passed, 136 deselected（本次独立执行） |
| 新增永久字段回归 | `pytest -q -vv tests/test_main.py::test_non_garmin_resync_refreshes_average_hr_and_training_load` | 1 passed（本次独立执行） |
| 更广 focused 集 | 主 Agent 提供 | 156 passed, 1 xfailed。 |
| 全量 pytest | 主 Agent 提供 | 523 passed, 1 xfailed（修复及热量单位补齐后的最终工作树）。 |
| 工作树空白检查 | `git diff --check` | 通过，无输出 |
| Coros 重同步 SQLite 回放 | 隔离临时 SQLite，执行两次真实 `_sync_provider` | 第二次为 `0 条新增, 1 条字段更新`；查询值 `avg_heart_rate=158`、`training_load=113.0` |

## 验收矩阵

| 验收项 | 结果 | 证据/说明 |
| --- | --- | --- |
| Coros 原始详情不被归一化覆盖 | 通过 | `test_coros_detail_store_keeps_raw_json_and_persists_normalized_splits` 验证 `detail_json.summary.distance` 保留原值；独立样本回放仍为 `814126` / `258245`。 |
| 归一化距离、活动时长、平均配速 | 通过（运行时） | 真实 `activity_id=479546480416292974` 的原始详情经修复后代码回放得到 `8141.26m` / `2582.45s` / `317.2s/km`；平均配速现以活动总时长/总距离为权威口径。 |
| 热量统一单位 | 通过 | Coros 详情原始 `495255` 归一为 `495.255 kcal`，活动汇总按既有整数合同保存为 `495 kcal`。 |
| L2、quantity ratio `1.0`、HR/步频/步幅 | 通过 | 样本输出 `granularity=L2`、`quantity_gate.ratio=1.0`、强度依据 HR、平均步频约 `160.99`、步幅约 `117.31cm`。 |
| Coros 权威 9 段 | 通过（摘要消费） | 原始 `lapList` 三个层级组的子项数为 `[9, 2, 1]`；摘要选择权威 9 段，`segment_sequence_count=9`。 |
| `frequencyList` 只聚合至 L2，原始 Hz 不进摘要/草稿 | 通过 | 样本回放中 summary 与草稿 compact 投影均不含 `frequencyList`；Provider 保留原始字段测试通过。 |
| Garmin `lapDTOs` 回归不丢失 | 通过 | 聚焦 `test_main` 覆盖重拉失败后保留既有 `lapDTOs` 并重建 summary；相关测试在 16 passed 内。 |
| 今日摘要、周报、草稿最近两日六项事实或显式 gap | 通过 | 从重建后的真实 persisted summary 构造日报：距离/时长、`317.2s/km`、HR `155`、步频约 `160.99`、步幅约 `117.31cm` 均可用；缺口仅为 sleep/recovery/training_load 上下文。周报/草稿共享 compact 投影保持 L2、9 段和同一配速，两个序列化投影均不含 `frequencyList`。 |
| 重同步更新 `avg_heart_rate`/`training_load` | 通过 | 隔离 SQLite 回放确认从 `155/107` 更新为 `158/113`；新增永久 `tests/test_main.py::test_non_garmin_resync_refreshes_average_hr_and_training_load` 独立通过。 |

## 持久化与消费者回放

- **真实记录**: `data/rd_a97c17c9056158bf0ec9f153e9ff7a14/data.db` 中 `activity_id=479546480416292974`；重建前备份位于 `data/backups/rd_a97c17c9056158bf0ec9f153e9ff7a14-pre-coros-normalization-20260814.db`。本验收只以 SQLite `mode=ro` 打开当前库。
- **持久化结果**: `activity_summary_facts` 为 L2、距离 `8141.26m`、时长 `2582.45s`、平均配速 `317.2s/km`、ratio `1.0`、9 段；`activity_splits` 为 9 行。原始详情仍保存 `814126`/`258245`、2584 个 `frequencyList` 点和 `[9,2,1]` 原始层级。
- **消费者结果**: 日报 session metrics 含配速、HR、步频和步幅；周报/草稿共用压缩投影保留 L2/9段/配速。持久化 summary、日报和压缩投影序列化文本均无 `frequencyList`。

## 环境边界

使用 `rg --files --hidden --no-ignore` 后定位到受 `.gitignore` 排除的真实 SQLite，并以 `file:<absolute path>?mode=ro` 打开 `data/rd_a97c17c9056158bf0ec9f153e9ff7a14/data.db`。未执行网络认证或任何外部写操作。

## 建议动作

1. 保留受控重建备份，后续遇到历史 Coros 坏摘要时复用同样的“先备份、再重建、后只读验证”流程。
