# Web 同步容量与资源生命周期治理

- **日期**: 2026-07-29
- **类型**: architecture

## 背景与动机

单 ECS 的原生 systemd 服务发生间歇性不可用。运行证据显示不是 Docker、磁盘或常驻内存
不足，而是 Python 进程出现 `Too many open files`；同步路由还会用阻塞调用占住 Web 事件循环。
目标是在保留同步 HTTP 响应合同的前提下，安全接纳 100 个用户的同步突发。

## 方案选择

本次选择进程内有界协调器：默认 4 个任务执行、100 个不同用户执行或等待，并对同一用户
singleflight。它适合当前单进程、单 ECS 和同步响应合同，改动范围可控。

没有在 Bug 修复中引入 Redis/Celery 或持久化任务队列，因为这会改变用户旅程、部署依赖、
任务恢复和接口合同。若未来要求请求脱离 HTTP 生命周期、跨重启恢复或多 ECS 协调，应另立设计。

## 实现步骤

1. 用复现测试证明原同步工作阻塞 asyncio 事件循环，且当前路由最大实际执行并发为 1。
2. 建立 `SyncCoordinator`，把阻塞工作送入线程池，并实现 4/100、singleflight 和快速拒绝。
3. 为 Storage、HTTP client 和 MFA 中间状态补齐确定性的资源关闭路径。
4. 将只读临时 SQLite 使用点改为用后关闭或不再无意义地创建 Storage。
5. 在 systemd 单元增加 `LimitNOFILE=8192` 作为防御性边界。
6. 运行 100 用户与阶梯容量压测，再运行完整测试套件。

## 遇到的问题与解决

修复前的本地 100 用户合成写入本身没有表现出 SQLite 串库或永久 FD 线性增长，但当前
`/api/sync` 的阻塞工作最大并发只有 1，2 秒慢调用会让 health 调度延迟约 1.96 秒。这说明
SQLite 隔离写入不是首要瓶颈，事件循环阻塞与第三方客户端生命周期才是必须先收紧的边界。

本地 Python 为 3.14，ECS 运行环境为 Python 3.12；因此本地吞吐数字只用于相对验证，不能
替代发布后在真实 Garmin/Coros/Huawei 延迟下的 ECS 验收。

修复后的端到端路由压测向同一个 Web 进程发出 100 个不同用户的 `/api/sync` 请求，
每用户写入 30 天同步状态、活动和健康记录。100 个响应全部为 200，共核验 9000 行、
100 个隔离数据库、100 份备份和 100 个已关闭的模拟 Provider Session；耗时 0.470 秒，
执行并发峰值 4，FD 为 `4 → 29 → 4`。这反映本地 SQLite 与调度开销，不代表真实平台吞吐。

同一合成写入的阶梯结果为：并发 1/2/4/8/16 时分别约 190/263/233/160/152 用户每秒，
FD 峰值分别为 18/21/25/46/80。8 以上没有吞吐收益且明显增加 FD，因此 2C2G 单 ECS
默认执行上限保持 4，不建议继续上调；100 仅作为接纳/排队硬边界。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/data-source-sync.md](../product/data-source-sync.md)
- Design: [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md)
- Bugfix: [docs/bugfixes/2026-07-29-ecs-sync-event-loop-fd-exhaustion.md](../bugfixes/2026-07-29-ecs-sync-event-loop-fd-exhaustion.md)
