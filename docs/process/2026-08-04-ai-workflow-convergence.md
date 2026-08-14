# AI 工作流收敛与 Chat 删除

- **日期**: 2026-08-04
- **类型**: refactor

## 背景与动机

当前产品没有通用 Chat、聊天历史或自由对话入口的需求，但代码同时保留 Web SSE Chat、日报 Tool Use
Agent、Coach Skill contributor 编排和多个没有直接产品入口的 Skill。相同的 OpenAI-compatible 调用、
上下文收集和错误处理分散在多条路径中，日报在线成功时还会重复构建完整报告。

## 方案选择

保留六条已有明确入口的在线 AI 工作流：日报、周复盘、方案草稿、局部调整解释、方案重规划和
比赛策略。每条工作流只调用一个主 Skill，服务端先完成事实计算，模型只接收只读 JSON 并返回
固定 Schema。Web Chat、模型应用 tools、贡献 Skill 和运行期 ToolPolicy 全部删除；MCP 继续作为
外部 AI 客户端的独立集成边界，不进入应用内模型调用链。

没有引入多 Agent。当前工作流之间没有需要自治协作的共享目标，显式路由和确定性校验比代理间
交接更容易控制延迟、成本、写入权限和回放测试。

## 实现步骤

1. 回写报告中心、训练体验和 AI/部署/记忆技术设计，固定六条工作流与无 Chat 边界。
2. 删除 `/api/chat/stream`、`chat_stream()`、旧 Chat fallback 和 `prompts/coach.md`。
3. 将首页模板从 `chat.html` 更名为 `dashboard.html`，保留既有日报仪表盘能力。
4. 将日报调用迁移到 `review-daily-training`，删除 Tool Use loop、工具定义和历史上下文收集器。
5. 删除六个无直接模型入口的 Skill、贡献 Skill 编排、Context findings 和 ToolPolicy。
6. 日报改为内存构建一次、选择在线或本地洞察、最终渲染并落盘一次。
7. 更新测试，直接断言单 Skill 调用和通用 Chat 路由不存在。

## 遇到的问题与解决

`chat.html` 已经不再是聊天页面，而是日报仪表盘。删除 Chat 功能时没有删除页面能力，而是将模板
更名为 `dashboard.html`，同步修正路由、测试和现行技术文档，避免文件名继续制造错误架构暗示。

日报旧流程依赖模型自行调用应用 tools 补事实。收敛后由 `memory.py` 一次性生成经过数据门禁的
Front Matter，`coach.py` 只挑选所需字段，既避免模型获得写工具，也避免重复查询和多轮调用。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [日报产品方案](../product/daily-report.md)、[训练体验产品方案](../product/training-experience.md)
- Design: [AI 教练工作流设计](../design/ai-coaching.md)、[Web 应用部署方案](../design/13-sae-deployment.md)
