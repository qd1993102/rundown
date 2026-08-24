# 产品方案 — 按昵称打包用户关联数据

> 版本: v1.0 · 日期: 2026-08-24
> 状态: 已确认并实现（独立脚本 MVP）
> 配套技术设计: [user-data-archive.md](../design/user-data-archive.md)

## 已确认决策

1. 入口是服务器本地管理员直接运行的 `scripts/export_user_data.py`，不新增 `neurun` 子命令、Web 入口或 MCP tool。
2. 本 MVP 固定为 `redacted-user-data`，永久排除 API Key、密码哈希、平台 Token、可重放凭据、Cookie、邀请码、配置、日志、源码、其他用户数据、符号链接和特殊文件。
3. 昵称只做两端 `strip()` 后的 Unicode 码点精确、区分大小写匹配；重名必须用应用登录邮箱消歧，不能自动猜测。
4. 文件名使用不透明随机请求 ID 和 UTC 时间，不包含昵称、邮箱或 API Key；归档和临时文件权限为 `0600`。
5. 归档保证逐文件读取一致性和最终 tar 完整性，不承诺 SQLite、memory、同步任务和备份之间的事务级同一时点快照。
6. 脚本只生成本地归档；加密传输、审批系统、对象存储交付和到期清理不属于本 MVP，由运维按受控流程处理。

## 1. 业务目标与范围

- **核心价值**: 运维管理员通过独立脚本提供用户当前昵称，即可在不浏览和手工拼接多个私有目录的情况下，生成该用户脱敏关联数据的单个 tar 归档，用于受控排障或交付。
- **成功指标**:
  1. 在自动化覆盖的单用户、重名用户、无匹配用户和昵称含特殊字符场景中，归档错配其他用户的比例为 0%。
  2. 成功归档中，allowlist 允许的已存在文件覆盖率为 100%，每个归档成员均记录大小与 SHA-256 校验值。
  3. 归档中其他用户数据、全局邀请码、服务环境变量和应用日志的泄漏率为 0%。
  4. 任一读取、校验或写入步骤失败时，可被调用方误认为成功的完整 `.tar` 文件数量为 0；进程退出码必须非零。
  5. 成功归档文件权限为 `0600`，中间文件和失败残留的可读取 tar 文件数量为 0。
  6. `--output json` 下成功、未找到、匹配歧义、权限不足、空间不足、数据变化冲突和 I/O 失败均返回稳定的机器可读状态与错误码。
- **In-Scope**:
  1. 独立脚本入口：`python scripts/export_user_data.py --nickname <NICKNAME> --destination <DIR> --output json`；可选 `--email` 用于昵称重名消歧，`--output` 支持 `table` 与 `json`。
  2. `--nickname` 和 `--destination` 必填；目标目录必须已存在且可写；全流程不得出现交互式确认。
  3. 以当前账号记录中的昵称匹配用户：仅移除输入两端 Unicode 空白后执行 Unicode 码点完全相等、区分大小写的匹配；不做模糊、前缀、子串、拼音、大小写折叠或 Unicode 归一化匹配。
  4. 零匹配时失败；单匹配时将结果锁定到该账号的不可变内部键后继续；多匹配时不创建归档并返回候选数量、脱敏邮箱和不透明用户引用。调用方必须同时传入同一昵称与唯一登录邮箱，且二者指向同一账号后才能锁定并继续；开始读取后昵称变化不得切换目标账号。
  5. 数据范围包含脱敏账号字段、用户隔离目录中的 SQLite 主库、`memory/` regular files、可选同步任务状态，以及共享备份目录中以该用户内部账号键关联的 SQLite 备份；不存在的可选类别写入清单 `missing_categories`，不伪造空文件。
  6. 每个 tar 顶层包含 `manifest.json`；清单记录归档请求 ID、创建时间、匹配口径、脱敏用户引用、凭据策略、包含/排除类别、每个成员的归档相对路径、来源类别、字节数、修改时间和 SHA-256，以及缺失项和一致性说明。
  7. 本 MVP 不提供 `full-recovery`；账号导出只保留显式白名单字段，永久排除 API Key、密码哈希、平台 Token、可重放登录材料及其他认证密钥。
  8. tar 成员必须使用归档内相对路径；不得包含绝对路径、`..` 路径段、指向允许数据根目录之外的符号链接或设备文件。
  9. 安全默认命名为 `neurun-user-data-<request-id>-<YYYYMMDDTHHMMSSZ>.tar`；`request-id` 必须是不含昵称、邮箱、完整或截断 API Key 的不可枚举标识。最终文件名、临时文件名和归档顶层路径均不得包含直接用户标识。
  10. 成功结果返回归档绝对路径、文件名、字节数、SHA-256、文件数量、凭据策略、开始/完成时间和警告；JSON 只写 stdout，诊断信息只写 stderr，且均不得输出完整 API Key、密码哈希或平台凭据。
  11. CLI exit code 固定为：`0` 成功；`2` 参数、未找到或匹配歧义；`3` 执行权限不足；`4` 目标目录、磁盘空间或文件 I/O 失败；`5` 数据一致性或并发冲突。
  12. 归档成功前使用不可被误认成最终结果的同目录临时文件；完成全部成员校验后才原子发布最终 `.tar`，最终权限为 `0600`。
- **Out-of-Scope**:
  1. 用户自助下载、Web 管理后台、邮件发送、对象存储上传、下载链接或第三方传输。
  2. 按模糊昵称、平台昵称、邮箱单独检索、API Key 单独检索或批量导出多个用户。
  3. 解密、转换或重新签发平台凭据；重置密码、修改账号、删除账号或恢复归档。
  4. 打包其他用户数据、全局邀请码文件、`.env`、服务级密钥、应用日志、源代码或系统配置。
  5. 自动发现用户曾通过任意 `--export PATH` 写到用户隔离目录之外的文件；缺少可验证用户归属的外部文件不得纳入。
  6. gzip、zip、分卷、增量归档、加密归档和数字签名；本次输出格式严格为未压缩 POSIX tar。
  7. 修改现有账号、同步、报告、训练或备份用户旅程。

## 2. 用户故事与验收标准 (Acceptance Criteria)

### US-01: 按唯一昵称生成归档
- **As a**: 获得授权的服务器运维管理员
- **I want**: 用用户当前昵称执行一条非交互 CLI 命令
- **So that**: 我能一次取得该用户全部约定范围内的关联数据
- **Gherkin 验收条件**:
    - Given 当前账号注册表中恰好一个账号的昵称与 `--nickname` 在移除输入两端 Unicode 空白后按 Unicode 码点完全相等
    - When 管理员指定可写目标目录和 `--output json` 执行独立导出脚本
    - Then 系统只解析该账号的允许数据根，生成一个权限为 `0600` 的 `.tar` 文件并以 exit code `0` 结束
    - And 系统在读取任何用户文件前把匹配结果锁定为唯一不可变内部账号键
    - And stdout 返回单行 JSON，包含 `status=success`、归档路径、字节数、SHA-256、成员数量和缺失可选类别
    - And tar 内 `manifest.json` 对每个已包含成员记录归档相对路径、来源类别、大小、修改时间和 SHA-256

### US-02: 阻止昵称误匹配与歧义导出
- **As a**: 获得授权的服务器运维管理员
- **I want**: 昵称不存在或重复时得到明确且不可误导的结果
- **So that**: 我不会把错误用户的数据交付给请求方
- **Gherkin 验收条件**:
    - Given 没有账号昵称与输入完全相等
    - When 管理员执行归档
    - Then 系统返回 exit code `2`、`status=failed`、`error_code=user_not_found`，且不创建最终或可读取的临时 tar
    - Given 两个或以上账号使用同一昵称且未提供 `--email`
    - When 管理员执行归档
    - Then 系统返回 exit code `2`、`error_code=nickname_ambiguous`、候选数量、脱敏邮箱和不透明用户引用，不输出完整 API Key、密码哈希或任何 Provider 标识
    - Given 管理员同时提供昵称与邮箱消歧
    - When 邮箱不存在、邮箱对应账号的昵称不一致或仍不能唯一定位
    - Then 系统返回 exit code `2`、`error_code=disambiguation_mismatch`，且不创建归档

### US-03: 遵守脱敏数据范围
- **As a**: 对归档内容负责的运维管理员
- **I want**: 归档清单准确说明包含、排除和缺失的数据
- **So that**: 我能判断归档是否适合恢复或隐私交付
- **Gherkin 验收条件**:
    - Given 唯一用户具有账号记录、SQLite 主库、同步状态、记忆/报告、用户目录内导出物和共享备份
    - When 系统按 `redacted-user-data` allowlist 归档
    - Then 上述每个已存在类别均被纳入或在 `excluded_categories` 中给出稳定排除原因，其他用户和全局文件均不进入 tar
    - Given 用户目录存在平台凭据且账号记录存在 API Key 与密码哈希
    - Then tar 和 `manifest.json` 均不包含这些秘密值，清单以类别和数量记录排除结果，不记录秘密摘要或片段
    - Given 任一可选类别不存在
    - When 其他必需数据可安全读取
    - Then 归档仍可成功，清单在 `missing_categories` 中记录类别与 `not_present`，不得创建伪造占位数据

### US-04: 安全发布和机器可读失败
- **As a**: 通过自动化脚本调用该能力的运维管理员
- **I want**: 成功与失败具有稳定输出且不留下半成品
- **So that**: 自动化可以安全交付、重试或告警
- **Gherkin 验收条件**:
    - Given 执行身份无权读取用户数据或写入目标目录
    - When 执行归档
    - Then 返回对应 exit code `3` 或 `4` 和稳定错误码，不降级到其他目录、不提升权限、不创建最终 tar
    - Given 脚本执行身份无权读取用户数据或写入目标目录
    - When 执行归档
    - Then 返回稳定权限错误且在失败后不创建归档
    - Given 目标目录剩余空间不足、任一必需文件读取失败、成员校验失败或源数据变化导致无法满足一致性规则
    - When 归档进行中
    - Then 返回 exit code `4` 或 `5`、`status=failed`、`retryable`，删除临时归档且不覆盖同名已有文件
    - Given tar 成员候选包含绝对路径、`..`、设备文件或逃逸允许数据根的符号链接
    - When 系统校验成员
    - Then 整体失败并返回 `error_code=unsafe_archive_member`，不得仅跳过该成员后宣告完整成功
    - Given 归档全部写入且成员校验通过
    - When 系统发布结果
    - Then 最终文件在同一目标目录原子出现、权限为 `0600`，JSON 中的大小与 SHA-256 与磁盘文件完全一致

### US-05: 并发与重复执行
- **As a**: 同时处理多个数据请求的运维管理员
- **I want**: 重复或并发命令不会互相覆盖或混入其他用户数据
- **So that**: 每个交付物都能独立追踪和验证
- **Gherkin 验收条件**:
    - Given 同一用户已有归档任务正在发布快照
    - When 第二个任务开始且无法取得一致读取条件
    - Given 不同用户同时执行归档
    - When 两个任务均可安全读取各自数据
    - Then 每个 tar 只包含各自用户数据，且文件名、请求 ID和临时文件互不冲突
    - Given 同一命令在同一秒内重复成功执行
    - When 目标目录相同
    - Then 每次使用唯一随机请求 ID 生成不同文件名，不覆盖或追加到已有 tar

## 3. 状态机与边界条件 (Edge Cases)

- **状态流转**: [待校验] -> (参数与权限有效) -> [匹配用户] -> (唯一匹配或邮箱成功消歧) -> [构建清单] -> (范围与路径安全校验通过) -> [生成临时归档] -> (成员与归档校验通过) -> [原子发布] -> [成功]
- **失败流转**: [匹配用户] -> (零匹配/多匹配/消歧不一致) -> [失败且无归档]
- **一致性失败流转**: [生成临时归档] -> (读取失败/源数据冲突/校验失败/中断) -> [清理临时归档] -> [可重试失败]
- **异常处理规则**:
    1. 空昵称或仅空白: 返回 `invalid_nickname` 与 exit code `2`；提示文案“请通过 --nickname 提供非空的当前昵称”。
    2. 昵称未找到: 返回 `user_not_found` 与 exit code `2`；不得回退为模糊匹配或平台昵称匹配。
    3. 昵称重复: 返回 `nickname_ambiguous` 与 exit code `2`；仅允许通过 `--email` 消歧，不得选择首条、最新或最近同步账号。
    4. 邮箱消歧失败: 返回 `disambiguation_mismatch` 与 exit code `2`；提示文案“昵称与邮箱未唯一指向同一账号”。
    5. 权限不足: 返回 `permission_denied` 与 exit code `3`；错误只指出不可访问的类别，不打印秘密内容。
    6. 目标目录不存在、不是目录或不可写: 返回对应目标错误与 exit code `4`；不创建目录，不回退当前目录或 `/tmp`。
    7. 目标文件碰撞: 返回 `archive_collision` 与 exit code `5`；不得覆盖、截断或追加已有文件。
    8. 空间不足或写入中断: 返回归档 I/O 错误，清理同次请求的临时文件。
    9. 可选类别缺失: 在 `missing_categories` 中记录并继续；账号记录或 SQLite 主库缺失/不可读属于必需数据失败。
    10. 数据在读取期间变化: 返回 `source_changed` 与 exit code `5`；不得发布混合版本并标记成功。
    11. 不安全成员: 绝对路径、路径穿越、设备文件或逃逸符号链接导致整体失败 `unsafe_source`；不得跟随链接读取外部文件。
    12. 输出脱敏: 表格、JSON、stderr 与应用日志不得出现完整 API Key、密码哈希、access token、refresh token、可重放登录材料、CrewPals token 或归档成员正文。

## 4. 数据实体草案

- **UserArchiveRequest**: { nickname: String, Required: Yes; disambiguation_email: String, Required: No; destination: String, Required: Yes; output: Enum(table/json), Required: Yes }
- **CandidateSummary**: { candidate_count: Int, Required: Yes; candidate_emails: Array<String> (masked), Required: Yes }
- **ArchiveCategory**: { name: String, Required: Yes; status: Enum(included/excluded/missing), Required: Yes; reason: String, Required: No; member_count: Int, Required: No }
- **ArchiveMember**: { archive_path: String (Relative), Required: Yes; category: ArchiveCategory.name, Required: Yes; size_bytes: Int, Required: Yes; modified_at: String (ISO 8601), Required: Yes; sha256: String, Required: Yes }
- **UserArchiveManifest**: { format_version: Int, Required: Yes; request_id: String, Required: Yes; created_at: String (ISO 8601 UTC), Required: Yes; archive_format: `tar`, Required: Yes; compression: `none`, Required: Yes; privacy_profile: `redacted-user-data`, Required: Yes; members: Array<ArchiveMember>, Required: Yes; missing_categories: Array<String>, Required: Yes; excluded_categories: Array<String>, Required: Yes }
- **UserArchiveResult**: { status: Enum(success/failed), Required: Yes; request_id: String, Required: Yes on success; archive_path: String, Required: No; archive_size: Int, Required: No; archive_sha256: String, Required: No; member_count: Int, Required: No; missing_categories: Array<String>, Required: No; error_code: String, Required: No; retryable: Boolean, Required: No }
