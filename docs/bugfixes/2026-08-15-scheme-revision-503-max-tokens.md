# Bug: 方案重规划接口 503（revise-training-scheme 输出被 max_tokens 截断）

- **发现日期**: 2026-08-15
- **修复日期**: 2026-08-15
- **严重程度**: major
- **影响范围**: 训练页"调整目标或重规划"→ `POST /api/training/scheme-revisions`（`revise-training-scheme` Skill）

## 现象

点击"重规划差异"提交方案重规划（如"10.18 不比赛了"）后，接口返回 503：

```json
{"status":"error","code":"scheme_candidate_unavailable","message":"方案重规划失败，当前方案保持不变；请手动重试"}
```

日志显示 `revise-training-scheme` 输出连续两次 `json_decode` 失败：

```
AI output rejected: skill=revise-training-scheme reason=json_decode content_bytes=6367 attempt=2/2
专业重规划 Skill 不可用: Skill 模型未返回有效 JSON
```

## 根因

`OpenAICompatibleSkillModel.generate` 的 `max_tokens` 只对两个草稿技能
（`build-training-framework` / `build-near-term-schedule`）放宽到 4000，
`revise-training-scheme` 走默认 **1800**。但方案重规划输出完整候选
（阶段周期 + 首四周课表 + 逐段处方），实测输出 7.4KB ≈ **2000+ tokens**，
**超过 1800 被 `finish_reason=length` 截断**，JSON 不完整 → `json_decode` 失败 →
`TrainingSchemeCandidateUnavailable` → 503。载荷还包含 active_scheme 全文
（45K tokens），长上下文加剧输出不稳定。

## 修复方案

- `src/coach_runtime/runner.py`：把 `revise-training-scheme` 加入 4000 `max_tokens`
  放宽名单（与框架/课表两阶段一致，避免截断 JSON）；
- 503 本身是产品设计行为（重规划失败保留当前方案 + 手动重试，不走确定性兜底），
  修复后不再触发。

## 相关文件

- [src/coach_runtime/runner.py](../../src/coach_runtime/runner.py) — max_tokens 名单
- [tests/test_coach_runtime.py](../../tests/test_coach_runtime.py) — 断言 revise 技能 4000

## 验证

- 修复前：同一请求 503（`json_decode` 失败 ×2）；
- 修复后：同一请求 **HTTP 201**，候选正常生成；
- pytest `test_coach_runtime.py` 21 项通过。
