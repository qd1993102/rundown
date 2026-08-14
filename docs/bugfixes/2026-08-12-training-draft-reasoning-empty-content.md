# Bug: 推理模型流式空 content 导致训练草稿 AI 调用必现失败

- **发现日期**: 2026-08-11
- **修复日期**: 2026-08-12
- **严重程度**: critical
- **影响范围**: 训练方案草稿（training_framework / near_term_schedule 两阶段 AI 调用）、Coach Skill 运行时、Web 草稿生成

## 现象

`scripts/ai_draft_stats.py` 与 Web 草稿生成在 `build-training-framework` 阶段反复失败。runner 日志显示：
`phase=complete_json status=200` 后立即 `AI output rejected: reason=json_decode content_bytes=0`，
即 HTTP 200 返回的 envelope 中 `message.content` 为空串，`json.loads("")` 抛 `JSONDecodeError`，
草稿中止并提示"Skill 模型未返回有效 JSON"。本会话内连续 7 次真实调用全部失败。

## 根因

两层原因叠加：

1. **完成判定缺陷（代码层）**：`src/coach_runtime/runner.py` 的 `_has_complete_choice_content`
   只检查 `choices[0].message.content` 是否为 `str`。推理类模型（`deepseek-v4-flash-0731`）
   流式输出期间先发送 `content=""`、仅含 `reasoning_content` 的中间态 envelope，
   空串也是 `str`，被误判为"完整响应"并提前 break，随后 `json.loads("")` 失败。

2. **Provider 间歇性空 content（网关层）**：krill-ai 网关对草稿级大请求（约 4K prompt tokens +
   2.6K+ payload tokens、max_tokens=2200）**约 60% 概率**返回 HTTP 200 但 `content=""` 的完整
   envelope（仅含大量 `reasoning_content`），或直接断连（`RemoteProtocolError: Server disconnected`）。
   同一请求逐字节比对完全一致（prompt 2031 字符、payload 9715 字符），直接 httpx 重放却可成功，
   确认是网关/模型端的概率性故障而非请求构造问题。

## 修复方案

1. **严格化完成判定**：`_has_complete_choice_content` 现在要求 `content` 非空且本身是合法 JSON
   对象（`isinstance(json.loads(content), dict)`），推理中间态的空串 envelope 不再被当作完成。
2. **空/非法内容自动重试**：`OpenAICompatibleSkillModel.generate` 对 `json.JSONDecodeError`
   （content 为空或非法 JSON）自动重试一次；超时、HTTP 400/403 等请求级错误不重试。
   重试在总响应预算（默认 300s）内进行，每次尝试独立计时。
3. **配置层规避**：推理模型（krill-ai `deepseek-v4-flash-0731`、官方 `deepseek-v4-pro`）
   在草稿级任务上要么间歇性返回空 content，要么 reasoning 吃光 `max_tokens` 导致
   `content=""`（实测 `finish_reason=length`、`completion_tokens=2200` 全为
   `reasoning_tokens`）。最终切换为 **DeepSeek 官方 API**（`api.deepseek.com` +
   `deepseek-chat` 非推理模型）：无 reasoning_content，草稿两阶段完整成功。
4. **适配官方 API 的流式时序**：官方 API 先返回 headers、随后静默推理数十秒再一次性发送
   body，`read_timeout` 由 35s 提至 120s（build-training-framework），避免 httpx
   ReadTimeout 误报“AI 服务响应超时”。
5. **放宽草稿长输出上限**：`max_tokens` 对 `build-training-framework` /
   `build-near-term-schedule` 由 2200/1800 统一提至 4000，避免近期课表长输出被
   `finish_reason=length` 截断成不完整 JSON。

## 相关文件

- [src/coach_runtime/runner.py](../../src/coach_runtime/runner.py) — 完成判定严格化、空内容重试、
  read_timeout 120s、草稿两阶段 max_tokens 4000
- [tests/test_coach_runtime.py](../../tests/test_coach_runtime.py) — 新增 4 个测试：
  完成判定纯函数、空内容不提前 break、重试一次成功、连续两次失败；同步 max_tokens 断言
- [.env](../../.env) — `NEURUN_AI_BASE_URL=https://api.deepseek.com`、`NEURUN_AI_MODEL=deepseek-chat`、官方 sk- key
- [docs/design/ai-coaching.md](../design/ai-coaching.md) — 补充完成判定与重试策略

## 验证

- `pytest tests/test_coach_runtime.py` 20/20 通过；临时还原旧判定实现后新增测试 2/2 失败（确认测试有效）。
- 完整 `pytest` 417 通过、3 失败（均为预存在的环境相关失败：Coros 凭据断言、umask 目录权限、
  Web 训练流程测试 0.5s 轮询窗口短于真实 AI 耗时，与本次改动无关）。
- 真实草稿端到端（DeepSeek 官方 deepseek-chat）：`generation_mode=skill`，总耗时 26.9s，
  `training_framework`（12.4s，4618B）与 `near_term_schedule`（13.6s，5977B）
  两次 AI 调用均返回合法 JSON，plan_id=`230-838ab23b`。
