# Bug: Coros Web 同步误入 Garmin 链路并触发 ProgressReporter.warning 异常

- **发现日期**: 2026-07-19
- **修复日期**: 2026-07-19
- **严重程度**: major
- **影响范围**: Web 多用户 Coros 同步、Coros token 恢复、garmy 活动同步异常处理

## 现象

用户已在 Web 端绑定 Coros，但点击同步后接口返回：

```json
{
  "status": "error",
  "message": "'ProgressReporter' object has no attribute 'warning'"
}
```

## 根因

`UserConfig.provider_type` 始终读取服务级 `NEURUN_PROVIDER`。服务默认值为 `garmin`
时，即使 `UserRecord.provider` 已记录为 `coros`，`POST /api/sync` 仍会创建
`GarminProvider` 并进入 garmy `SyncManager`。

Garmin 活动分页随后失败；当前 garmy 的 `ActivitiesIterator` 在异常分支调用
`ProgressReporter.warning()`，但同版本的 `ProgressReporter` 只实现了 `info()` 和
`error()`，次生 `AttributeError` 覆盖了原始同步错误。

此外，Coros 绑定 token 原先只由第三方库写入全局位置，新的 Web 请求无法按 neurun
用户安全、稳定地恢复认证。

## 修复方案

- 为 `UserConfig` 增加用户级 provider 覆盖值，`/api/sync` 从 `UserRecord.provider`
  注入，确保 Coros 用户进入 Coros Provider。
- Coros 绑定成功后把 `StoredAuth` 保存到用户专属 token 目录，后续请求无需保存明文
  密码即可恢复认证；目录权限为 `0700`，文件权限为 `0600`。
- 兼容升级前已经绑定的用户：仅当系统中恰好存在一个 active Coros 用户时，将
  coros-mcp 旧版全局 token 自动迁入该用户目录；多用户时不猜测 token 归属。
- 非 Garmin Provider 认证失败时立即返回“请重新绑定账号”的可操作错误。
- 初始化 garmy `SyncManager` 时补齐 `ProgressReporter.warning()` 兼容接口，保留原始
  活动拉取警告。

## 相关文件

- [src/config.py](../../src/config.py) — 支持用户级 provider 覆盖
- [src/web.py](../../src/web.py) — 同步前注入用户绑定的数据源
- [src/providers/coros.py](../../src/providers/coros.py) — 按用户持久化和恢复 Coros token
- [src/main.py](../../src/main.py) — 校验非 Garmin Provider 认证结果
- [src/storage.py](../../src/storage.py) — 增加 garmy 进度报告器兼容层
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 同步多平台认证与路由设计
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 同步 Web 多用户数据流

## 验证

- 新增测试确认用户级 `coros` 会覆盖服务默认 `garmin`。
- 新增测试确认 Coros token 写入用户目录、权限正确，并可在新 Provider 实例中恢复
  `user_id`。
- 新增测试确认旧版全局 token 仅在 Coros 用户归属唯一时自动迁移，多用户时跳过。
- 新增测试确认缺少 `warning()` 的进度报告器会获得兼容方法并正常记录原始警告。
- 运行完整 `pytest`，确认零失败。
