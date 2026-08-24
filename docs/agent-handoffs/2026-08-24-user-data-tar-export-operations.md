# 用户关联数据 TAR 导出运营交接

- **日期**：2026-08-24
- **角色**：operations

## status

`blocked`

## summary

“按昵称直接打包所有关联数据”不具备上线条件。已形成最小化、可审计、失败关闭的数据导出治理要求，并收紧既有 ECS 还原手册，移除 Token、用户注册原件与 Cookie 注入步骤。需先由主 Agent 完成范围和合规裁决，才能进入产品、设计和实现阶段。

## facts

- 昵称允许重复且可修改；登录邮箱是唯一性校验字段，`UserManager` 仅提供邮箱和 `api_key` 查询，不提供昵称查询。
- 用户注册原件含邮箱、`password_hash`、平台账号字段和 `api_key`；用户目录还可能包含 OAuth/平台 Token、SQLite、记忆与同步任务。
- 现有 ECS 还原手册此前文字称不下载 Token，但 TAR 命令实际包含 `tokens/`，并提供 Cookie 注入方式；本次已修正。
- 仓库中未验证存在导出权限模型、导出实现、审计存储、加密交付、字段级脱敏或自动清理能力。

## assumptions

- 导出功能仍处于方案阶段，未向用户或运营人员开放。
- `api_key` 能作为经核验后稳定的内部目标键，但不得进入文件名、指标、普通日志或对外材料。
- 数据负责人和安全负责人将参与涉及法律依据、保留与凭据的决策；当前未给出相应政策或既有留存期限。

## artifacts

- [用户关联数据 TAR 导出治理与上线准备](../operations/user-data-tar-export-governance.md)：运营目标、用户分层、数据 allowlist/脱敏、权限审计、归档、命名、指标、失败重试、应急与上线清单。
- [ECS 用户数据本地还原](../operations/ecs-user-data-restore.md)：收紧为最小排障数据包，排除 Token、注册原件和 Cookie 注入。
- [本交接记录](2026-08-24-user-data-tar-export-operations.md)。

## decisions_needed

1. 导出入口是否仅限本人自助访问；是否允许支持、迁移或取证，以及每类的法律依据、审批人和 break-glass 边界。
2. 精确可导出数据类别、`data.db`/记忆字段级脱敏规则，以及是否存在任何经批准的凭据处理。
3. TAR 加密格式、密钥管理、受控交付渠道、下载次数与有效期。
4. 审计系统、访问边界、保留期限、删除机制、安全事件响应责任人。
5. 重试/退避、并发、文件大小、保留期和告警阈值；当前无数据基线，不能编造目标值。

## next_agent

`main_agent`：先完成上述范围与合规裁决；确认后依次交给 `product_design`、`developer`、`e2e`。

## handoff_packet

```text
目标：提供单用户、经授权且最小化的数据 TAR 导出能力；绝不按昵称直接选择账号或“打包全部文件”。

已确认约束：
- 昵称只能作人工受理线索；候选不唯一时失败关闭，二次核验后锁定单一 api_key。
- 普通包永久排除 tokens/、huawei-tokens/、Cookie、密码哈希、邀请码、环境变量、配置、全局 users/、backup/ 和其他用户路径。
- 必须有请求/审批/执行职责分离、最小权限、审计、allowlist、manifest、SHA-256、敏感扫描、受控交付、到期清理和可重放失败终态。
- 无加密交付、审计写入、完整性校验或到期清理，均不得上线；不得外部发布、群发、数据修改或自动触达。

验收标准：执行 docs/operations/user-data-tar-export-governance.md 第 8 节全部八项检查，覆盖重复昵称、权限、路径/凭据负测、manifest/哈希、日志脱敏、重试清理和生产等价演练。

文件范围：
- operations 已修改 docs/operations/ecs-user-data-restore.md，并新增 docs/operations/user-data-tar-export-governance.md。
- 后续产品/技术真相源、业务代码和测试由相应角色在主 Agent 确认后决定；不要覆盖上述运营文档。

验证命令：
- 本阶段为运营文档评估，仅运行 git diff --check。
- developer 必须提供新导出能力的单元/集成验证命令；e2e 独立执行受控账户验收。

剩余风险：当前没有已验证的权限、审计、加密交付、脱敏、保留/清理实现或数据基线；未裁决前不得实现或上线。
```
