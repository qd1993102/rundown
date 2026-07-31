# Bug: ECS 旧启动入口传入的预期 SHA 阻断新提交发布

- **发现日期**: 2026-07-31
- **修复日期**: 2026-07-31
- **严重程度**: major
- **影响范围**: 阿里云 ECS 原生发布入口、候选 Commit 校验、单机发布可用性

## 现象

发布平台已 checkout 新提交 `397ab88ccd379aaed339ddb790e2d6401d6629ef`，但 ECS 控制台仍在执行旧启动
入口：额外刷新 `feature/huawei`，并把 `21c07970e0c0bed03802bbd663ee13a17b8e6bcc` 作为
`EXPECTED_COMMIT` 传入。发布脚本因两个 SHA 不一致返回 1，导致平台报 `InvalidApiResponse`。

## 根因

仓库文档已移除固定分支，但 ECS 控制台中保存的生命周期脚本不会随 Git 提交自动更新。
`deploy-ecs.sh` 仍把外部 `EXPECTED_COMMIT` 当作强制门禁，因此一份过期的控制台入口仍能拒绝平台本次已经
checkout 的清晰候选版本。

## 修复方案

- `deploy-ecs.sh` 不再读取、校验或依赖 `EXPECTED_COMMIT`，只使用平台 checkout 的完整 `HEAD` SHA。
- 旧控制台入口即使继续传入错误 `EXPECTED_COMMIT`，发布脚本也会忽略它，确保新代码能先发布。
- README 的最终入口同时移除额外分支刷新与 `EXPECTED_COMMIT`，避免多一次 GitHub 网络依赖。
- 保留完整 40 位 `HEAD` SHA、干净工作树、非阻塞发布锁、候选构建、健康检查和失败回滚。

## 相关文件

- [scripts/deploy-ecs.sh](../../scripts/deploy-ecs.sh) — 移除外部预期 SHA 门禁，校验平台 checkout `HEAD`
- [tests/test_packaging.py](../../tests/test_packaging.py) — 复现并固化旧环境变量不得阻断发布
- [README.md](../../README.md) — 精简 ECS 控制台启动脚本
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 同步 revision 选择与版本校验责任边界

## 验证

- 复现测试传入与实际 `HEAD` 不同的旧 `EXPECTED_COMMIT`，发布流程可继续到并发锁门禁，不再返回候选提交不一致。
- 打包契约测试确认发布脚本和 README 入口都不包含 `EXPECTED_COMMIT`。
- 候选依赖安装失败时仍不切换软链或操作 systemd，旧 release 继续运行。
