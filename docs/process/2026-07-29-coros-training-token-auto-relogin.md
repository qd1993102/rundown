# Coros Training Hub 与 Mobile 分域自动鉴权

- **日期**: 2026-07-29
- **类型**: feature

## 背景与动机

Garmin 有可持久化的刷新凭据，而 Coros Training Hub 只有短期 Access Token。原实现把
`result=1019` 直接转为 `expired`，使用户周期性重复输入高驰账号密码。Mobile 睡眠凭据虽可重放，
但不能跨 API 续期 Training Hub。

## 方案选择

没有伪造 Refresh Token，也没有保存明文密码。选择在用户明确同意后保存 Fernet 加密的 Training
Hub 登录重放对象；其中 MD5 密码摘要仍被视为密码等价物。密钥由部署环境独立注入，数据备份不
包含密钥。自动重登只由明确认证失效触发，并限制为单用户一次登录、原请求一次重试。

安全存储可用时默认开启以避免短期 Token 周期性中断，用户可在提交前取消；密钥缺失时授权继续
成功但开关禁用并解释原因。运动和睡眠密文可分别删除，且删除不影响当前 Access Token。

## 实现步骤

1. 先增加密文、权限、密钥缺失、`1019`、并发和重试上限的失败测试。
2. 新增 `CorosReloginCredentialStore`，复用私有目录和原子写入工具。
3. 在 `CorosAuth` 增加按 Token 文件隔离的 singleflight、重放登录和一次重试包装。
4. 将活动、每日分析和 HRV 接入统一包装，保持 Mobile 睡眠独立降级。
5. Web 拆分 Training Hub 与 Coros App 表单，增加默认开启的分域自动鉴权字段；“我的”增加两套
   状态、按钮和删除 API。
6. Mobile 重放载荷从 token JSON 迁入独立 Fernet 密文；刷新在 neurun 用户域内完成，避免上游
   `_save_auth` 写入全局认证文件。
7. 同步配置、依赖、README、产品、模块、数据流、多平台与部署设计。
8. 完整执行 `pytest`（`214 passed`），并执行 Python 编译、内联 JavaScript 解析与 diff 检查。

## 遇到的问题与解决

- `coros-mcp.StoredAuth` 没有 Training Hub Refresh Token：复用登录接口的账号、区域和 MD5 摘要
  重放对象，不依赖不存在的刷新协议。
- 多个 Provider 实例可能同时看到旧 Token：以 `coros-auth.json` 路径作为进程内锁键，锁内重新
  读取文件；其他请求已写入新 Token 时直接复用。
- 健康接口原先吞掉所有异常：只让明确的认证失效进入重登/过期路径，普通健康数据异常继续降级。
- 密钥缺失不能阻断人工授权：保留活动/Mobile Token，禁用自动鉴权并返回不含凭据的警告，不回退
  明文落盘。
- `coros-mcp.fetch_sleep()` 的内置刷新会写全局认证文件：首次请求不注入重放载荷，收到 `1019`
  后由 neurun 在用户锁内直接重放 Mobile 登录，只保存用户目录 Token 再重试一次。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/data-source-sync.md](../product/data-source-sync.md)
- Design: [docs/design/12-multi-platform.md](../design/12-multi-platform.md)
- Bugfix: [docs/bugfixes/2026-07-29-coros-training-token-auto-relogin.md](../bugfixes/2026-07-29-coros-training-token-auto-relogin.md)
