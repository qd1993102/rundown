# 设计方案 — 2. 前置调研结论

> 属于 [设计方案索引](../design.md) · 版本 v3.0 · 2026-06-24

---

## 2. 前置调研结论

### 2.1 为什么选 garmy

| 对比维度 | garmy | garminconnect | garth |
|---------|-------|---------------|-------|
| 维护状态 | ✅ 活跃 (2025.06 发布 v1.0) | ✅ 活跃 | ❌ 已停止维护 |
| AI/LLM 集成 | ✅ 内置 MCP Server | ❌ 无 | ❌ 无 |
| 本地数据库 | ✅ 内置 SQLite + SyncManager | ❌ 无 | ❌ 无 |
| 类型安全 | ✅ 完整 dataclass + 类型标注 | 部分 | 部分 |
| 中国区支持 | ✅ domain 参数切换 | ✅ is_cn 参数 | ✅ domain 参数 |
| 自动发现指标 | ✅ 自动注册 metric | ❌ | ❌ |
| CLI 工具 | ✅ garmy-sync / garmy-mcp | ❌ | ❌ |

**结论**: garmy 是最契合需求的选择——其 LocalDB 模块天然解决了"拉取→存储→查询"的闭环，MCP Server 为后续 AI 分析预留了扩展空间。

### 2.2 国际区 vs 中国区

用户使用"佳明国际"账号，即 Garmin Connect 国际区（`garmin.com`）。garmy 默认连接 `garmin.com`，无需额外配置。若后续需要切换到中国区（`garmin.cn`），仅需将 `domain` 参数改为 `"garmin.cn"`。

### 2.3 认证机制

garmy 使用 Garmin Connect 移动端 SSO OAuth 流程：
1. 模拟 Android 应用 User-Agent 请求 SSO 嵌入页
2. 提取 CSRF Token
3. 提交邮箱/密码表单
4. 如有 MFA，支持交互式输入或返回 MFA 状态
5. 获取 OAuth1 Token → 交换 OAuth2 Token
6. Token 自动持久化到 `~/.garmy/`（支持自动刷新）

---
