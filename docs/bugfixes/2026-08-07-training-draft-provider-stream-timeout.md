# Bug: 完整草稿结果被 Provider 连接收尾误判为超时

- **发现日期**: 2026-08-07
- **修复日期**: 2026-08-07
- **严重程度**: major
- **影响范围**: `draft-training-scheme`、`revise-training-scheme` 的 OpenAI-compatible Provider 调用

## 现象

训练草稿或重新调整方案时，Provider 已返回 HTTP 200，但请求仍持续到原 90 秒总时限，训练页提示“AI 服务响应超时”并返回重试。

## 根因

模型适配层把读取到 HTTP 响应流结束当成成功的必要条件。某些 Provider 或代理会在完整 JSON 已到达后延迟关闭连接，导致可用结果在本地等待连接收尾期间被总 deadline 覆盖。

## 修复方案

草稿/重算的紧凑输出上限设为 1800 tokens，总 deadline 放宽为 300 秒（5 分钟）。读取期间一旦获得完整、可解析的 Provider JSON，立即停止读取并关闭响应上下文，再继续既有的 Skill Schema、Normalizer 与 Validator 校验。保留非 200 响应处理、45 秒连续无数据读取上限和 4 MiB 上限；日志只记录状态、字节数和耗时。

## 相关文件

- [src/coach_runtime/runner.py](../../src/coach_runtime/runner.py) — 提前完成已完整的 Provider JSON 响应。
- [tests/test_coach_runtime.py](../../tests/test_coach_runtime.py) — 覆盖不消费滞留尾部字节的回归测试。
- [docs/design/training-system.md](../design/training-system.md) — 同步模型适配层的响应完成合同。

## 验证

使用模拟 Provider 响应：第一个字节块已包含完整 JSON，后续字节块会失败；模型适配层必须在第一个块后返回并保持既有超时测试有效。
