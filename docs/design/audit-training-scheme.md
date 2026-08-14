# 草稿可执行性审计（audit-training-scheme）技术设计

- **日期**: 2026-08-12
- **类型**: feature
- **状态**: 待确认

## 背景与动机

草稿课表从"可执行"角度出现过严重问题：长距离 43.5km（超 180min 约束）、轻松跑 21.7km、
定时跑距离缺失等。确定性层已修复定时跑距离估算，但单课剂量、恢复节奏、长距离上限等
可执行性约束仍依赖 AI 骨架自觉，缺少**独立审计环节**。新增单独调用的 AI 审计 Skill，
审计不过自动打回重新生成，从流程上保证草稿可执行。

## 方案

### 1. 新 Skill：`audit-training-scheme`

- **职责**：独立审计训练方案草稿的可执行性，只输出结构化审计结论，不改写方案。
- **输入事实**（只读）：
  - 训练框架（阶段、周量、长距离原则）
  - 近期课表（当前周 + 下一周逐日课程：类型/距离/时长/强度/配速意图）
  - 确定性约束（`max_session_minutes`、可训练日、`preferred_terrain`）
  - 用户补充信息（`supplement`/`additional_context`，用于周中长时豁免判断）
  - 个人配速参考、恢复快照、近两周执行历史
- **输出模型 `SchemeAudit`**：

```json
{
  "verdict": "pass | fail",
  "summary": "一句话结论",
  "issues": [
    {
      "code": "long_run_exceeds_session_limit",
      "severity": "critical | warning",
      "message": "周日长距离 34.5km 按个人配速约 3.2 小时，超过单次最长 180 分钟",
      "recommendation": "收敛至 26-30km 或拆分"
    }
  ]
}
```

- **审计维度**（以可执行为主，替代不了确定性门禁、与之互补）：
  1. ~~单课时长 vs `max_session_minutes`~~（**放宽**：仅作 warning 提示，不阻断；超限本身不判 fail）；
  2. 长距离上限（相对个人历史长距离/全马距离比例，**critical**）；
  3. 质量课间隔（间歇/节奏/阈值等不能连续排，质量课后次日应有恢复，**critical**）；
  4. 长距离或质量课后次日的恢复距离合理性（**critical**）；
  5. **周中（周一至周五）长时课程**（**critical**）：周中课程按个人配速推算超过**周中时长上限（默认 120 分钟）**即 fail；**除非用户补充信息明确提到周中可长时训练**（如“工作日晚上可跑 2 小时”），否则不豁免；
  6. 周量分配（距离型 vs 质量课比例、周目标一致性，**warning**）；
  7. 配速意图与个人水平匹配（**warning**）。
- **契约约束**：`issues` 为空且 `verdict=pass` 才算通过；`warning` 不阻断、`critical` 阻断；
  **单课时长超过 `max_session_minutes` 仅作 warning 提示，不判 fail**（如长距离慢跑超 3 小时
  对全马选手可接受）；AI 只输出结论与建议，不直接修改课表。

### 2. 草稿流程接入（`_plan_dag` 第 5 阶段）

```
framework(1/4) → near_term(2/4) → normalization(3/4) → validation(4/4)
                                              ↓
                              audit-training-scheme（独立 AI 调用，5/5）
                                              ↓
                         确定性复核（critical 数值规则用确定性代码复核）
                                              ↓
                          verdict=pass → 完成草稿
                          verdict=fail → 打回重新生成
```

- **确定性复核**：AI 审计判定的 critical 中，可数值化的规则（`long_run_exceeds_limit`、
  `weekday_session_too_long`、`quality_back_to_back`）在打回前用确定性代码复核——
  长距离阈值按 `min(个人历史×1.15, 全马×0.75)`（基础期再限 `全马×0.5`）、周中时长按
  个人配速推算与 120 分钟比较（补充信息明确允许时豁免）、质量课连排/恢复规则按课型与
  距离阈值判断；**复核不确认的 critical 自动降级为 warning**，避免 AI 数值误判（如
  21km < 21.1km 却判超限）触发无谓打回。无法确定性复核的定性 issue 保留 AI 判定。

- **打回重生成**：带本次审计 `issues` 作为上下文重新调用 `build-near-term-schedule`
  （框架不重生成，仅重排课表），重生成后再审计；
- **重试上限**：最多 **2 轮**重生成；仍 `fail` 则草稿标记 `audit_failed=true` 并持久化
  `audit.issues`，前端展示"可执行性审计未通过"及原因，用户可手动再生成；
- **成本控制**：审计输入只含紧凑课表摘要与约束，不使用完整 prompt/载荷；每次草稿最多
  3 次 AI 调用（1 审计 + 2 重生成 × 1 审计）。
- **失败降级**：审计 Skill 本身调用失败（超时/schema）不阻断草稿——标记
  `audit_unavailable`，草稿按确定性校验结果放行，避免审计成为单点故障。

### 3. 前端展示

- 草稿预览"方案周期"上方新增审计状态条：`已通过可执行性审计` / `审计未通过（原因）` / `审计暂不可用`；
- `audit_failed` 时展示 issues 列表（message + recommendation），提供"重新生成"入口。

## 涉及文件

- `prompts/skills/audit-training-scheme/SKILL.md` — 新 Skill 指令
- `src/coach_runtime/registry.py` — 注册 SkillSpec（含输出模型 SchemeAudit）
- `src/coach_runtime/schemas.py` — `validate_scheme_audit`
- `src/coach_runtime/runner.py` — `_OUTPUT_JSON_EXAMPLES/_OUTPUT_JSON_RULES` 增加 SchemeAudit
- `src/training_planning.py` — `_plan_dag` 增加审计阶段与打回重试
- `web/templates/training.html` — 审计状态展示
- `tests/` — 审计通过/打回/降级路径测试

## 验收标准

1. 草稿生成后自动调用审计 Skill（独立 AI 调用）；
2. `verdict=fail`（存在 critical 级 issue：长距离失控、质量课连排/恢复不足等）自动打回重新生成（最多 2 轮），`pass` 才正常完成；单课时长超限仅 warning 不阻断；
3. 审计 Skill 调用失败不阻断草稿（标记 `audit_unavailable`）；
4. 前端展示审计状态与未通过原因；
5. 测试覆盖 pass / fail 打回 / 2 轮上限 / 审计降级四条路径。
