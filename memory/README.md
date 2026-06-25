# 记忆库使用指南

欢迎来到 Rundown 记忆库。这里以人类可读、AI 可消费的格式存储你的运动知识。

## 目录结构

```
memory/
├── auto/           # 自动生成（由 rundown sync 写入，请勿手动编辑）
│   ├── daily/      # 每日综合报告 ⭐
│   ├── summaries/  # 周/月度运动摘要
│   ├── recovery/   # 周恢复摘要
│   └── execution/  # 训练计划执行跟踪
│
├── profile/        # 竞技档案（手动维护）
│   ├── fitness-assessment.md
│   ├── race-records.md
│   └── fitness-history.md
│
├── goals/          # 目标管理（手动维护）
│   ├── active/     # 进行中
│   ├── completed/  # 已完成
│   └── archived/   # 已归档
│
├── plans/          # 训练计划（手动维护）
│   ├── active/     # 执行中
│   ├── completed/  # 已完成
│   └── templates/  # 计划模板
│
└── coaching/       # 教练知识（手动维护）
    ├── preferences.md
    ├── cases/      # 训练案例
    ├── insights/   # 训练心得（人工）
    └── ai-insights/ # AI 教练洞察（自动生成）
```

## 记忆类型

| 类型 | 说明 | 维护方式 |
|------|------|---------|
| 每日报告 | 全方位日度分析 | 自动 |
| 运动摘要 | 周/月运动统计 | 自动 |
| 恢复摘要 | 周恢复状态评估 | 自动 |
| 执行跟踪 | 训练计划执行情况 | 自动 |
| 竞技档案 | 水平评估、成绩 | 手动 |
| 目标管理 | 训练目标与里程碑 | 手动 |
| 训练计划 | 周期性计划 | 手动 |
| 教练知识 | 偏好、案例、心得 | 手动 + AI |

## 使用方式

```bash
# 查看记忆列表
rundown memory list --type daily_report
rundown memory list --tag 5k --status active

# 查看单条记忆
rundown memory show 2025-06-24

# 搜索记忆
rundown memory list --search "间歇跑"

# 创建目标
rundown memory goal create

# 完整性检查
rundown memory check
```
