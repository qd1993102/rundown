---
status: superseded by ADR-0009
---

# Allow one-time legacy account claim without an invitation

旧用户 JSON 迁入认证库时保留原内部账号 ID、Platform Connection 和数据目录，并标记为 `legacy_unclaimed`；由于旧记录缺少昵称、Login Email 和密码且存在重复平台账号，系统不得自动合并或猜测归属。持有对应旧 `api_key` Cookie 的用户可以执行一次 Legacy Account Claim，设置昵称、Login Email、密码并领取 Recovery Code，而不消耗 Invitation；成功后旧 Cookie 立即永久失效并签发新 Session。这个受限例外只用于迁移，不能成为长期开放注册入口。
