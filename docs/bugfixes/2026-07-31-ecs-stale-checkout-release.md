# Bug: ECS 将旧 Git checkout 误报为成功发布

- **发现日期**: 2026-07-31
- **修复日期**: 2026-07-31
- **严重程度**: major
- **影响范围**: ECS 原生发布、release 切换、发布健康检查

## 现象

远端 `feature/huawei` 已包含目标提交 `21c0797`，但平台暂存目录仍停留在 `47cade3`。发布脚本将
该旧 checkout 复制为新 release、重启成功并通过 `/healthz`，导致部署任务显示成功，而线上界面
仍缺少目标提交中的样式修复。另有 Coros 凭据加密密钥缺失问题同时存在，增加了误判难度。

## 根因

发布脚本只检查暂存目录是否存在 `pyproject.toml`，没有接收和校验本次发布期望的 Git Commit；
`/healthz` 也只返回存活状态，不包含运行 release 身份。因此，完整但过期的暂存仓库与旧进程响应
都能满足原有成功条件。脚本同时缺少发布互斥锁，并允许默认源码路径命中残留暂存目录。

## 修复方案

- 控制台入口刷新目标 `DEPLOY_REF`，把 `FETCH_HEAD` 的完整 SHA 作为 `EXPECTED_COMMIT` 显式传入。
- 发布脚本要求显式 `SOURCE_DIR` 与 `EXPECTED_COMMIT`，校验干净 Git 工作树及 `HEAD` 完全一致。
- 每个 release 写入 Commit 标记，通过 systemd 注入 `NEURUN_RELEASE_SHA`；`/healthz` 返回运行 SHA，
  发布健康检查必须同时满足存活与版本一致。
- 使用非阻塞文件锁拒绝并发发布；缺少 Coros 加密密钥时输出告警但不自动生成或轮换。

## 相关文件

- [scripts/deploy-ecs.sh](../../scripts/deploy-ecs.sh) — 增加来源、版本、互斥和能力告警门禁
- [src/web.py](../../src/web.py) — 健康响应增加运行 release SHA
- [tests/test_packaging.py](../../tests/test_packaging.py) — 覆盖旧提交和并发发布拒绝
- [tests/test_web.py](../../tests/test_web.py) — 固化健康响应版本合同
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 回写不可变发布验收规则
- [README.md](../../README.md) — 更新阿里云控制台启动脚本

## 验证

- 旧 checkout 与 `EXPECTED_COMMIT` 不一致时，在任何 systemd 操作前返回非零。
- 并发发布无法取得锁时快速失败，当前 release 与服务不变。
- 健康响应携带 `NEURUN_RELEASE_SHA`，新版本成功条件要求返回值与候选 Commit 一致。
- 完整测试结果见本次提交交付记录。
