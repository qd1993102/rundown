# Bug: Garmin Access Token 到期后反复要求重新输入密码

- **发现日期**: 2026-07-29
- **修复日期**: 2026-07-29
- **严重程度**: major
- **影响范围**: Garmin 国际区/中国区 Web 与 CLI 同步认证、连接状态判定

## 现象

Garmin 绑定成功后可以正常同步，但经过约一天再次同步时提示“Garmin 认证失败，请重新绑定账号”。
用户必须重新输入 Garmin 账号和密码，重新绑定后同步立即恢复。一次本地复现中，后台先执行
账号密码登录并收到 SSO 401；用户重新绑定生成新 Token 后，下一次批量同步成功。

## 根因

garmy 同时保存短期 Access Token 和更长期的刷新凭据。Access Token 到期后，
`AuthClient.needs_refresh` 已能识别仍可刷新，`get_auth_headers()` / `refresh_tokens()` 也提供续期
能力；但 neurun 的 `GarminAuth.login()` 只检查 `is_authenticated`，为 `False` 时直接执行
`login(email, password)`，跳过了刷新分支。

Web 后台刻意不保存 Garmin 明文密码，因此该回退使用空密码请求 SSO 并收到 401，随后公共同步
初始化把它转换为 `ProviderAuthenticationError`，任务失败回调又将连接标记为 `expired`。
此外，Provider 初始化和 profile 读取曾把任意异常或空 profile 都扩大为认证失败，使 Garmin
临时网络、5xx 或响应异常也可能要求用户重新绑定。

## 修复方案

- `GarminAuth` 和 `AuthManager` 加载 Token 后先判断 `is_authenticated`，再判断
  `needs_refresh`；可刷新时调用 `refresh_tokens()` 并复用持久化的新 OAuth2 Token。
- Garmin 后台没有可刷新凭据且没有密码时才返回认证失败，不再尝试空密码 SSO。
- 刷新明确返回 401/403 时视为凭据失效；超时、限流、5xx 和其他异常向上保留为可重试失败。
- Garmin profile 改用不吞异常的 `connectapi()`；只有嵌套 HTTP 状态为 401/403 才归类认证
  失效，profile 临时错误和缺失 ID 不再改变连接状态。
- `_setup()` 保留 Provider 原始异常；只有 `authenticate()` 明确返回 `False` 时创建
  `ProviderAuthenticationError`，无效 `user_id` 使用独立的临时身份错误。

## 相关文件

- [src/auth.py](../../src/auth.py) — 共用认证管理器的 Token 自动刷新
- [src/providers/garmin.py](../../src/providers/garmin.py) — Garmin 同步认证、刷新和 profile 异常传播
- [src/main.py](../../src/main.py) — Provider 认证与身份错误边界
- [src/web.py](../../src/web.py) — 识别 garmy 嵌套的 401/403，非认证错误保持连接可用
- [docs/product/data-source-sync.md](../product/data-source-sync.md) — 用户旅程和验收标准
- [docs/design/04-modules.md](../design/04-modules.md) — Token 生命周期设计
- [docs/design/05-data-flow.md](../design/05-data-flow.md) — 同步认证数据流
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 三平台认证初始化契约

## 验证

- 测试确认 Access Token 过期、Refresh Token 有效且 Web 无密码时，只调用刷新、不调用 SSO。
- 测试确认刷新成功后继续认证，刷新 401/403 才要求重新绑定，刷新超时向上保留。
- 测试确认 profile 临时异常不转换为 `user_id=0`，Provider 临时异常不转换为认证失败。
- Web 任务测试确认认证失败将连接标记为 `expired`，临时超时返回可重试错误且保持 `active`。
- 完整执行 `pytest`，确认零失败。
