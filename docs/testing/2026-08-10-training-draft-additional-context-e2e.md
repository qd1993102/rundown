# E2E 验收报告：训练草稿生成前补充信息窗口

- **日期**: 2026-08-10
- **范围**: `additional_context` 向导窗口、同次草稿 Prompt 传递、脱敏存储和 Web 健康检查
- **状态**: ready

## 结论

最新训练向导改动通过独立 E2E/回归验收。未修改业务代码或测试文件。

## 验收结果

| 场景 | 结果 | 证据 |
|---|---|---|
| 页面展示补充窗口与三段文案 | PASS | `tests/test_web.py`：训练页包含“还有什么需要教练知道”“提交并生成”“跳过并生成”，并包含 `additional_context` |
| 提交/跳过路径不新增必需准入 | PASS | `web/templates/training.html`：窗口只在现实约束提交后出现；提交和跳过都调用同一 `createDraft()`，跳过清空文本后继续请求 |
| 补充文本进入同次草稿 Prompt | PASS | `tests/test_coach_runtime.py`：仅调用 `draft-training-scheme` 一次，Prompt 包含 `user_supplement` 与“未验证”约束；未发现第二次串行模型调用 |
| 草稿 payload/持久化不含原文 | PASS | 隔离 `TrainingService.create_draft()` 场景注入“本周出差两天，长距离尽量安排周末；不要保存这句原文”，检查最终 `plans/drafts/*.md`：原文不存在，`additional_context` 字段不存在，仅保留 `supplement` 元数据（request_id、字符数、状态、截断标记、采用标记） |
| AI 不可用仍可生成草稿 | PASS | 同一隔离场景走 deterministic fallback，`supplement.status=supplement_unavailable`，草稿正常生成 |
| 定向回归 | PASS | `pytest -q tests/test_training_planning.py tests/test_coach_runtime.py tests/test_web.py` → `93 passed in 5.81s` |
| HTTP 健康检查 | PASS | 临时启动项目 `cmd_serve`（隔离 `/private/tmp/rundown-health-data`），`curl http://127.0.0.1:8080/healthz` 返回 `{"status":"ok","release":"development"}`；已停止临时进程 |

## 失败层级与剩余风险

- **业务层失败**: 无。
- **测试层失败**: 无。
- **环境说明**: 沙箱内直接绑定 8080 被限制，使用项目入口在允许本地绑定的临时进程中完成 healthz 验证；不是应用启动失败。
- **未验证假设**: 未使用真实 AI Provider 验证模型如何实际采纳补充文本；已验证 Prompt 合同、单次调用和 Provider 不可用降级。
- **隐私边界风险**: 当前 `supplement.char_count` 统计原始输入长度，`accepted_char_count` 统计服务端截断后长度；这是预期脱敏元数据，但线上日志/任务审计仍应保持只记录这些元数据，不记录原文。

## 建议动作

可进入主 Agent 的开发→E2E 交付闭环。上线前使用真实 Provider 做一次脱敏抽样（确认日志、任务状态和响应正文均不包含原文），并复核部署环境的 8080 healthz。

## Handoff

- `status`: ready
- `summary`: 训练向导补充窗口、提交/跳过动作、同次 Prompt 传递、AI 降级和草稿脱敏均通过验收。
- `facts`: 93 个定向测试通过；healthz 返回 `status=ok`；隔离持久化检查确认原文和 `additional_context` 字段未落盘。
- `assumptions`: 真实 Provider 采纳行为与生产日志脱敏未执行线上验收。
- `artifacts`: [本报告](2026-08-10-training-draft-additional-context-e2e.md)；测试日志 `/private/tmp/rundown-additional-context-pytest.log`。
- `decisions_needed`: 无；上线前是否安排真实 Provider 脱敏抽样由主 Agent 决定。
- `next_agent`: 主 Agent
- `handoff_packet`: 目标为验收草稿生成前可选补充信息；约束为最多 280 字、同一次 `draft-training-scheme` 请求、文本作为未验证自述、空值/跳过/AI 不可用不阻断、持久化只保留脱敏元数据、不得新增页面/设置或第二次 AI 调用；文件范围为训练服务/规划、训练模板、草稿 Prompt、相关测试和文档；验证命令为定向 pytest 93 passed、临时服务 healthz curl 和隔离 `TrainingService.create_draft` 脱敏检查；剩余风险为真实 Provider 与生产日志抽样未验证。
