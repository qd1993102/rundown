# Web Chat 多用户部署改造

- **日期**: 2026-07-09
- **类型**: feature

## 背景与动机

将 Rundown 从纯 CLI + MCP Server (stdio) 扩展为可部署到 VPS/SAE 的 Web 服务，
面向非 AI 从业者的普通用户。用户打开浏览器即可绑定 Garmin、与 AI 教练对话。
支持多用户，数据按 API Key 完全隔离。

## 方案选择

讨论了多种终端方案：
1. 微信小程序 — 需要审核、开发量大，初期放弃
2. 纯 MCP（用户自带客户端）— 门槛太高，普通用户不会用
3. **Web Chat** ✅ — 零安装、零审核、链接即用

存储方案：VPS 本地磁盘（40GB）+ OSS 灾备。不用 NAS，不用中间件。

## 实现步骤

1. `config.py` — 新增 non_interactive、data_dir 字段；token_dir 纳入 RUNDOWN_HOME 解析；新增 UserConfig 类
2. `auth.py` — MFA 两步拆分（start_login / complete_mfa），支持 Web 模式
3. `storage.py` — SQLite backup_to / restore_from
4. `providers/garmin.py` — non_interactive 参数传递
5. `users.py` — 新建用户管理器（API Key、注册表、路径映射）
6. `coach.py` — 新增 chat_stream() 流式对话
7. `web.py` — Web 路由 + HTML 页面（内联，零前端依赖）
8. `main.py` — cmd_serve 入口 + cmd_mcp SSE 支持
9. Dockerfile + docker-compose.yml

## 遇到的问题与解决

- **pyproject.toml 缩进**: toml 文件使用 tab 或 space 混用导致 Edit 失败，最终用最小化匹配解决
- **Chat 页面在模板 vs 内联**: 初期不再拆分独立 HTML 文件，用内联 fallback 保持单文件部署简单
- **MFA 状态管理**: 用进程内 dict + 超时清除，容器重启丢失是预期行为（用户重试即可）

## 关联文档

- CHANGELOG: [../CHANGELOG.md](../CHANGELOG.md)
- Design: [13-sae-deployment.md](../design/13-sae-deployment.md)
- Design Index: [index.md](../design/index.md)
