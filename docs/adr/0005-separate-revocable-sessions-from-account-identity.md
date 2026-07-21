---
status: superseded by ADR-0010
---

# Separate revocable sessions from account identity

`api_key` 只作为 neurun Account 的内部稳定标识，不得继续直接充当浏览器 Cookie。登录应签发独立、随机、服务端只保存哈希的 Session Token；每个会话固定 30 天过期，不做滑动续期，主动退出撤销当前会话。使用 Recovery Code 成功修改密码后必须撤销该账号的全部现有会话；已登录用户凭当前密码主动修改密码时保留当前会话、撤销其他会话，且不轮换 Recovery Code。这个选择增加少量会话持久化成本，但避免长期身份键泄露，并保证账号恢复可以真正排除仍持有旧 Cookie 的访问者。
