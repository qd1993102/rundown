# Bug: 刷新后只提示 AI 任务占用但无法查看进度

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 周复盘生成、在线 AI singleflight、报告页刷新恢复

## 现象

用户生成周复盘后刷新页面，再次点击只看到“你已有 AI 生成任务进行中”，无法知道任务是在等待、执行、完成还是失败。当前周进度不会归档，后台即使成功，刷新后的页面也无法恢复结果。

## 根因

AI 门禁只维护内部用户占位和执行计数，没有面向当前用户的任务状态读模型。请求刷新取消后，受保护的后台线程继续运行，但任务阶段和结果没有 API；周中进度的返回值只存在于已取消的原 POST 响应中。

## 修复方案

为每个已接纳任务记录用户隔离的短期状态和真实时间点，提供当前用户只读状态接口。报告页刷新后轮询等待/执行状态，完成后恢复周中结果或正式归档，失败后允许重试；不伪造模型百分比。状态只在内存保留 5 分钟，不包含凭证、Prompt 或运行时资源对象。

## 相关文件

- [src/ai_inference_coordinator.py](../../src/ai_inference_coordinator.py) — 任务状态、取消保护与受限结果缓存
- [src/web.py](../../src/web.py) — 当前用户任务状态 API 与周复盘结果映射
- [web/templates/reports.html](../../web/templates/reports.html) — 刷新恢复、状态展示和轮询
- [docs/design/ai-coaching.md](../design/ai-coaching.md) — 并发与任务状态合同
- [docs/design/training-system.md](../design/training-system.md) — Web API 和客户端交互

## 验证

单元测试覆盖等待、执行、完成、失败、调用方取消和终态过期；Web 测试覆盖用户隔离状态 API、周复盘结果恢复及报告页面轮询标记；全量 `pytest` 零失败。
