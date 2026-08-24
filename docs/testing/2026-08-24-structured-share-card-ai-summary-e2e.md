# Structured Share Card AI Summary E2E 验收报告

- **验收日期**: 2026-08-24
- **验收范围**: 结构化日报 `ai_insight.share_card`、两次跑步摘要、禁止词过滤、空字段兼容、旧格式回退
- **执行原则**: 只读业务代码；仅新增本报告
- **报告结论**: 本次结构化摘要验收项通过；全量 pytest 仍受已知 sandbox 文件权限问题影响

## 1. 命令与结果

### 1.1 定向回归

命令：

```bash
pytest -q tests/test_web.py tests/test_coach_provider.py tests/test_coach_runtime.py
```

最终结果：

```text
95 passed in 7.49s
```

说明：主 Agent 先前提供的同一命令结果为 `92 passed`；当前工作区在验收期间补充了空结构化摘要回退、session 序号校验和隐私 Schema 覆盖，因此最终重跑计数为 95。

### 1.2 真实日报 ViewModel

命令：使用 `src.memory.parse_front_matter` 只读解析以下真实文件，再由 Node 加载现有 `web/static/share-card.js` 并调用 `buildDailyShareCardData(report, "sport")`。

```text
data/rd_4eb74f34850295e14105e3ed9a712df2/memory/auto/daily/2026-08-23.md
```

结果：

- 原始 `daily_activities.sessions` 为 2 条。
- ViewModel `title` 为 `2 次跑步`。
- 第 1 次跑步保留 3 条逐次观察：强度分布、跑步动力学、跑步分析。
- 第 2 次跑步保留 3 条逐次观察：强度分布、跑步动力学、跑步分析。
- 序列化 ViewModel 不含：`睡眠`、`恢复`、`HRV`、`ACWR`、`风险`、`警告`、`异常`、`建议`。
- 真实日报缺少 `ai_insight.share_card`，实际走旧格式兼容回退，符合历史日报兼容规则。

观察：该 Markdown front matter 使用 `date` 字段，而当前 Adapter 直接读取 `report_date`，因此本次直接以文件 front matter 构造时 ViewModel 的 `date` 为空；不影响本次两 session 与隐私字段验收，但上层 API 适配时应继续提供 `report_date`。

### 1.3 结构化 share_card 四类场景

命令：Node fixture 调用现有 `buildDailyShareCardData`，未改写 `web/static/share-card.js`。

结果：

| 场景 | 结果 | 关键证据 |
|---|---|---|
| 结构化两 session | PASS | labels: `训练摘要`, `第1次跑步`, `第2次跑步`, `训练提炼`, `教练结论`；旧 `observations` 未泄漏 |
| 禁止词 | PASS | 含禁止词的 headline/session/takeaway/conclusion 被逐条丢弃，仅保留安全的 `第2次跑步` |
| 空字段 | PASS | 空 `share_card` 回退到旧 `observations`，保留 `第1次跑步 · 跑步分析` 与安全结论 |
| 旧格式回退 | PASS | 缺少 `share_card` 时保留 `第1次跑步 · 强度分布`、`第2次跑步 · 强度分布` 与结论 |

### 1.4 全量 pytest

主 Agent 提供的已知基线命令：

```bash
pytest -q
```

主 Agent 结果：

```text
662 passed, 1 skipped, 1 xfailed, 1 failed
tests/test_training_service_factory.py
PermissionError: [Errno 1] Operation not permitted: /Users/qindong/.neurun/data.db
```

本次验收环境此前重跑结果为：

```text
676 passed, 2 failed, 1 skipped, 1 xfailed
```

已确认的 sandbox 失败仍为：

```text
tests/test_training_service_factory.py::test_setup_baseline_longest_distance_uses_28d_window
Path(config.db_path).touch()
PermissionError: [Errno 1] Operation not permitted: /Users/qindong/.neurun/data.db
```

长教练文本测试在最终代码状态下已纳入定向回归并通过；其合同为“Schema 限长、Canvas 完整换行、不追加布局省略号”。

## 2. 失败层级与风险

- **业务代码层**: 本次验收未发现结构化摘要、逐次观察保留、session 序号校验或禁止词过滤失败。
- **测试环境层**: 全量 pytest 的 `training_service_factory` 失败发生在测试触碰 `/Users/qindong/.neurun/data.db` 时，属于 sandbox 权限问题，不属于分享卡功能回归。
- **工作区一致性风险**: 验收期间检测到并行测试/前端改动，导致定向和全量计数与主 Agent 基线不同；最终定向命令已在当前文件状态下重新通过。

## 3. 未执行项

- 未启动真实登录浏览器流程；本次验收目标限定为本地真实日报 front matter 到 ViewModel，以及 Node 驱动的现有 Adapter 合同。
- 未修改业务源文件、E2E 测试文件或生产配置。

## 4. 建议动作

1. 保留已知 sandbox 权限失败，交由开发/CI 提供可写的测试数据目录或修正测试配置；不要为通过验收修改分享卡逻辑。
2. 主 Agent 合并并行改动后重新执行 `pytest -q`，确认全量计数与失败集合稳定。
3. 上层日报 API 已将 front matter 的 `date` 映射为 Adapter 所需的 `report_date`；直接读取 Markdown 做离线夹具时仍需手动补齐该字段。
