# Bug: 本地敏感数据写入依赖进程 umask 和文件所有者

- **发现日期**: 2026-07-26
- **修复日期**: 2026-07-26
- **严重程度**: major
- **影响范围**: Web 用户、邀请码、平台凭据、SQLite、备份、记忆、配置与数据导出

## 现象

管理员以 root 单独运行 CLI 后，可能在数据目录生成 root 所有的文件，systemd 中的
`neurun` 用户随后写入时报 `Permission denied`。部分目录和文件只依赖系统 umask，可能
分别以 `0755`、`0644` 创建，扩大账号、健康数据和平台凭据的本机读取范围。

## 根因

各模块分别使用 `mkdir()`、`write_text()`、普通 `open()` 或覆盖写，没有统一规定目录、
文件权限与原子替换行为。错误信息也没有稳定包含失败路径和服务用户归属修复建议。

## 修复方案

- 新增统一的本地私有写入工具：应用数据目录固定 `0700`，敏感文件固定 `0600`。
- JSON、Markdown、环境文件和 CSV/JSON 导出使用同目录临时文件、`fsync` 与 `os.replace`
  原子替换；显式导出不修改用户选定目录的权限，只收紧输出文件。
- SQLite、备份和 PNG 在底层生成后立即收紧为 `0600`。
- 目录不可写时返回实际路径，并提示用 `chown -R <服务用户>:<服务组>` 修复所有者。
- README 明确 ECS 管理命令必须以 systemd 服务用户运行，并给出既有 root 文件修复命令。

## 相关文件

- [src/local_files.py](../../src/local_files.py) — 私有目录、文件和原子写入公共实现
- [src/users.py](../../src/users.py) — 用户记录和目录权限
- [src/storage.py](../../src/storage.py) — SQLite、备份及导出权限
- [src/memory.py](../../src/memory.py) — Markdown 记忆原子写入
- [src/invitations.py](../../src/invitations.py) — 邀请码原子写入
- [docs/design/04-modules.md](../design/04-modules.md) — 本地持久化职责与权限边界
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — ECS 运行用户与数据目录约束

## 验证

- 测试确认用户目录、Token、memory、backup 和 SQLite 目录均为 `0700`。
- 测试确认用户 JSON、邀请文件、Huawei 凭据、SQLite、备份、Markdown、CSV/JSON 与 HTML
  文件均为 `0600`。
- 先将既有文件改为只读，再保存更新，确认原子替换成功且权限恢复为 `0600`。
- 完整执行 `pytest`，确认零失败。
