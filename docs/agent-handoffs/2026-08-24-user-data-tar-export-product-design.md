# 按昵称打包用户关联数据 — 产品设计交接

## status

`blocked`

## summary

已确认 `docs/product/` 无现有真相源，并创建 Proposed 产品文档，覆盖管理员 CLI 入口、昵称精确匹配与重名消歧、数据范围、敏感凭据策略、权限审批、tar 命名、状态机、机器可读输出和验收标准。产品方案与配套技术设计经用户确认前不得实现。

## facts

- `docs/product/` 原有账号、数据源同步、报告、分享卡和训练等旅程，没有按昵称打包单用户全部关联数据的产品所有者。
- 现有账号规则确认昵称长度为 2–32 字符、允许重复且可修改；登录邮箱唯一，内部 API Key 用作用户数据目录键。
- 用户关联存储包括账号记录、用户隔离目录中的 SQLite、同步任务、Token/Provider 凭据、Markdown 记忆与报告，以及共享备份目录中的用户 SQLite 备份。
- 账号记录含 API Key、邮箱和密码哈希；Token 目录可能包含 access token、refresh token、可重放登录材料或 CrewPals token，均属高敏感凭据。
- 产品方案禁止昵称模糊匹配或多匹配时静默选择；多匹配必须使用同一昵称加唯一登录邮箱消歧，成功后锁定不可变内部账号键。
- 产品方案将输出定义为未压缩 POSIX `.tar`，要求 `manifest.json`、逐成员 SHA-256、相对安全路径、最终权限 `0600`、临时文件清理、原子发布和稳定 exit code/JSON error code。
- 并行运营方案要求普通支持/访问数据包排除凭据、账号原件和全局备份，并禁止文件名包含昵称；产品文档已把与原始“所有关联数据”的冲突列为 blocker，文件名采用不透明 `request_id` 的安全默认值。
- `.gitignore` 中新增 `online-data/` 不是 product_design 修改；该改动在检查工作区时已存在，product_design 未触碰、未恢复该文件。

## assumptions

- 本能力是服务器本地管理员能力，不是终端用户自助下载或 Web 管理能力。
- `request_id` 最终可由一个受控审批/审计真相源校验；仓库当前未验证存在该系统。
- “所有关联数据”可能指灾备/迁移全量恢复，也可能指本人数据访问或普通排障；在用户裁决前不能把这三种用途合并成一个默认范围。
- 技术设计将定义 SQLite 一致快照方式、锁与并发语义、受控目标目录、审计落点和 MCP 安全例外。

## artifacts

- `docs/product/user-data-archive.md`：新增 Proposed 产品真相源。
- `docs/product/index.md`：新增该用户旅程索引，标注待确认、待配套技术设计。
- `docs/agent-handoffs/2026-08-24-user-data-tar-export-product-design.md`：本交接记录。

## decisions_needed

1. 确认允许执行的主体、审批人、`request_id` 真相源和审计要求；仅 SSH 本地文件读取权限是否足够，还是必须新增独立导出角色。
2. 确认能力用途：仅受控灾备/迁移，还是也支持本人访问/普通排障；不同用途是否拆成独立命令或凭据策略。
3. 确认 `full-recovery` 是否允许包含账号 API Key、密码哈希、Provider Token 和可重放凭据；若不允许，则固定为 `privacy-export` 并删除全量凭据分支。
4. 确认共享备份、账号原件、同步任务和用户目录内既有导出物是否属于“所有关联数据”的必选范围。
5. 确认不透明命名 `neurun-user-data-<request-id>-<UTC>.tar`；若坚持文件名包含昵称，需显式接受文件名泄露风险。
6. 确认一致性级别：逐文件自洽还是跨 SQLite、Markdown、任务状态和凭据的严格同一时点快照，并确认可接受暂停写入时长。
7. 确认管理员 MCP 策略：显式开启且仅限 stdio/localhost，或因高敏感数据导出而不注册 MCP tool 的安全例外。

## next_agent

`main_agent`：合并 product_design 与 operations 的冲突项并请求用户确认；确认后再交给 `developer` 先创建 `docs/design/` 配套技术设计，技术设计获用户确认后才可实现。

## handoff_packet

```text
目标：提供“根据用户昵称直接打包所有关联数据为 tar 文件”的新用户旅程，同时保证重名不误导出、敏感数据不越界、结果可供脚本可靠判断。

已确认约束：
- 入口拟为服务器本地非交互 CLI：neurun user-data archive --request-id ... --nickname ... --destination ... --output json。
- 昵称只做 trim 后 Unicode 码点完全相等且区分大小写的匹配；不做模糊、前缀、拼音、casefold 或 Unicode 归一化。
- 零匹配失败；多匹配失败并返回脱敏候选，只有 nickname + 唯一登录邮箱指向同一账号才能消歧；随后锁定不可变内部账号键。
- 不允许其他用户、全局邀请码、.env、服务级密钥、日志、源码或系统配置进入 tar。
- tar 必须使用相对安全路径，禁止绝对路径、..、设备文件和逃逸符号链接；必须包含 manifest、逐成员 SHA-256、缺失/排除类别和一致性说明。
- 最终文件权限 0600；同目录临时文件通过全部校验后原子发布；失败清理临时文件且不得留下可误认成功的 tar。
- JSON 只写 stdout、诊断写 stderr，秘密值不得出现在输出或日志；exit code 为 0/2/3/4/5 的稳定分类。
- 文件名安全默认不含昵称、邮箱或 API Key，使用 neurun-user-data-<request-id>-<YYYYMMDDTHHMMSSZ>.tar。
- 产品和配套技术设计必须先获用户确认；当前不得实现代码。

核心验收标准：
- 唯一昵称 + 有效审批 + 安全目标目录生成单一 0600 tar，manifest 与磁盘文件大小/哈希一致。
- 无匹配返回 user_not_found；重名返回 nickname_ambiguous；错误消歧返回 disambiguation_mismatch；均不创建归档。
- 无效审批在读取用户业务数据前返回 authorization_denied。
- 凭据策略对账号记录、Provider Token 和密码哈希的纳入/排除可自动验证，且秘密永不进入 stdout/stderr/日志。
- 权限、空间、I/O、SQLite 快照、源数据变化、并发和不安全成员均失败关闭，不覆盖已有文件。
- 同秒重复执行使用不同 request_id，不同用户并发归档不混入彼此数据。

文件范围：
- Product 已修改 docs/product/index.md。
- Product 已新增 docs/product/user-data-archive.md。
- Product 已新增 docs/agent-handoffs/2026-08-24-user-data-tar-export-product-design.md。
- Product 未修改 .gitignore；online-data/ 是其他 Agent 的并行改动，不得归因或回滚。
- Product 未修改业务代码、tests、docs/design、README、CHANGELOG 或 operations 文档。

验证命令：
- git diff --check -- docs/product/index.md docs/product/user-data-archive.md docs/agent-handoffs/2026-08-24-user-data-tar-export-product-design.md
- rg -n "user-data-archive" docs/product/index.md
- rg -n "^## \[Blockers\]|^## 1\.|^## 2\.|^## 3\.|^## 4\." docs/product/user-data-archive.md
- 本阶段仅文档变更，未运行 pytest。

剩余风险：
- 权限/审批/审计系统、用途与法律边界未确认。
- 普通最小化导出与全量恢复包的数据范围冲突未裁决。
- 凭据、账号原件和共享备份是否可入包未确认。
- 跨文件一致性级别与暂停写入预算未确认。
- MCP 同步规则与高敏感导出的暴露限制尚未形成技术设计。
```
