# Bug: 批量同步后报告列表只显示结束日期

- **发现日期**: 2026-07-19
- **修复日期**: 2026-07-19
- **严重程度**: major
- **影响范围**: Web 批量同步、日报生成、报告列表

## 现象

请求 `mode=batch` 同步一个日期范围后，SQLite 已包含范围内的活动和健康数据，但
`/api/reports` 及报告列表只显示结束日期的一条日报，用户无法从列表查看批量范围内的
其他日期。

## 根因

批量请求被正确标准化为 `from_date ~ to_date` 并完成范围数据拉取，但复用的
`_do_daily_sync()` 只为 `target=to_date` 生成一份日报。报告列表的数据源是
`memory/auto/daily/*.md`，不是 SQLite 原始数据日期，因此列表只能看到一条。

用户提供的 curl 还缺少 `neurun_key` Cookie，原样调用会返回 HTTP 401；这与已经认证
的浏览器批量请求是两个独立问题，服务端仍须保留用户隔离认证。

## 修复方案

- 批量范围完成一次数据同步后，调用 `_generate_batch_reports()` 为范围内每一天生成
  日报，并跳过主流程已经生成的结束日期。
- 结束日期继续使用 `_do_daily_sync()` 的在线 AI 教练洞察；历史日期直接使用
  `MemoryStore.generate_daily_report()` 的本地规则洞察，避免大量外部 AI 请求。
- API 响应新增 `reports_generated`，明确返回本次范围对应的逐日报告数量。

## 相关文件

- [src/web.py](../../src/web.py) — 批量范围逐日报告生成和响应计数
- [tests/test_web.py](../../tests/test_web.py) — 验证逐日生成并跳过已有结束日
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 同步批量报告数据流
- [README.md](../../README.md) — 更新 Web 批量同步行为

## 验证

- 数据库检查确认 `2026-06-19 ~ 2026-07-19` 已有 20 条活动和 30 天健康记录，原问题
  是日报文件仅有 `2026-07-19.md`。
- 单元测试确认三天范围生成三份报告，且不重复生成主流程已有的结束日报。
- 使用带 `neurun_key` Cookie 的真实 HTTP 批量请求验证 `reports_generated` 与
  `/api/reports` 返回数量。
- 运行完整 `pytest`，确认零失败。
