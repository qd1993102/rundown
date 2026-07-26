# 三平台认证与本地持久化审计

- **日期**: 2026-07-26
- **类型**: refactor

## 背景与动机

Garmin Web 同步在已注册、已绑定后仍报告未认证。修复不能只针对 Garmin，因为公共
Provider 初始化顺序同时影响 Coros 和 Huawei；用户又发现 root CLI 与 systemd 服务用户
混用后出现本地文件权限问题，因此需要对认证前置条件和所有本地写入做一次完整审计。

## 方案选择

认证层选择在公共 `_setup()` 建立唯一顺序契约，避免每个同步分支自行认证和重复读取
身份。Huawei 的 CrewPals token 选择按 Web 用户持久化，并坚持认证成功后才写入。

本地写入选择引入小型公共工具，不改变数据库或记忆格式。应用管理的数据目录统一私有；
用户显式选择的导出目录不被强制改权，只把包含健康数据的输出文件设为 `0600`。图片和
SQLite 由外部库生成，生成后立即收紧权限；文本使用同目录原子替换。

## 实现步骤

1. 先更新项目目标、多平台架构、数据流、模块和 ECS 部署设计。
2. 增加失败测试，复现三平台认证前读取 `user_id`、Huawei 凭据不持久化以及目录/文件
   权限依赖 umask 的问题。
3. 调整公共 Provider 初始化顺序，并统一 Web 认证失效响应。
4. 增加 Huawei 用户级凭据存储，仅在绑定认证成功后持久化。
5. 引入私有本地写入工具，迁移账号、邀请、Token、SQLite、备份、记忆、配置、报告与导出。
6. 复查残余写入点；对应用状态与显式导出分别验证权限策略。
7. 更新 README、Bug 记录和 CHANGELOG，运行针对性及完整测试。

## 遇到的问题与解决

- Coros 没有复现 Garmin 的相同异常文本，但未认证时会返回不可靠的零值身份；公共契约
  仍必须覆盖它。
- Huawei Web 路由原先忽略 `authenticate()` 的布尔返回值；改为 `False` 即失败，避免把
  无效 CrewPals token 保存为可用连接。
- 项目根目录可能是源码仓库，不能为了保护 `.env` 把整个仓库改为 `0700`；原子写入工具
  因此支持只收紧文件、不修改调用方明确选择的父目录。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/04-modules.md](../design/04-modules.md)、[docs/design/05-data-flow.md](../design/05-data-flow.md)、[docs/design/12-multi-platform.md](../design/12-multi-platform.md)、[docs/design/13-sae-deployment.md](../design/13-sae-deployment.md)
- Bugfix: [Provider 认证顺序](../bugfixes/2026-07-26-provider-auth-before-user-id.md)、[私有本地持久化](../bugfixes/2026-07-26-private-local-persistence.md)
