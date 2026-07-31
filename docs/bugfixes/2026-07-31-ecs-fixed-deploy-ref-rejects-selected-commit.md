# Bug: ECS 固定分支校验拒绝平台选定的新提交

- **发现日期**: 2026-07-31
- **修复日期**: 2026-07-31
- **严重程度**: major
- **影响范围**: 阿里云 ECS 原生发布入口、候选 Commit 校验

## 现象

发布平台已经 checkout 用户选定的新提交 `3e4d027`，但控制台启动入口固定刷新
`feature/huawei`，并把该分支的旧提交 `21c0797` 作为 `EXPECTED_COMMIT`。由于新提交已经包含
旧提交，`git merge --ff-only` 返回 `Already up to date`，随后的一致性校验却因两个 SHA 不相等而拒绝发布。

## 根因

控制台入口把平台已经完成的 revision 选择与服务器上的二次分支选择混在一起。固定
`DEPLOY_REF=feature/huawei` 既可以与发布任务选中的提交不同，也为每次发布增加一次不必要的 GitHub 访问。

## 修复方案

- 发布任务继续负责选择目标 revision，包括控制台的“最新提交”选项。
- 启动入口直接读取平台已 checkout 的完整 `HEAD` SHA，作为 `EXPECTED_COMMIT` 传给原有发布脚本。
- 移除入口中固定的 `DEPLOY_REF`、额外 `git fetch` 和 `git merge`，保留干净工作树、完整 SHA、并发锁及发布后健康检查。

## 相关文件

- [README.md](../../README.md) — 控制台启动入口改为发布平台 checkout 的 `HEAD`
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 明确 revision 选择与 SHA 校验的责任边界
- [tests/test_packaging.py](../../tests/test_packaging.py) — 防止控制台入口重新引入固定分支

## 验证

- 控制台入口中不再出现 `DEPLOY_REF`、`git fetch` 或 `git merge`。
- 平台 checkout `3e4d0278961768a2e986dd1b3b29decc7b7295e2` 时，入口传入的 `EXPECTED_COMMIT` 与实际 `HEAD` 完全一致。
- 原有发布脚本仍在候选构建、软链切换和 systemd 操作前校验完整 SHA 与干净工作树。
