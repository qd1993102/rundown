# Bug: CLB 健康检查复用登录路由且部署监听地址不明确

- **发现日期**: 2026-07-26
- **修复日期**: 2026-07-26
- **严重程度**: major
- **影响范围**: ECS + CLB Web 部署、负载均衡健康检查、外网访问可用性

## 现象

neurun 的 systemd 进程仍显示运行，但 CLB 健康检查由正常转为异常。使用 ECS 私网地址
访问 `http://<ECS_PRIVATE_IP>:8080/login` 时出现 `Connection refused`，负载均衡无法稳定
判断 Web 进程状态。

## 根因

部署时复用了业务登录页 `/login` 作为健康检查目标。该路由只面向页面访问，会读取
Cookie、查询用户状态并渲染模板，不应成为基础设施存活契约。

同时，CLB 通过 ECS 私网网卡连接后端。如果 systemd 使用 `MCP_HOST=127.0.0.1`，服务
即使在本机回环地址正常运行，私网地址的 TCP 连接仍会被拒绝。`Connection refused`
发生在 HTTP 路由之前，不是登录鉴权代码返回的拒绝响应。

## 修复方案

- 新增公开的 `GET|HEAD /healthz`，固定返回 HTTP 200。
- `GET` 返回 `{"status":"ok"}`；`HEAD` 返回空响应体；两者都设置
  `Cache-Control: no-store`。
- 探活处理不读取 Cookie、用户注册表、SQLite，也不访问任何运动平台或 AI 服务。
- 部署文档明确 CLB 场景使用 `MCP_HOST=0.0.0.0`、后端端口 `8080`，并要求同时验证
  回环地址与 ECS 私网地址。

## 相关文件

- [src/web.py](../../src/web.py) — 注册独立的 `/healthz` 基础设施路由
- [tests/test_web.py](../../tests/test_web.py) — 验证 GET/HEAD、固定 200、无用户查询及禁止缓存
- [README.md](../../README.md) — 增加 ECS + CLB 探活配置和验证命令
- [docs/design/01-project-goals.md](../design/01-project-goals.md) — 增加 Web 可运维性目标
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 定义探活契约与私网监听要求

## 验证

- 单元测试直接调用 GET 和 HEAD 处理器，确认均返回 HTTP 200。
- 确认 GET 返回固定 JSON、HEAD 无响应体，且不会调用 `UserManager.get()`。
- ECS 部署后使用 `curl` 分别验证 `127.0.0.1:8080/healthz` 和
  `<ECS_PRIVATE_IP>:8080/healthz`，然后将 CLB 健康检查路径切换为 `/healthz`。
- 运行完整 `pytest`，确认零失败。
