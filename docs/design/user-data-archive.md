# 技术设计 — 单用户脱敏 TAR 导出

> 日期: 2026-08-24  
> 状态: Implemented  
> 产品真相源: [按昵称打包用户关联数据](../product/user-data-archive.md)

## 1. 目标与已确认边界

本能力供服务器本地管理员按应用账号昵称生成单用户、脱敏、未压缩 `.tar`。用户已确认本次 MVP 直接使用独立脚本，不新增 `neurun` 主命令，不修改 MCP tool。

```bash
python scripts/export_user_data.py \
  --nickname <昵称> \
  --destination <已存在目录> \
  [--email <应用账号邮箱>] \
  [--output table|json]
```

- `--nickname`、`--destination` 必填，流程无交互。
- `--output` 默认 `table`；`json` 模式只向 stdout 写一条 JSON。
- 数据根目录沿用 `NEURUN_DATA_DIR`、兼容 `RUNDOWN_DATA_DIR`，未设置时使用项目根目录 `data/`。
- 本次不提供审批参数、凭据导出、压缩、恢复或远程传输能力。

## 2. 模块职责

### `src/user_data_archive.py`

- 只读扫描 `<data_root>/users/*.json`，不初始化 `UserManager`，因此不会触发 `mkdir` 或 `chmod`。
- 执行昵称匹配、邮箱消歧、allowlist 枚举、文件一致性检查、tar 写入、manifest 生成和原子发布。
- 通过 `ArchiveError` 暴露稳定的 `error_code`、`exit_code`、`retryable` 和非敏感说明。

### `scripts/export_user_data.py`

- 仅负责 `argparse` 参数解析、数据根目录解析、调用核心模块和 table/JSON 渲染。
- 不导入或注册 `src.main` 子命令，不向 `src.mcp_server` 注册工具。

## 3. 用户定位

1. 输入昵称与记录昵称分别执行 `strip()`。
2. 之后按 Unicode 码点精确、区分大小写比较；不做 casefold、Unicode 归一化、模糊或子串匹配。
3. 零匹配返回 `user_not_found`。
4. 多匹配且无 `--email` 返回 `nickname_ambiguous`，结果只包含候选数量和脱敏邮箱。
5. 提供邮箱时，邮箱两端去空白并 casefold，与昵称候选集合比较；必须唯一指向同一账号，否则返回 `disambiguation_mismatch`。
6. 注册记录文件名必须等于 `<api_key>.json`，内部 `api_key` 不得为空或含路径分隔符；不一致视为源数据不安全。

## 4. 数据 allowlist

| 逻辑成员 | 来源 | 必需性 | 处理 |
|---|---|---|---|
| `account.json` | `users/<api_key>.json` | 必需 | 重新序列化，仅保留 `nickname`、`email`、`provider`、`garmin_domain`、`created`、`last_sync`、`token_status` |
| `data/data.db` | `<api_key>/data.db` | 必需 | 按普通文件字节读取，不调用 SQLite backup API |
| `memory/...` | `<api_key>/memory/` | 可选 | 只递归包含 regular files，路径排序确定性枚举 |
| `sync/sync-tasks.json` | `<api_key>/sync-tasks.json` | 可选 | regular file 原样复制 |
| `backup/data.db` | `backup/<api_key>.db` | 可选 | 只允许目标用户对应文件，归档路径不暴露 API Key |
| `manifest.json` | 运行时生成 | 必需 | 顶层清单，不记录自身 hash |

可选类别不存在或 `memory/` 没有 regular file 时，分别在 `missing_categories` 记录 `memory`、`sync_tasks`、`backup`。

永久排除：`tokens/`、`huawei-tokens/`、API Key、密码哈希、`garmin_email`、Provider token/可重放凭据、其他用户注册记录或数据、全局 `users/`/`invite-codes`/其他备份、环境配置、日志、源码、symlink 和设备/FIFO/socket 等特殊文件。

## 5. 源文件安全与一致性

- 数据根、`users/`、目标用户根、`memory/` 和存在的 `backup/` 必须是实际目录，不能是 symlink。
- 文件先 `lstat`，再以 `O_NOFOLLOW`（平台支持时）打开，并用打开文件描述符 `fstat` 校验 regular file。
- 读取完整 bytes 后再次 `fstat` 与 `lstat`；设备号、inode、size 或纳秒 mtime 任一变化，或读取长度不等于初始 size，返回 `source_changed`。
- SQLite 与其他文件采用相同字节复制策略。该策略保证单文件读取前后属性一致，但不提供跨 SQLite、memory 和任务文件的同事务时间点快照。
- `memory/` 使用 `os.walk(..., followlinks=False)`，目录名和文件名分别排序；发现 symlink 或特殊文件立即失败，不静默跳过。
- tar 成员仅允许预定义逻辑相对路径；拒绝绝对路径、空段、`.` 和 `..`。

## 6. Manifest 合同

`manifest.json` 包含：

- `format_version`、UTC `created_at`、opaque `request_id`；
- `archive_format=tar`、`compression=none`、`privacy_profile=redacted-user-data`；
- 每个已包含非 manifest 成员的 `path`、`size`、整数秒 `mtime`、`sha256`；
- `missing_categories`；
- 固定 `excluded_categories`，声明普通导出永久不包含的敏感类别。

成功结果另返回最终 tar 的 `archive_size` 与 `archive_sha256`。manifest 不记录自身哈希，避免自引用。

## 7. 原子发布与权限

1. `destination` 必须已存在、为实际目录且可写/可进入；脚本不创建目标目录。
2. 文件名为 `neurun-user-data-<12 hex>-<YYYYMMDDTHHMMSSZ>.tar`，不包含昵称、邮箱或 API Key。
3. 在目标目录用 `O_CREAT | O_EXCL` 创建随机临时文件，mode `0600`。
4. tar 写完后 flush + `fsync`，再用 `os.replace` 发布，最终再次 `chmod 0600` 并尽力 fsync 目录。
5. 已有最终路径返回 `archive_collision`，不覆盖；失败尽力删除临时文件。

## 8. 输出与退出码

| Exit code | 分类 | 示例 error_code |
|---|---|---|
| `0` | 成功 | — |
| `2` | 输入、未找到、重名、消歧失败 | `invalid_nickname`、`user_not_found`、`nickname_ambiguous`、`disambiguation_mismatch` |
| `3` | 权限 | `permission_denied`、`destination_not_writable` |
| `4` | 目标或 I/O | `destination_not_found`、`destination_not_directory`、`source_io_error`、`archive_io_error` |
| `5` | 源一致性、归档安全、冲突 | `required_source_missing`、`source_changed`、`unsafe_source`、`archive_collision` |

失败 JSON 固定含 `status=failed`、`error_code`、`retryable` 和非敏感 `message`。实现不记录或打印昵称、完整邮箱、API Key、密码哈希、Token、归档正文。

## 9. 测试方案

`tests/test_user_data_archive.py` 覆盖：唯一昵称成功、成员顺序、manifest、成员/归档 hash 和 `0600`；重名失败、脱敏候选、邮箱消歧成功与 mismatch；敏感账号字段、Token 目录、其他用户排除；可选类别缺失；symlink/FIFO 拒绝、必需 DB 缺失、读取期间源变更；目标目录缺失、碰撞不覆盖、临时文件清理；JSON 单行合同、退出码与 argparse 必填参数；以及 `src.main` 与 MCP 未暴露该能力。
