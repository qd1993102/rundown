# Bug: ECS Git 下载失败会破坏在线应用目录

- **发现日期**: 2026-07-28
- **修复日期**: 2026-07-28
- **严重程度**: major
- **影响范围**: 阿里云 ECS Git 部署、systemd Web 服务、版本回滚

## 现象

原启动脚本将平台自动下载的
`/opt/neurun-deploy/code_deploy_application` 同时作为部署暂存区和 systemd 在线应用目录。
当 Git 未拉取成功或平台重建工作目录时，当前进程可能短暂继续运行，但在下次重启后会因源码或
可编辑安装路径消失而无法启动。

## 根因

发布流程没有区分“平台下载暂存区”和“在线不可变 release”。systemd 的 `WorkingDirectory` 及共享
虚拟环境中的可编辑安装都直接指向会被下一次部署管理的工作目录，因此下载失败时没有独立的旧版本
可继续运行。

## 修复方案

- 新增 `scripts/deploy-ecs.sh`，先检查下载暂存区的 `pyproject.toml`；检查失败时在任何 systemd
  操作前退出。
- 每次发布复制到 `/opt/neurun-releases/<release-id>`，并在 release 内创建独立 `.venv`；依赖安装和
  入口导入检查不再修改当前版本。
- 新 release 完全就绪后才原子切换 `/opt/neurun-current` 并重启。新版本健康检查失败时，
  恢复旧软链接并重启旧 release。
- 控制台入口先通过绝对路径验证 `pyproject.toml` 和发布脚本，不在验证前执行
  `cd ./code_deploy_application`；候选构建阶段发生任何未处理错误时输出“当前 release、软链接
  和在线服务均未修改”。
- 不再复用指向 Git 暂存区的 `/opt/neurun-venv`，也不删除旧 release。

## 相关文件

- [scripts/deploy-ecs.sh](../../scripts/deploy-ecs.sh) — 新增不可变 release 发布、延迟重启和健康检查回滚
- [tests/test_packaging.py](../../tests/test_packaging.py) — 固化 Git 目录缺失时不调用 systemd 的回归契约
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 同步 ECS 发布目录职责和切换顺序
- [README.md](../../README.md) — 记录原生部署脚本与运行目录

## 验证

- 运行 `bash -n scripts/deploy-ecs.sh`，脚本语法检查通过。
- 在临时工作目录中不创建 `code_deploy_application/pyproject.toml`，执行脚本后返回非零，
  fake `systemctl` 日志保持不存在。
- 模拟候选 release 的依赖安装失败，确认 `/opt/neurun-current` 仍指向旧 release、旧文件仍
  存在且 fake `systemctl` 没有收到任何命令。
- 打包契约测试确认 systemd 只指向 `/opt/neurun-current`，而不是 Git 下载暂存区。
- 运行完整 `pytest`，确认零失败。
