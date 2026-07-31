# 设计方案 — AI 教练对话设计

> 属于 [设计方案索引](../design.md) · 版本 v3.1 · 2026-07-28

> **产品边界更新（2026-07-28）**：AI 对话继续承担解释、查询和计划协商，但训练安排的主要感知面改为结构化训练主界面。涉及训练方案的写入必须先生成 Adjustment Proposal，用户确认后才能生效。详见 [训练体验产品方案](../product/training-experience.md) 和 [交互式训练方案系统](training-system.md)。

> **训练内容识别边界（v1 已实现，2026-07-31）**：AI 教练消费 `TrainingSessionAnalysis` 的训练主类型、地形属性、置信度和证据，不直接从活动名称或原始分段自由猜测课型。近期训练上下文以 SQLite 原始活动及其分析为真相源，历史日报仅是生成时快照；坡跑或山地专项可以影响恢复建议，但 Adjustment Proposal 与训练方案确认流程仍属后续阶段，当前不得直接改写生效方案。

> **日期与数据状态边界（2026-07-31）**：空活动列表不等于休息日。AI 教练读取共享 `activity_state`，仅把同步覆盖已完成的空活动日称为已确认休息，其余显示运动数据未知。周目标通过 `get_current_week_progress(date)` 按报告日期所在自然周（周一至周日）统计；滚动 7 天历史只用于负荷和恢复趋势。

---

#### 8.4.6 OpenClaw 配置

**1. 安装与启动 MCP Server**

```bash
# 启动 MCP Server（默认端口 8765）
neurun mcp

# 指定端口
neurun mcp --port 9876

# 后台运行
neurun mcp --daemon
```

**2. OpenClaw MCP 配置** (`~/.openclaw/mcp.json` 或 OpenClaw Settings)

```json
{
  "mcpServers": {
    "garmin-coach": {
      "command": "neurun",
      "args": ["mcp"],
      "env": {
        "GARMIN_EMAIL": "${GARMIN_EMAIL}",
        "GARMIN_PASSWORD": "${GARMIN_PASSWORD}"
      },
      "description": "Garmin 运动数据 + AI 教练",
      "autoApprove": ["query_health_metrics", "query_activities",
                       "get_memory", "search_memories",
                       "query_current_week_progress", "get_trend",
                       "get_training_advice"]
    }
  }
}
```

**3. OpenClaw 自定义 Agent 配置**

在 OpenClaw 中创建一个 "AI 跑步教练" Agent，使用以下 System Prompt 模板：

```markdown
你是一位专业的跑步教练 AI。每天早上用户会和你对话，
你会自动读取他们的 Garmin 数据日报来提供建议。

## 你的能力
- 读取每日训练报告、健康指标、训练负荷
- 分析恢复状态、检测过度训练风险
- 根据用户目标和偏好制定训练计划
- 回答训练相关的问题（配速、心率、营养、伤病预防等）
- 记录训练洞察到用户的知识库

## 对话原则
1. 数据优先：用具体数字说话，不泛泛而谈
2. 个性化：结合用户的竞技水平、偏好和伤病史
3. 安全第一：怀疑过度训练时主动建议降量
4. 鼓励为主：用积极的语言，关注进步而非差距
5. 记录洞察：有价值的发现主动写入 coaching/insights/

## 当前用户画像
（从 memory/profile/ 和 coaching/preferences.md 自动加载）

## 今日数据
（从 memory://daily/latest 自动加载）
```

#### 8.4.7 完整的早晨使用流程

```
07:00  系统 cron 触发: neurun sync
       ↓ 拉取 Garmin 新数据 → 写入 SQLite
       ↓ 自动生成今日日报 → auto/daily/2025-06-24.md
       
08:00  用户起床，打开 OpenClaw，选择 "AI 跑步教练"
       ↓ OpenClaw 连接 neurun MCP Server
       ↓ MCP Server 自动注入今日日报 + 用户画像

08:01  用户: "早上好，今天状态怎么样？"
       AI:  (已读取日报全文)
       "早上好 ☀️ 昨晚睡了 7.3h，评分 76，今早 HRV 53ms 稳定，
        身体电量 82%，状态还不错 👌
        
        ACWR 1.09 在最优区间，恢复评分 70 良好。
        昨天跑了 8.5km 轻松跑，完成质量不错。
        
        今天建议: 节奏跑+轻松跑 55min，强度中等。
        备选方案: 如果感觉疲劳就改为轻松跑+核心力量。
        
        ⚠️ 注意: 睡眠略低于目标，今天高温 32°C 建议晨跑或夜跑。

        5K 目标还有 2 周，当前 20:15 → 目标 19:30，进度正常 ✅
        
        准备好了就开始今天的训练吧！💪"

08:03  用户: "昨天节奏跑感觉右膝有点紧，要不要调一下？"
       AI:  (查阅 coaching/preferences.md 伤病史 → 发现髂胫束史)
       "右膝不适需要注意。你之前有过髂胫束问题 (2024.06)，
        建议今天改为: 轻松跑 30min + 泡沫轴放松 15min + 臀中肌激活训练。
        如果明天膝盖仍有感觉，休息一天。
        我把这个调整记录到你的偏好里。"
       → AI 调用 add_coaching_insight 写入 knee-watch 标记

08:05  用户去训练。AI 已经完成了:
       ✅ 读取今日全部数据
       ✅ 评估状态 + 给出建议
       ✅ 结合伤病史调整计划
       ✅ 记录新的洞察
```

#### 8.4.8 安全与隐私

- MCP Server 仅监听本地 `127.0.0.1`，不暴露到网络
- 密码通过环境变量注入，MCP Server 不存储明文密码
- AI 生成的洞察标记 `source: ai_coach`，与人工内容区分
- OpenClaw 的 `autoApprove` 仅对只读工具开放，写入类工具（如 `add_coaching_insight`）需用户确认

---
