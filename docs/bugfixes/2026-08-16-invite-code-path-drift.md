# Bug: ECS 发布后邀请码读写路径漂移

- **发现日期**: 2026-08-15
- **修复日期**: 2026-08-16
- **严重程度**: critical
- **影响范围**: 邀请码生成（CLI `invite create`）与 Web 注册校验（`/api/register`、`/api/invitations/validate`）

## 现象

ECS（阿里云 CodeDeploy + systemd 原生部署）上执行

```bash
sudo -u neurun env NEURUN_DATA_DIR=/var/lib/neurun NEURUN_INVITE_CODES_FILE=/var/lib/neurun/invite-codes.json neurun invite create -n 30 --output json
```

能正常输出邀请码，但注册时提示「邀请码无效、已停用或已经使用」。

## 根因

`config.invite_codes_path` 的兜底解析为 `<data_dir>/invite-codes.json`，而 `data_dir`
默认值 `"./data"` 是**相对进程 cwd** 的。多因素叠加导致生成与读取位置不一致：

1. PATH 里的 `neurun` 命令指向**旧版本代码**（部署早期残留），不认识
   `NEURUN_DATA_DIR` / `NEURUN_INVITE_CODES_FILE` 环境变量，`env` 传入的变量被无视；
2. 旧代码按默认 `./data` 相对路径写入，位置随执行时 cwd 漂移：
   - 在 `/root/code_deploy_application`（发布暂存区）下执行 → 码写入
     `/root/code_deploy_application/data/invite-codes.json`；
   - 在 `/opt/neurun-current`（软链接 → release 目录）下执行 → 码写入
     `releases/<id>/data/invite-codes.json`；
3. 旧 release 代码的 Web 进程同样按 `./data` 相对自己 `WorkingDirectory` 读取，
   且 ECS 发布切换 release 目录后，旧 release 的 `data/` 不再被读取（目录被替换/清理），
   已生成的邀请码随之“丢失”；
4. `/var/lib/neurun/invite-codes.json`（systemd 注入的 `NEURUN_INVITE_CODES_FILE`
   指向的文件）与旧代码实际读写位置不一致，产生“文件有内容却无效”的现象。

## 修复方案

1. `src/config.py` — `invite_codes_path` 兜底逻辑：相对 `data_dir`（含默认 `./data`）
   固定基于**项目根目录**（`src/` 的上级）解析，不再随进程 cwd 漂移；显式
   `NEURUN_INVITE_CODES_FILE`（推荐 ECS 用绝对路径）优先级不变。
2. `src/main.py` — `invite create --output json` 时在 **stderr** 打印
   `已写入文件: <路径>`（stdout JSON 结构不变，AI/MCP 消费不受影响），
   table 模式直接打印，避免生成后无法确认写入位置。
3. 运维侧约定（写入 README）：ECS 上生成邀请码必须：
   - 使用 `/opt/neurun-current/.venv/bin/neurun` 绝对路径（不用 PATH 里的旧 `neurun`）；
   - 显式传入与 Web 进程相同的 `NEURUN_DATA_DIR` / `NEURUN_INVITE_CODES_FILE`；
   - 从 `/tmp` 等中立目录执行，避免 cwd 干扰；
   - 发布新版本（当前 release 代码为旧版，不支持环境变量）后，systemd 注入的
     `NEURUN_INVITE_CODES_FILE=/var/lib/neurun/invite-codes.json` 才会被新代码生效。

## 相关文件

- [src/config.py](src/config.py) — `invite_codes_path` 兜底固定项目根
- [src/main.py](src/main.py) — `_invite_output` 支持输出写入路径
- [tests/test_config.py](tests/test_config.py) — 相对 data_dir 固定项目根测试
- [tests/test_main.py](tests/test_main.py) — `invite create` 输出写入路径测试
- [README.md](README.md) — 邀请码路径固定规则与 ECS 生成命令

## 验证

- 本地：无任何 env 时 `Config().invite_codes_path` 固定为项目根
  `data/invite-codes.json`，与 cwd 无关；
- 本地：`invite create --output json` 在 stderr 打印写入路径，文件确实落盘；
- `pytest tests/test_config.py tests/test_main.py` 全部通过；
- ECS：发布新版本后，用 `/opt/neurun-current/.venv/bin/neurun` + 显式 env 生成，
  stderr 应显示 `已写入文件: /var/lib/neurun/invite-codes.json`，用该码注册成功。
