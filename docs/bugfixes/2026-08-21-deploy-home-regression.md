# Bug: systemd HOME 指向 /home/neurun 导致部署启动崩溃

- **发现日期**: 2026-08-20
- **修复日期**: 2026-08-21
- **严重程度**: critical
- **影响范围**: ECS 部署流程（`scripts/deploy-ecs.sh` 生成的 systemd unit）、Web 启动

## 现象

发布 `ec79982` 时，新版本进程启动约 50 秒后崩溃：

```text
PermissionError: [Errno 13] Permission denied: '/home/neurun'
src.local_files.LocalPersistenceError: 无法创建或收紧私有目录 /home/neurun/.neurun
```

8080 从未监听，部署脚本健康检查（60 秒窗口）失败；回滚到旧版本 `5a6a228` 后
健康检查同样失败，服务在 `Restart=on-failure` 下反复崩溃重启。

## 根因

`e06d298`（配合 `ec79982` 发布的部署脚本改动）在 systemd unit 中新增
`Environment=HOME=/home/neurun`。但部署脚本创建运行用户时使用的是
`useradd --system --home-dir /var/lib/neurun`，即 neurun 用户的真实 home 是
`/var/lib/neurun`，`/home/neurun` 不存在且 neurun 无权在 `/home` 下创建。

应用 `db_path` 默认值为 `Path.home() / ".neurun" / "data.db"`、Garmin token 目录
默认值为 `~/.garmy`，都基于 HOME 解析。HOME 被改成 `/home/neurun` 后，
`create_web_server` 初始化 `Storage(config)` 时尝试创建 `/home/neurun/.neurun`，
直接 `PermissionError`，进程退出。

回滚路径 `restore_previous_release` 用同一份新模板重写 unit 文件再重启，
因此旧版本代码也带上了 `HOME=/home/neurun`，同样崩溃——"旧版本已恢复，
但健康检查仍未通过"并非旧版本本身损坏，而是回滚带回了错误的 unit。

## 修复方案

把 unit 模板中的 `Environment=HOME=/home/${RUN_USER}` 改为
`Environment=HOME=${DATA_DIR}`（即 `/var/lib/neurun`），与
`useradd --home-dir ${DATA_DIR}` 保持一致；Playwright 浏览器已由
`PLAYWRIGHT_BROWSERS_PATH=${BROWSERS_DIR}` 独立指定，不依赖 HOME。

## 相关文件

- [scripts/deploy-ecs.sh](../scripts/deploy-ecs.sh) — unit 模板 `HOME=${DATA_DIR}` 并注释原因
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 部署设计同步 HOME 语义
- [tests/test_packaging.py](../tests/test_packaging.py) — 断言 unit 使用 `HOME=${DATA_DIR}` 且不含 `/home/`

## 验证

- `bash -n scripts/deploy-ecs.sh` 通过；`tests/test_packaging.py` 新增断言通过
- 线上 ECS 先手工把 unit 的 HOME 改回 `/var/lib/neurun` 并 `systemctl daemon-reload && systemctl restart neurun.service` 恢复服务
- 重新发布修复后的提交，观察 `GET /healthz` 返回新 release SHA，启动日志 `DB:` 显示 `/var/lib/neurun/.neurun/data.db`
