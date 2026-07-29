# ECS 不可变 Release 安全部署

- **日期**: 2026-07-29
- **类型**: architecture

## 背景与动机

阿里云部署平台把 Git 代码下载到 `code_deploy_application`。旧启动脚本同时把该目录作为
systemd 在线工作目录，并复用共享虚拟环境；Git 下载失败、暂存区重建或候选依赖安装失败
都可能让旧服务在重启后无法恢复。

## 方案选择

采用不可变 release 和原子软链接，而不是继续在 Git 暂存目录原地安装。暂存区只作为输入，
每个 release 拥有独立虚拟环境；相比备份后覆盖，这一方案能在构建阶段完全不接触在线版本，
也能在新版本健康检查失败时快速恢复旧软链接。

## 实现步骤

1. 控制台入口先验证 Git 下载结果和仓库内发布脚本，不提前 `cd` 或操作 systemd。
2. 将候选源码复制到新的 `/opt/neurun-releases/<release-id>`。
3. 在候选 release 内创建虚拟环境、安装依赖并验证服务入口可导入。
4. 候选完全就绪后原子切换 `/opt/neurun-current` 并重启。
5. 新版本健康检查失败时恢复旧软链接和旧服务。
6. 用 fake systemctl 覆盖 Git 缺失和候选依赖失败两个“不得触碰在线服务”的回归场景。

## 遇到的问题与解决

- Git 下载失败时仓库内脚本本身也不存在：控制台保留一段最小入口，只检查绝对路径并在
  缺失时退出。
- 平台可能在脚本前主动停止应用：仓库脚本无法撤销平台的前置生命周期动作，必须同时关闭
  控制台中的“部署前停止应用”配置。
- 首次采用 release 方案时可能没有旧软链接：候选构建仍不影响正在运行的旧进程，但只有
  完成一次成功发布后才能获得自动软链接回滚能力。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md)
- Bugfix: [docs/bugfixes/2026-07-28-ecs-deploy-preserve-current-release.md](../bugfixes/2026-07-28-ecs-deploy-preserve-current-release.md)
