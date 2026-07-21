---
status: superseded by ADR-0010
---

# Store authentication state in SQLite

neurun 将账号、完整 Invitation、Recovery Code 哈希和可撤销 Session 统一存入独立 SQLite 认证库，不再分散写入用户与邀请码 JSON。账号创建和 Invitation 核销必须在同一数据库事务中提交，确保进程崩溃或并发请求下仍满足“一码一账号”；SQLite 属于 Python 内置、随数据卷持久化的单机组件，不增加外部服务或运维成本。平台训练数据库继续按账号隔离，不能与认证库混用。
