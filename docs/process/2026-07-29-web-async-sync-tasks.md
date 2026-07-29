# Web 异步同步任务与阶段轮询

- **日期**: 2026-07-29
- **类型**: feature / architecture

## 背景与动机

Garmin、Coros、Huawei 的批量同步会等待第三方网络请求、SQLite 写入和备份完成。虽然已有
`SyncCoordinator` 把阻塞工作移出 asyncio 事件循环，发起同步的 HTTP 请求仍需长时间保持连接，
移动网络、CLB 超时或页面刷新会让用户无法判断后台是否继续执行，也容易诱发重复提交。

目标是把“任务已接纳”和“数据同步成功”分开：POST 快速返回任务 ID，页面通过只读接口查询
真实阶段和终态，同时保留现有单 ECS、4 个执行并发、100 个接纳名额和同用户 singleflight。

## 方案选择

采用用户目录内的原子 `sync-tasks.json` + 进程内后台协程：

- 任务阶段只在认证、指标、活动、备份等真实边界更新，不按运行时间伪造数据百分比；
- 每个用户的任务文件与 Token/SQLite 一样隔离并固定为 `0600`；
- `SyncCoordinator.submit()` 在同一准入临界区内完成容量检查、任务落盘和后台协程注册；
- 进程重启后旧实例的活动任务转为 `interrupted`，由用户决定是否重试，不自动重放平台请求。

没有引入 Redis/Celery：当前部署是单进程、单 ECS，分布式队列会增加运维面且不能直接提高第三方
平台吞吐。没有把任务表写进业务 SQLite：认证失败可能发生在平台用户 ID 和业务数据库初始化前，
独立小型 JSON 更适合保存少量阶段转换，也避免轮询与 garmy 写入争用同一数据库。

CLI 和 MCP 保持同步等待合同；本次只改变 Web API 和页面，避免一次改动同时破坏 AI 工具调用。

## 实现步骤

1. 先更新 `docs/product/data-source-sync.md` 与 Web 技术设计，确认 202、轮询、状态机和重启语义。
2. 新增 `SyncTaskStore`、私有任务文件、终态约束、保留策略和旧 runner 中断恢复。
3. 扩展 `SyncCoordinator.submit()`，持有后台任务强引用并保留 4/100 与 singleflight。
4. 为核心同步增加可选阶段回调；Web 后台任务在资源释放后记录成功或结构化失败。
5. 新增 `GET /api/sync/tasks/{task_id}`，按当前应用账号隔离任务并对运行任务返回
   `Retry-After: 1`。
6. 同步页增加四阶段进度卡、活动任务本地恢复、轮询退避、终态动作和日历刷新。
7. 同步 README、产品/设计状态和 CHANGELOG，完成测试与本地浏览器验收。

## 遇到的问题与解决

- 直接在路由中 `await` 工作线程仍会维持长 HTTP 请求，因此调度器必须接纳后创建有强引用的
  后台协程；协程内部消费异常并在 `finally` 释放用户名额。
- 平台认证失败会把连接状态改为 `expired`，但用户仍需读取失败任务；任务查询只要求应用账号
  会话有效，不要求运动平台连接仍为 `active`。
- 页面刷新可能丢失内存状态，因此浏览器只保存非敏感 `task_id`；其他用户读取同一 ID 返回 404。
- 任务终态不能早于备份和资源回收，否则页面看到成功时仍可能有未关闭的 HTTP/SQLite 资源；
  后台工作函数先完成 `finally`，协调器再写 `succeeded` 或 `failed`。
- 真实 Garmin 验收发现四阶段边界不足以表达数分钟的指标同步，因此复用 garmy 已有逐日期/指标事件，
  在 `progress.items` 中保存阶段内快照；顶层仍固定四段，避免前端生成数百个阶段块。快速事件按换日、
  一秒间隔和最终项节流，兼顾可见推进与原子文件 `fsync` 成本。
- Coros 不经过 garmy，因此在现有 Provider 内部接口增加可选回调：活动只在分页响应成功后推进，
  健康只在逐日聚合完成后推进。主流程统一映射到 `progress.items` 并节流，保留 Mobile 睡眠失败不
  阻塞活动、RHR 和 HRV 的既有降级边界。

## 验证

- 单元测试覆盖任务文件权限、阶段/终态、重启中断、202/Location、运行轮询、409 找回、503
  不创建任务、跨用户 404、认证失败和前端轮询合同。
- 前端脚本通过 JavaScript 语法检查。
- 完整 `pytest`：`187 passed`；`compileall`、同步页 JavaScript 语法检查和 `git diff --check`
  均通过。本地浏览器验收见本次任务交付；ECS 真实 Garmin 验收仍需发布后单独完成。
- 本地现有注册 Coros 账号的三天真实任务已验证 POST 202、认证阶段、活动阶段和结构化失败轮询；
  Coros 返回 `result=1019`（Training Hub Token 已失效），任务正确进入 `failed/reauthorize` 且没有
  伪造逐项进度。成功路径的真实逐项快照仍需用户重新授权后复验，不能以单元测试替代该结论。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [数据源同步产品方案](../product/data-source-sync.md)
- Design: [Web 部署设计](../design/13-sae-deployment.md)
- Bugfix: [同步进度停滞](../bugfixes/2026-07-29-sync-progress-stale-after-refresh.md)
