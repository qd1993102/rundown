# Bug: Garmin 中国区绑定成功后同步误报认证失败

- **发现日期**: 2026-07-29
- **修复日期**: 2026-07-29
- **严重程度**: major
- **影响范围**: Garmin 中国区 Web/CLI 的用户身份、活动、健康与记忆补全请求

## 现象

用户选择 `garmin.cn` 后可以完成登录和 Token 落盘，但首次批量同步先记录“Token 有效”，
随后返回 HTTP 401 和“Garmin 认证失败，请重新绑定账号”，连接状态被标记为 `expired`。

## 根因

`garmy.APIClient` 的 `domain` 参数默认是 `garmin.com`，不会从传入的 `AuthClient` 自动继承。
neurun 创建 APIClient 时只传了 `auth_client`，导致中国区 Token 被用于请求国际区 API。
garmy 的 profile 属性又会把底层 API 异常转换为空字典，上层拿到 `user_id=0` 后误判为认证失败。

## 修复方案

所有 Garmin APIClient 都显式传入当前 AuthManager、GarminAuth 或用户配置中的同一区域，覆盖
profile、活动、健康同步和记忆补全路径。增加回归测试，保证 `garmin.cn` 从认证客户端传播到
每一个数据客户端，同时保留真正 Token 失效时的 401 与重新绑定行为。

## 相关文件

- [src/auth.py](../../src/auth.py) — 提供绑定当前区域的 APIClient 创建入口
- [src/providers/garmin.py](../../src/providers/garmin.py) — profile 与活动请求复用区域一致的 APIClient
- [src/main.py](../../src/main.py) — 同步和记忆补全使用 Provider 的 APIClient
- [src/fetcher.py](../../src/fetcher.py) — CLI 拉取使用 AuthManager 的 APIClient
- [docs/product/data-source-sync.md](../product/data-source-sync.md) — 补齐数据源同步产品真相源与验收标准
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 明确 Garmin Token 与 API 区域一致性

## 验证

- 使用同一份有效中国区 Token 对比验证：默认 APIClient 的 profile 为空，显式传入
  `domain="garmin.cn"` 后返回有效用户 ID。
- 单元测试覆盖 AuthManager、Garmin Provider、同步注入和 CLI Fetcher 的区域传播。
- 完整运行 `pytest`，并重启本地 Web 后重放原批量同步请求。
