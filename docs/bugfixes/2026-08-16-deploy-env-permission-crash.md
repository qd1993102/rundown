# Bug: 部署环境文件权限导致 Web 服务启动崩溃（PermissionError）

- **发现日期**: 2026-08-16
- **修复日期**: 2026-08-16
- **严重程度**: critical
- **影响范围**: Web 服务启动（`cmd_serve` → `get_config`）、CLI 管理员命令（`invite` 等）

## 现象

发布 e0e4925（含 5744729 部署环境文件自动读取 + 配置解析诊断日志）到 ECS 后，
systemd 启动 neurun.service 立即崩溃，健康检查失败自动回滚到旧 release：

```
PermissionError: [Errno 13] Permission denied: '/etc/neurun/neurun.env'
  File ".../src/config.py", line 369, in get_config
    _DEPLOY_ENV_FILE.exists(),
  File ".../pathlib.py", line 860, in exists
    self.stat(follow_symlinks=follow_symlinks)
  File ".../pathlib.py", line 840, in stat
    return os.stat(self, follow_symlinks=follow_symlinks)
PermissionError: [Errno 13] Permission denied: '/etc/neurun/neurun.env'
```

## 根因

1. 部署脚本 `scripts/deploy-ecs.sh` 以 root 创建 `/etc/neurun`（0750 root:root）
   与 `neurun.env`（0640 root:root）；Web/CLI 进程以 `User=neurun Group=neurun`
   运行，连 stat 目录都被拒（EACCES）。
2. Python 3.12 的 `Path.exists()` 只吞掉 FileNotFoundError / NotADirectoryError，
   **对 PermissionError(EACCES) 直接传播**（3.13+ 才吞掉 OSError），因此
   `get_config()` 里对部署环境文件的 `.exists()` 检查直接抛异常，服务起不来。
3. 该隐患自 5744729（自动读取部署环境文件）就存在，但此前从未在 ECS 上运行过
   ——首次发布即触发。

## 修复方案

1. `src/config.py` — 新增 `_deploy_env_status()`：对部署环境文件的 exists /
   可读性检查全部 try/except OSError，返回状态描述（不存在 / 可读 / 不可读 /
   访问异常），绝不抛出；`get_config()` 只在「可读」时 `load_dotenv`，不可用时
   warning 并跳过（Web 仍由 systemd `EnvironmentFile` 注入，不受影响）；
   诊断日志 `配置探测[部署环境文件]` 输出该状态。
2. `scripts/deploy-ecs.sh` — 目录与文件属组改为 `root:运行组`（0750/0640），
   保证 neurun 进程可读；对历史遗留的 root:root 属主做幂等 chown；属主修改
   仅在 root 下执行（本地非 root 测试跳过）。
3. 运维：修复后重新发布即可（部署脚本会自动修正 `/etc/neurun` 属主）。

## 相关文件

- [src/config.py](src/config.py) — `_deploy_env_status()` 容错 + 诊断日志状态
- [scripts/deploy-ecs.sh](scripts/deploy-ecs.sh) — 目录/文件属组 root:运行组
- [tests/test_config.py](tests/test_config.py) — 不可读部署环境文件不崩溃用例

## 验证

- 本地：monkeypatch `Path.exists` 抛 PermissionError（模拟 Python 3.12 ECS
  行为），`get_config()` 不崩溃、回退默认路径、日志输出「访问异常」与
  「部署环境文件不可用」；
- `pytest` 551 通过（含新增用例）；
- ECS：重新发布后 systemd 启动正常，`/etc/neurun/neurun.env` 属主
  `root:neurun`，`sudo -u neurun ... invite create` 自动读取并写入
  `/var/lib/neurun/invite-codes.json`。
