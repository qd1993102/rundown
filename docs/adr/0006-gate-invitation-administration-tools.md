# Gate invitation administration tools

Invitation 的生成、查询和停用只通过非交互式管理员 CLI 提供，不建设 Web 管理后台；命令必须支持机器可读 JSON 输出。`invite create` 返回新生成的完整邀请码，普通 `invite list` 只显示 ID、状态、时间和掩码，只有服务器本地 `invite show <id> --reveal` 可显式取回单个完整邀请码。为遵守 CLI 与 MCP 能力同步规则，项目保留对应 MCP tool 定义，但默认不注册，只有显式设置 `NEURUN_ENABLE_ADMIN_TOOLS=true` 才启用，而且只能运行在本地 stdio/localhost 管理模式；MCP 不提供邀请码明文读取能力，公网 `neurun serve` 模式即使误设开关也必须拒绝注册管理员 tools。这样本地运维保持低成本，同时避免配置错误向普通 AI 调用方暴露注册资格管理能力。
