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
2. `src/config.py` — `get_config()` 自动读取部署环境文件 `/etc/neurun/neurun.env`
   （ECS systemd 部署的持久化环境文件，只补缺失项、不覆盖系统环境变量）：
   CLI 管理员命令与 Web（systemd `EnvironmentFile` 同源）共享同一配置源，
   ECS 上执行 `neurun invite create` **无需手工传 env** 即与 Web 读写同一文件。
3. `scripts/deploy-ecs.sh` — 发布时把 `NEURUN_DATA_DIR` / `NEURUN_INVITE_CODES_FILE`
   幂等写入部署环境文件（已存在则跳过），保证新旧发布都指向持久化数据目录。
4. `src/main.py` — `invite create --output json` 时在 **stderr** 打印
   `已写入文件: <路径>`（stdout JSON 结构不变，AI/MCP 消费不受影响），
   table 模式直接打印，避免生成后无法确认写入位置。
5. 运维侧约定（写入 README）：ECS 上生成邀请码必须使用
   `/opt/neurun-current/.venv/bin/neurun` 绝对路径（不用 PATH 里的旧 `neurun`），
   不要从暂存区等目录执行；发布新版本（含本修复）后生效。

## 相关文件

- [src/config.py](src/config.py) — `invite_codes_path` 兜底固定项目根；读取部署环境文件；
  配置解析诊断日志（`配置探测[原始环境]` / `配置探测[部署环境文件]` / `配置探测[解析结果]`）
- [src/main.py](src/main.py) — `_invite_output` 支持输出写入路径
- [scripts/deploy-ecs.sh](scripts/deploy-ecs.sh) — 发布时写入部署环境文件路径变量
- [tests/test_config.py](tests/test_config.py) — 相对 data_dir 固定项目根 / 部署环境文件测试
- [tests/test_main.py](tests/test_main.py) — `invite create` 输出写入路径测试
- [README.md](README.md) — 邀请码路径固定规则与 ECS 生成命令

## 验证

- 本地：无任何 env 时 `Config().invite_codes_path` 固定为项目根
  `data/invite-codes.json`，与 cwd 无关；
- 本地：`invite create --output json` 在 stderr 打印写入路径，文件确实落盘；
- `pytest tests/test_config.py tests/test_main.py` 全部通过；
- ECS：发布新版本后，用 `/opt/neurun-current/.venv/bin/neurun` + 显式 env 生成，
  stderr 应显示 `已写入文件: /var/lib/neurun/invite-codes.json`，用该码注册成功。

## 再发现（2026-08-16）：手工传 env 被吞，仍落盘 release/data

### 现象

ECS 上执行

```bash
sudo -u neurun env NEURUN_DATA_DIR=/var/lib/neurun NEURUN_INVITE_CODES_FILE=/var/lib/neurun/invite-codes.json /opt/neurun-current/.venv/bin/neurun invite create -n 1 --output json
```

stderr 仍显示 `已写入文件: /opt/neurun-releases/<id>/data/invite-codes.json`。

### 定位

本地复现（当前 HEAD 代码）确认两种输入下 `invite_codes_path` 的差异：

| 场景 | `配置探测[原始环境]` | `配置探测[解析结果]` |
|------|---------------------|---------------------|
| 无 env（命令折行/env 被吞） | `NEURUN_DATA_DIR=None NEURUN_INVITE_CODES_FILE=None` | `data_dir='./data'` → `<release>/data/invite-codes.json`（正是线上看到的路径） |
| env 显式传入 | 两个变量均有值 | `/var/lib/neurun/invite-codes.json` |

结论：进程实际没收到 `NEURUN_DATA_DIR` / `NEURUN_INVITE_CODES_FILE`（长命令被终端
折行破坏，与上一条 changelog 描述的失败模式一致），退回兜底路径。

### 加固：配置解析诊断日志

`get_config()` 新增三条 INFO 日志，一次 ECS 运行即可定位丢在哪一环：

1. `配置探测[原始环境]` — 加载任何 .env 之前进程实际收到的 `NEURUN_*` 值
   （None = env 没传进去；有值但最终路径不对 = 被 .env 覆盖）；
2. `配置探测[部署环境文件]` — `/etc/neurun/neurun.env` 是否存在（False = 部署脚本未写入）；
3. `配置探测[解析结果]` — 叠加 .env / 部署环境文件后的最终 `invite_codes_path`。

同时强调运维约定：**不要手工传 env，直接依赖部署环境文件**
（`/etc/neurun/neurun.env` 由 `scripts/deploy-ecs.sh` 发布时写入）：

```bash
sudo -u neurun /opt/neurun-current/.venv/bin/neurun invite create -n 1 --output json
```

若 `/opt/neurun-current` 指向的 release 由旧版 deploy 脚本发布（`/etc/neurun/neurun.env`
不存在），需重新发布含部署环境文件写入逻辑的版本后生效。
