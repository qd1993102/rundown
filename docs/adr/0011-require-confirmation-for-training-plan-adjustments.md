# Require confirmation for training plan adjustments

neurun 的 AI 可以读取训练数据并生成训练方案调整提案，但不得直接修改已生效方案；只有用户查看变更内容、原因和影响并明确确认后，新方案版本才可生效。该边界牺牲部分全自动体验，以换取训练安全、用户控制权、可审计版本和跨 Web、CLI、MCP 一致的写入语义。
