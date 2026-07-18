# Bug: 外部 Huawei Token 字段无法识别

- **发现日期**: 2026-07-15
- **修复日期**: 2026-07-15
- **严重程度**: major
- **影响范围**: Huawei 本地 AT 导入、认证状态与用户识别

## 现象

外部 token 使用 `expired_at`、`open_id` 和 `user_id` 时，Rundown 只读取 `expires_at` 与 `openid`，导致仍有效的 AT 被判定为过期，继而尝试刷新或重新授权。

## 根因

Huawei token 读取逻辑固定使用单一字段命名，且保存时无条件重算 `expires_at`，未兼容已有绝对过期时间和外部用户标识。

## 修复方案

过期判断同时接受 `expired_at` / `expires_at`，用户标识依次接受 `user_id`、`open_id`、`openid` 和 `sub`。保存 token 时优先保留已有绝对过期时间。

## 相关文件

- [src/providers/huawei.py](../../src/providers/huawei.py) — 扩展 token 字段兼容和用户 ID 解析
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 记录兼容字段标准

## 验证

新增外部 token 结构复现测试，验证认证状态、Bearer 请求头和 `user_id` 均能正确读取；完整测试套件通过。
