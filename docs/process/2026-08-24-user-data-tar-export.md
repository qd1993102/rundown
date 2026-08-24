# 单用户脱敏 TAR 导出

- **日期**: 2026-08-24
- **类型**: feature

## 背景与动机

运维需要根据应用账号昵称提取单个用户的最小关联数据，用于受控排障或交付。原始数据分散在账号注册表、用户隔离目录和共享备份目录，手工打包容易混入 API Key、密码哈希、Provider Token、其他用户数据或不安全路径。

用户确认本次 MVP 直接提供独立脚本，不维护在 `neurun` 主命令或 MCP 中，并固定为不含凭据的脱敏导出。

## 方案选择

选择“独立 wrapper + 可测试核心模块”：

- `scripts/export_user_data.py` 保持调用面简单且适合 shell/AI 自动化。
- `src/user_data_archive.py` 集中实现只读发现、allowlist、校验、manifest 和原子写入，便于单元测试。
- 不直接实例化 `UserManager`，因为其构造函数会创建数据根和 `users/` 并调整权限，不符合只读导出边界。
- SQLite 采用普通字节读取与前后 stat 校验，不使用 `sqlite3.Connection.backup`，避免改变既有测试和运行依赖。
- tar 内使用固定逻辑路径，不保留真实 API Key 路径。

未选择：不增加 `neurun user-data` 子命令，不导出 tokens、Huawei tokens、密码哈希、API Key 或账号原件，不使用 `tarfile.add`。

## 实现步骤

1. 核对 `src/users.py`，确认注册表位于 `data/users/<api_key>.json`，用户数据库、memory、同步任务和共享备份路径。
2. 建立稳定错误类型与退出码映射，完成昵称精确匹配和邮箱消歧。
3. 实现单用户 allowlist、regular file 校验、确定性 memory 枚举和读取前后 stat 一致性检查。
4. 生成脱敏 `account.json` 与逐成员 SHA-256 manifest。
5. 以目标目录内 `0600` 临时文件写 tar，fsync 后 `os.replace` 原子发布并计算最终归档 hash。
6. 添加独立 argparse/Rich wrapper，JSON 模式保持 stdout 单条 JSON。
7. 添加覆盖成功、歧义、敏感排除、不安全文件、源变化、碰撞和 CLI 合同的单元测试。
8. 同步技术设计、README 和 CHANGELOG。

## 遇到的问题与解决

- **只读发现与 `UserManager` 冲突**：`UserManager.__init__` 会调用目录权限维护函数。改为按同一注册表格式直接只读扫描，并严格校验记录文件名与内部 API Key 一致。
- **symlink 竞态**：仅 `Path.is_file()` 会跟随链接。改为 `lstat` + `O_NOFOLLOW` + 打开文件描述符 `fstat`，并在读取后复核 inode、size 和 mtime。
- **归档路径泄漏**：直接加入源路径会暴露 API Key。改为手工创建 `TarInfo`，只写 `data/data.db`、`memory/...` 等逻辑路径，并清空 uid/gid 用户名信息。
- **manifest 自引用 hash**：manifest 若包含自身 hash 会产生循环依赖。按合同只记录其他成员，最终 tar hash 在成功结果中单独返回。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/user-data-archive.md](../design/user-data-archive.md)
- Product: [docs/product/user-data-archive.md](../product/user-data-archive.md)
