# Bug: 周复盘在线 AI 任务长期卡在生成中

- **发现日期**: 2026-08-04
- **修复日期**: 2026-08-04
- **严重程度**: major
- **影响范围**: 周复盘生成、账户级 AI 任务准入、方案调整等同账户后续操作

## 现象

`qd1993102@gmail.com` 在本周已有生效训练方案时生成周复盘，页面持续显示“周复盘正在生成”，
超过五分钟仍不结束。同一账户随后提交方案重规划会收到 `429 ai_request_in_progress`。
服务进程保持到 AI 上游的连接，前端轮询和账户级任务状态均正常，说明实际工作线程未返回。

## 根因

有方案周复盘会按顺序调用 `evaluate-plan-execution`、`assess-recovery-readiness` 和
`review-training-week` 三个模型请求，最坏耗时叠加。模型客户端只配置 `httpx` 的逐次 I/O
timeout，没有端到端响应预算；只要上游持续缓慢返回字节，单次读取超时就可能一直不触发。
工作线程不结束时，`AIInferenceCoordinator` 无法进入终态或释放该账户的 singleflight 占位。

## 修复方案

- 计划执行、恢复风险、趋势和四段周复盘先由本地逻辑完整计算；
- 有方案周复盘也只调用一次主 `review-training-week`，不再串行运行两个贡献 Skill；
- OpenAI-compatible 客户端改为分块读取，对周复盘使用 25 秒总响应预算和 20 秒读取上限；
- 上游超时、慢速响应或输出无效时关闭连接并使用现有确定性复盘，最迟约 45 秒释放账户任务；
- 超时错误保持脱敏，不记录请求正文、训练事实、凭证或上游响应正文。

## 相关文件

- [src/training.py](../../src/training.py) — 合并本地周事实并收敛为一次主 Skill 调用
- [src/coach_runtime/runner.py](../../src/coach_runtime/runner.py) — 增加分块读取和总响应预算
- [docs/product/daily-report.md](../product/daily-report.md) — 明确周复盘生成时限与兜底体验
- [docs/design/training-system.md](../design/training-system.md) — 记录单次调用和超时合同

## 验证

- 单元测试证明有方案周复盘只调用一次主 Skill；
- 模型适配层测试模拟超过总预算的慢速响应，确认连接退出并返回安全超时；
- 完整 `pytest` 零失败；
- 重启 8080 后使用复现账户重新生成周复盘，确认任务在预算内完成或兜底。
