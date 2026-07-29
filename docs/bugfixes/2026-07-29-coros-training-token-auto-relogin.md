# Bug: Coros Training Hub 短期 Token 到期后必须反复重新授权

- **发现日期**: 2026-07-29
- **修复日期**: 2026-07-29
- **严重程度**: major
- **影响范围**: Coros 活动、每日分析、HRV 同步与 Web 数据源连接状态

## 现象

Coros 首次绑定后可以同步，但 Training Hub Access Token 到期时活动接口返回 `result=1019`，
连接被标记为 `expired`，用户必须重新输入高驰账号密码。Mobile 睡眠虽有可重放登录载荷，但属于
另一套 API，不能更新 Training Hub Token。

## 根因

`coros-mcp.StoredAuth` 的 Training Hub 部分只保存 `access_token`、`user_id`、`region` 和时间戳，
没有 Refresh Token。neurun 只检查认证对象是否存在，不具备 Training Hub 重新登录凭据；API
明确返回失效后只能走人工重新绑定。Web 又刻意不保存明文密码，因此无法后台自动恢复。

## 修复方案

- 运动认证增加默认开启的自动鉴权选项；用户可在提交前关闭。开启后，把账号、区域和 MD5 密码摘要组成的
  密码等价重放对象用 Fernet 加密，不保存明文密码。
- 密文按用户保存为 `tokens/coros-relogin.enc`，权限 `0600`；服务级密钥只从
  `NEURUN_COROS_CREDENTIAL_KEY` 注入，不进入数据目录或日志。
- 活动、每日分析和 HRV 请求识别 `result=1019` 后按 Token 文件 singleflight 重登；成功原子写回
  新 Token，并且原请求最多重试一次。
- 高驰明确拒绝保存的凭据或重试后仍失效时删除密文并进入人工重新授权；超时、429 和 5xx 保留
  密文及 `active` 状态。
- “我的”分别展示运动与睡眠自动鉴权状态，并提供分域幂等删除入口，不影响当前 Access Token。

## 相关文件

- [src/providers/coros_credentials.py](../../src/providers/coros_credentials.py) — Fernet 加密、校验、私密写入和删除
- [src/providers/coros.py](../../src/providers/coros.py) — Training Hub 自动重登、一次重试和并发去重
- [src/config.py](../../src/config.py) — 服务密钥与用户绑定选择
- [src/web.py](../../src/web.py) — Web 同意字段、状态和删除 API
- [web/templates/setup.html](../../web/templates/setup.html) — 安全存储可用时默认开启的自动鉴权选项
- [web/templates/profile.html](../../web/templates/profile.html) — 分域自动鉴权状态与删除入口
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 凭据边界和状态机

## 验证

- 测试确认密文不包含账号、明文密码或 MD5 摘要，目录/文件权限为 `0700/0600`。
- 测试确认未配置密钥时不落盘且绑定仍成功并返回警告。
- 测试确认 `1019` 后重登并只重试一次，两个 Provider 实例并发时只登录一次。
- 测试确认第二次 `1019` 或明确拒绝删除凭据，临时网络异常保留凭据。
- 测试确认 Web 默认请求启用，用户取消时不生成密文，并允许当前用户分别删除自己的密文。
- 完整 `pytest`：`214 passed`；真实 ECS/Coros 账号验收仍需部署后完成。
