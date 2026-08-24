# User Data TAR Export E2E 验收报告

- **验收日期**: 2026-08-24
- **验收范围**: `scripts/export_user_data.py` 的真实 CLI 调用、单用户脱敏 TAR 内容、失败清理、JSON 输出、CLI/MCP 入口边界
- **执行原则**: 只读业务代码；未修改业务代码、测试文件或生产配置；仅新增本报告
- **环境**: macOS，当前用户 `qindong`，Python `3.14.6`，`.venv/bin/pytest`，工作区 `/Users/qindong/Work/personal/rundown`
- **测试数据**: 本次复验使用独立临时数据根 `/var/folders/jy/c3vzh6hx1sg04lj_vy5h98xc0000gn/T/neurun-e2e-refresh-e6y4lo3w`，包含三个受控 fixture 账号，其中两个使用相同昵称；不使用线上数据或真实凭据

## 1. 执行命令

核心回归：

```bash
.venv/bin/pytest -q tests/test_user_data_archive.py
```

结果：

```text
17 passed in 0.12s
```

真实 CLI 目标调用形态：

```bash
NEURUN_DATA_DIR=<temporary-data-root> \
.venv/bin/python scripts/export_user_data.py \
  --nickname <昵称> --destination <existing-directory> \
  [--email <邮箱>] --output json
```

脚本 help 和主 CLI 入口检查：

```bash
.venv/bin/python scripts/export_user_data.py --help
.venv/bin/python -m src.main --help
```

## 2. 主流程与包内容

唯一昵称 `唯一昵称` 成功：exit `0`，JSON 单行，`status=success`。本次复验生成文件：

```text
/var/folders/jy/c3vzh6hx1sg04lj_vy5h98xc0000gn/T/neurun-e2e-refresh-e6y4lo3w/exports/neurun-user-data-6f6d49576760-20260824T093050Z.tar
```

实际 TAR 成员顺序和范围：

```text
account.json
data/data.db
memory/profile.md
sync/sync-tasks.json
backup/data.db
manifest.json
```

结果：PASS。

- 成员均在 allowlist 内，只有 `account.json`、`data/data.db`、允许的 `memory/`、`sync/`、`backup/` 和 `manifest.json`。
- `account.json` 只保留 `nickname`、`email`、`provider`、`garmin_domain`、`created`、`last_sync`、`token_status`。
- 包内容未命中 fixture 中的 API Key、其他用户 API Key、`password_hash`、`garmin_email`、Provider token、Huawei token、全局配置和其他用户诱饵。
- 文件名未包含昵称、邮箱、API Key 或 `api_key` 字样。

## 3. 完整性、格式和权限

结果：PASS。

- manifest 每个成员的 `sha256` 与解包后的实际字节一致。
- manifest 每个成员的 `size` 与实际内容长度一致。
- manifest 每个成员的 `mtime` 同时匹配 TAR member metadata 和源 regular file 的整数秒 mtime；`account.json` 按实现约定为 `0`。
- JSON 返回的 `archive_sha256` 与最终 TAR 字节 SHA-256 一致。
- JSON 返回的 `archive_size` 与最终 TAR 文件大小一致。
- 最终 TAR 权限为 `0600`，TAR 成员权限为 `0600`，成员均为 regular file。
- 归档为未压缩 TAR；以 `tarfile.open(..., "r:")` 成功读取，未检测到 gzip/zip magic。

## 4. 账号定位与 JSON 错误矩阵

| 场景 | exit | `error_code` | 结果 |
|---|---:|---|---|
| 唯一昵称 | 0 | - | PASS，生成归档 |
| 重名且无 email | 2 | `nickname_ambiguous` | PASS，无归档 |
| 重名且 email 大小写/空白可归一化 | 0 | - | PASS，选中对应账号 |
| 重名且 email 不匹配 | 2 | `disambiguation_mismatch` | PASS，无归档 |
| 目标目录不存在 | 4 | `destination_not_found` | PASS，不创建目录 |
| 目标路径不是目录 | 4 | `destination_not_directory` | PASS，不写入 |
| 已存在目录不可写（`/`） | 3 | `destination_not_writable` | PASS，不写入 |
| 必需 `data.db` 缺失 | 5 | `required_source_missing` | PASS，无归档 |
| memory 中存在 symlink | 5 | `unsafe_source` | PASS，无归档 |
| memory 中存在 FIFO | 5 | `unsafe_source` | PASS，无归档 |
| `os.walk` memory 权限错误 | 3 | `permission_denied` | PASS，fail-closed，无归档 |
| `os.walk` memory 其他 I/O 错误 | 4 | `source_io_error` | PASS，fail-closed，无归档 |

所有真实 CLI 场景均为单行 JSON，stderr 为空；失败目标目录没有留下最终 TAR 或临时 TAR。重复昵称错误只返回 masked candidate email，不回显完整账号标识。`os.walk` permission/I/O 场景由 API 回归测试注入并验证，非公开 CLI 参数场景。

## 5. 目标碰撞、源变化和临时文件清理

结果：PASS，证据层级为核心 API 测试而非 CLI 黑盒注入。

- [tests/test_user_data_archive.py:346](/Users/qindong/Work/personal/rundown/tests/test_user_data_archive.py:346) 使用固定 request ID 和时间制造同名目标文件，验证返回 `archive_collision`、exit `5`，原文件内容不变且目录无新增文件。
- [tests/test_user_data_archive.py:367](/Users/qindong/Work/personal/rundown/tests/test_user_data_archive.py:367) 在二次 stat 前修改 `data.db`，验证返回 `source_changed`、exit `5`、`retryable=true`，且目标目录没有最终或临时 TAR。
- [tests/test_user_data_archive.py:401](/Users/qindong/Work/personal/rundown/tests/test_user_data_archive.py:401) 验证缺失 `data.db` 为必需源失败。
- CLI symlink/FIFO 负向场景均实跑并确认失败目录为空；这两个场景的底层检查也由 [tests/test_user_data_archive.py:269](/Users/qindong/Work/personal/rundown/tests/test_user_data_archive.py:269) 覆盖。
- 新增 [tests/test_user_data_archive.py:300](/Users/qindong/Work/personal/rundown/tests/test_user_data_archive.py:300) 回归覆盖 `os.walk` 的 `PermissionError` 和通用 `OSError`：分别返回 `permission_denied`/exit `3` 与 `source_io_error`/exit `4`，两种情况均 fail-closed 且目标目录为空。

当前 CLI 命令不暴露固定 request ID 或读取期间 callback，因此目标碰撞和源变化无法仅通过公开 CLI 参数稳定注入；本报告没有将 API 层证据错误标记为 CLI 黑盒覆盖。

## 6. CLI/MCP 入口边界

结果：PASS。

- `scripts/export_user_data.py --help` 仅提供 `--nickname`、`--destination`、`--email`、`--output` 及其短选项。
- `src.main` 当前子命令为 `init`、`auth`、`sync`、`daily`、`activities`、`health`、`memory`、`status`、`setup`、`mcp`、`serve`、`invite`，没有 user-data archive/export 入口。
- `src.mcp_server` 没有新增 user-data archive tool；既有测试 [tests/test_user_data_archive.py:482](/Users/qindong/Work/personal/rundown/tests/test_user_data_archive.py:482) 对源码入口边界有断言。

## 7. 全量测试与失败层级

定向归档测试通过。全量命令曾运行：

```bash
.venv/bin/pytest -q
```

本轮输出在约 63% 后超过工具等待窗口，未取得终态，因此不把本轮全量视为通过。当前项目已有验收报告记录的全量已知无关失败是 sandbox 权限层失败：

```text
tests/test_training_service_factory.py::test_setup_baseline_longest_distance_uses_28d_window
PermissionError: [Errno 1] Operation not permitted: /Users/qindong/.neurun/data.db
```

该失败发生在测试触碰外部用户目录数据库时，不在本次导出业务路径内；未修复、未改变该测试或配置。全量历史基线见 [structured-share-card-ai-summary-e2e.md](/Users/qindong/Work/personal/rundown/docs/testing/2026-08-24-structured-share-card-ai-summary-e2e.md:45)。

失败层级结论：本次导出定向测试和真实 CLI 验收未发现业务失败；全量残余风险属于测试环境/沙箱权限与全量命令未取得终态，不应归因于 TAR 导出实现。

## 8. 未验证风险与建议动作

- 未在真实生产数据、真实授权系统或受控交付渠道验证；当前脚本本身没有审批、审计、加密传输、到期删除或交付控制。
- 未执行高并发/中断信号/磁盘空间耗尽/发布后目录 fsync 故障演练。
- 未以 CLI 黑盒方式注入读取期间源文件变化或固定命名碰撞；已有 17 项核心测试覆盖确定性实现边界，包括 memory 遍历 permission/I/O fail-closed。
- `data.db` 被按文件打包，报告未验证数据库内部是否存在跨用户记录；需要数据层或恢复演练进一步确认。
- 全量 pytest 应在可写且隔离的测试数据目录中重跑，得到稳定终态后再作为发布门禁依据。
