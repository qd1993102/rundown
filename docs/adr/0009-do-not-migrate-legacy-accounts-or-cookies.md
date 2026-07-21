# Do not migrate legacy accounts or cookies

新认证体系不迁移旧用户记录、旧 `api_key` Cookie 或历史平台数据，也不提供 Legacy Account Claim；所有旧 Cookie 在升级后直接失效，继续使用者必须凭新 Invitation 创建新的 neurun Account。旧数据目录暂时原样留在磁盘但新系统不读取、不自动合并也不自动删除。这个成本优先的边界避免为少量重复且无登录凭证的旧记录建设一次性迁移流程，并明确取代 ADR-0008。
