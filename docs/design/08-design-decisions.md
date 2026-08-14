# 设计方案 — 8. 关键设计决策

> 属于 [设计方案索引](../design.md) · 版本 v3.1 · 2026-07-28

---

## 8. 关键设计决策

### 8.0 起步阶段的成本优先原则

- **先实现当前约束**：只实现已确认的单账号、单平台连接和必要安全边界，不提前建设多平台聚合、邮件服务或 Web 管理后台。
- **预留不预建**：未来能力通过清晰领域边界和可替换接口预留，不为尚未发生的需求增加运行依赖或运维流程。
- **邀请码不自动过期**：系统随机生成的一次性邀请码在使用或管理员停用前持续有效，避免引入过期管理成本。
- **例外**：可能导致账号接管、不可控数据删除或高迁移成本的风险不以“低成本”为由省略。

### 8.1 安全策略

- **密码不入 Git**: `.env` 加入 `.gitignore`，提供 `.env.example` 模板
- **日志脱敏**: 邮箱地址在日志中做部分遮蔽（`j***@example.com`）
- **Token 本地加密**: 依赖 garmy 的 `~/.garmy/` 文件权限（0600），如需进一步增强可考虑 keyring 集成
- **HTTPS 强制**: garmy 内部所有请求均为 HTTPS

### 8.2 容错设计

- **网络重试**: garmy 内置 3 次重试 + 指数退避（可配置 `GARMY_RETRIES`）
- **部分失败继续**: 某天某指标拉取失败不中断整体同步，记录到 `sync_status` 表
- **API 限流应对**: garmy 对 429 (Too Many Requests) 自动重试
- **优雅降级**: 某个指标 API 不可用时，跳过而非崩溃

### 8.3 扩展性预留

- **中国区切换**: 仅需设置 `GARMIN_DOMAIN=garmin.cn`
- **多用户支持**: 当前单用户设计，但 LocalDB 表结构已包含 `user_id` 字段，天然支持未来多用户
- **MCP Server**: garmy 已内置 MCP Server，启动 `neurun mcp` 即可对接 Claude Desktop
- **自定义指标**: 可继承 garmy `BaseMetric` 注册自定义指标
- **记忆扩展**: 新增记忆类型只需：(1) 定义 YAML Front Matter schema，(2) 创建目录，(3) 注册到 MemoryStore 的 type 枚举
- **多语言记忆**: Front Matter 字段与 Markdown 正文分离，正文可自由使用任何语言
- **记忆同步**: memory/ 目录天然可通过 Git 跨设备同步，也可通过 iCloud/Dropbox 等云盘同步

### 8.4 AI 只服务明确工作流，MCP 保持独立集成边界

neurun 当前没有通用 Chat 产品需求。Web 只提供结构化训练、报告、同步和档案旅程；应用内模型
调用只允许六个显式工作流：日报解释、周复盘、训练方案草稿、局部调整说明、方案重规划和比赛策略。
训练前说明、计划执行比较、恢复评估和 intake 由本地规则或表单完成，不拆成模型调用。

每个在线工作流固定只运行一个主 Skill。应用服务先从 SQLite、Memory 和生效方案计算完整事实包，
再将只读 JSON 交给模型；模型不获得读取数据库、搜索记忆或写入方案的 tools。输出必须经过对应
Schema 与确定性 Validator，保存、提案和版本切换仍由应用服务负责。

```mermaid
flowchart LR
    UI["Web / CLI / MCP 显式动作"] --> APP["应用服务构建结构化事实"]
    APP --> SKILL["一个主 Skill"]
    SKILL --> MODEL["OpenAI-compatible JSON 调用"]
    MODEL --> VALIDATE["Schema + 确定性校验"]
    VALIDATE --> WRITE["应用服务渲染或创建提案"]
```

MCP Server 继续向 OpenClaw / Claude Desktop 提供只读资源与显式业务工具，但它是外部 AI 客户端的
集成接口，不是 Web Chat 的后端，也不进入上述模型调用链。MCP 写操作继续遵守用户确认、版本化和
管理员工具默认关闭等既有边界。已生效训练方案不得由任何 AI 直接覆盖；AI 只能生成提案，用户确认
后才创建新 Scheme Version，详见 [ADR-0011](../adr/0011-require-confirmation-for-training-plan-adjustments.md)。

完整工作流与 Skill 合同见 [AI 教练工作流设计](ai-coaching.md)，产品行为见
[训练体验产品方案](../product/training-experience.md) 和 [报告中心产品方案](../product/daily-report.md)。
