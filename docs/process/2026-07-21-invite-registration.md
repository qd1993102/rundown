# Web 邀请码注册与应用账号登录

- **日期**: 2026-07-21
- **类型**: feature

## 背景与动机

旧版 Web 在绑定 Garmin、Coros 或 Huawei 时隐式生成 API Key，没有独立应用账号，
任何访问者都能创建用户，而且退出后无法通过邮箱密码恢复原用户目录。本次引入邀请码门禁，
注册时收集昵称、邮箱和密码，再把应用登录与运动平台绑定拆成两个阶段。

## 方案选择

邀请码继续使用 JSON 而不是新增数据库或管理后台，符合当前单机、低成本部署边界。邀请码由
服务器本地 CLI 随机生成，严格单次使用且不自动过期；完整码保存在管理员文件中，成功注册后
写入 `used_by` / `used_at`，并通过临时文件加 `os.replace` 原子写回。

应用密码使用 Python 标准库 `hashlib.scrypt`，不新增外部依赖；随机 API Key 继续作为 Cookie
会话标识和用户数据目录键。该方案保留现有数据隔离模型，同时让用户能用邮箱密码恢复会话。

## 实现步骤

1. 新增 `InvitationStore`，实现 JSON schema 校验、可用性检查和限次核销。
2. 扩展 `UserRecord` 与 `UserManager`，加入昵称、应用邮箱、密码哈希、唯一邮箱查询和登录校验。
3. 新增 `/login`、`/register` 页面以及邀请验证、注册、登录 API。
4. 将 `/api/setup` 改为必须先有有效应用会话，再执行运动平台认证。
5. 更新个人页账号展示、部署配置、README、设计文档和单元测试。

设计压力测试后按“当前没有正式用户、成本优先”收敛 MVP：保留邀请码注册、邮箱密码登录、
30 天 Cookie 和单平台连接；Recovery Code、密码找回/修改、账号删除、换绑、旧数据迁移、
SQLite 认证库和多设备 Session 全部延后。

## 遇到的问题与解决

工作区已有 Web 同步、Coros 和页面相关的未提交改动，因此本次以当前工作树为基线，只做局部增量，
没有回退或覆盖已有功能。旧用户 JSON 缺少新增字段时继续兼容读取，已有 Cookie 会话保持有效。

邀请码验证与最终注册之间可能存在时间差，因此注册接口会再次验证，并在用户写入后核销；若核销
失败则删除本次刚创建的用户记录，避免绕过邀请门禁。当前部署为单进程，邀请码核销与邮箱唯一性
检查由进程内锁串行化。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/04-modules.md](../design/04-modules.md), [docs/design/05-data-flow.md](../design/05-data-flow.md), [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md)
