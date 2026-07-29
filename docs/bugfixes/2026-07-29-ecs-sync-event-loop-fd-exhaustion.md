# Bug: ECS Web 同步阻塞事件循环并最终耗尽文件描述符

- **发现日期**: 2026-07-29
- **修复日期**: 2026-07-29
- **严重程度**: critical
- **影响范围**: Web 同步、存活检查、Garmin MFA、SQLite/HTTP 资源、ECS systemd 服务

## 现象

ECS 上 `neurun.service` 偶发失去响应。系统日志出现
`OSError: [Errno 24] Too many open files` 和
`asyncio socket.accept out of system resource`；慢同步期间 `/healthz` 也会被拖延。
systemd 停止服务时曾超过 `TimeoutStopSec`，最终以 `SIGKILL` 结束进程。

## 根因

`POST /api/sync` 虽然是异步路由，却直接执行同步的第三方 HTTP 请求和 SQLite 写入，
阻塞了 uvicorn 的 asyncio 事件循环。服务没有同步准入或同用户 singleflight，突发请求可同时
创建更多平台客户端与数据库资源；同步和过期 MFA 状态也缺少统一的显式关闭路径。
systemd 原生服务没有独立的 FD 上限兜底。

## 修复方案

- 新增进程内 `SyncCoordinator`，用工作线程执行阻塞同步；默认执行并发 4、接纳总数 100。
- 同一用户重复同步快速返回 HTTP 409，容量超限返回 HTTP 503。
- 同步完成或失败后显式关闭 Provider HTTP Session、Storage、SQLAlchemy engine；清理过期
  MFA 状态时同步关闭认证 Session，临时 SQLite 查询也在响应前释放 engine。
- systemd 服务模板增加 `LimitNOFILE=8192`，继续使用 `Restart=on-failure` 守护原生 Python
  进程，不引入 Docker。

## 相关文件

- [src/sync_coordinator.py](../../src/sync_coordinator.py) — 同步准入、singleflight 和受控并发。
- [src/web.py](../../src/web.py) — 同步移出事件循环并映射 409/503。
- [src/main.py](../../src/main.py) — 同步在返回前失败时回收已经初始化的局部资源。
- [src/storage.py](../../src/storage.py) — 显式关闭 SQLite engine 和注入的 API client。
- [src/auth.py](../../src/auth.py) — MFA 状态和认证 Session 生命周期。
- [scripts/deploy-ecs.sh](../../scripts/deploy-ecs.sh) — systemd FD 上限。
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 容量与部署设计。

## 验证

- 单元测试覆盖事件循环响应、并发边界、同用户 singleflight、容量拒绝、失败释放和资源关闭。
- 本地 100 个真实 `/api/sync` 路由请求全部返回 200：每用户写入 30 天，共 9000 行、
  100 个隔离数据库及 100 份备份；峰值执行并发 4，FD 从 4 升至 29 后回到 4，
  100 个模拟 Provider Session 全部关闭。
- 100 个用户占满接纳名额时，第 101 个用户在本地约 0.005ms 内被容量逻辑拒绝；
  执行并发仍保持 4。
- 本地验证不等同于 ECS 上游平台验收；发布后仍需观察 systemd、FD、内存和真实同步耗时。
