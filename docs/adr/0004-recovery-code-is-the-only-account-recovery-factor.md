---
status: superseded by ADR-0010
---

# Use a one-time recovery code without an administrative bypass

注册成功时向用户展示一次 Recovery Code，服务端只保存其哈希；成功页必须提供复制和下载，并要求用户确认“已保存且理解丢失后果”后才能进入平台绑定，离开页面后不得再次返回该明文。在 Login Email 未验证的第一版中，它是密码之外唯一可信的账号恢复因素。每次成功恢复必须原子地设置新密码、作废已使用的 Recovery Code，并生成一个只展示一次的新 Recovery Code。系统不得提供仅凭邮箱或管理员主观判断执行的密码重置后门；密码和 Recovery Code 同时丢失时，原账号不可恢复，用户只能凭新 Invitation 创建新账号。这个限制牺牲可恢复性，以避免低成本部署中的人工恢复流程成为账号劫持入口。
