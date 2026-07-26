# Bug: 同步在平台认证前读取用户 ID

- **发现日期**: 2026-07-26
- **修复日期**: 2026-07-26
- **严重程度**: major
- **影响范围**: Garmin、Coros、Huawei Web 同步与 Huawei Web 绑定

## 现象

应用账号与运动平台绑定成功后，Web 同步仍可能返回
`Not authenticated. Please login first.`。Garmin 会直接抛出未认证错误；Coros 在凭据
未恢复时可能得到 `user_id=0`；Huawei Web 绑定的 CrewPals token 没有保存到用户目录，
后续同步会错误依赖服务级配置。

## 根因

公共 `_setup()` 在调用 `provider.authenticate()` 前读取 `provider.user_id`。Garmin 的
`get_user_id()` 需要已认证客户端，Coros 和 Huawei 也只有在恢复或取得有效 token 后才有
可靠身份。Huawei Web 路由还尝试给只读属性直接赋值，且没有持久化用户级凭据。

## 修复方案

- 三个平台统一执行“恢复/认证凭据 → 校验成功 → 读取正整数 `user_id` → 打开本地存储”。
- 新增 `ProviderAuthenticationError`；Web 将三平台认证失效统一返回 HTTP 401、连接状态改为
  `expired` 并提示重新绑定，认证失败前不写 SQLite。
- Huawei 绑定先以内存凭据认证，成功后才将 CrewPals token 私密保存到当前用户目录；认证
  返回 `False` 时不保存凭据。

## 相关文件

- [src/main.py](../../src/main.py) — 统一 Provider 初始化顺序和认证错误
- [src/web.py](../../src/web.py) — 三平台同步失效处理及 Huawei 绑定持久化
- [src/config.py](../../src/config.py) — Huawei 用户级 CrewPals token 读写
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 认证初始化契约
- [docs/design/05-data-flow.md](../design/05-data-flow.md) — 同步写入前置条件

## 验证

- 参数化测试确认 Garmin、Coros、Huawei 均先认证再读取 `user_id`。
- 测试确认认证异常或返回 `False` 时不会读取 `user_id`。
- Web 测试确认三平台认证失败均返回 401 并标记 `expired`。
- Huawei Web 测试确认认证成功后才生成用户凭据文件，失败时不落盘。
- 完整执行 `pytest`，确认零失败。
