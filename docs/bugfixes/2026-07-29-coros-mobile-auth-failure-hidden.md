# Bug: Coros 运动与睡眠认证重叠且 Mobile 失败被误显示为成功

- **发现日期**: 2026-07-29
- **修复日期**: 2026-07-29
- **严重程度**: major
- **影响范围**: 存量 Coros 用户睡眠重新授权、睡眠同步和授权失败提示

## 现象

“我的”中的“重新授权 Coros 睡眠”和“重新授权并设置自动续期”指向同一个组合登录表单，用户无法
判断需要 Training Hub 账号还是 Coros App 账号。即使 Mobile 登录失败，用户仍可能被带到同步页，
随后日志提示“当前凭据未启用睡眠权限”。手机号也会被提交到只接受邮箱的 Mobile 登录。

## 根因

Training Hub 与 Mobile 使用不同账号规则、端点、Token 和重放材料，但 Web 把它们合并为一次
提交。`coros-mcp` 的组合登录还会吞掉 Mobile 异常并返回有效 Training Hub 对象，使 neurun 无法
区分哪个认证域成功；Mobile 重放载荷此前也跟随 token JSON 保存，没有独立的安全刷新状态。

## 修复方案

- 拆成 `POST /api/coros/auth/training` 与 `POST /api/coros/auth/sleep`；前者要求 Training Hub
  邮箱或手机号、密码和显式区域，后者只接受 Coros App 登录邮箱并继承区域。
- “我的”使用两个含义、URL 和状态均不同的按钮；任一认证失败只停留在对应表单，不改变另一域。
- Mobile 登录重放载荷用 Fernet 加密保存为 `coros-mobile-relogin.enc`，从 `coros-auth.json`
  移除；Mobile `1019` 在用户域内重放并只重试一次，不写 coros-mcp 全局认证文件。
- Mobile 业务错误码、缺少访问凭据和网络异常继续转换为不包含账号、密码或 token 的可操作提示。

## 相关文件

- [src/providers/coros.py](../../src/providers/coros.py) — 显式执行 Mobile 登录并记录脱敏失败原因
- [src/web.py](../../src/web.py) — 分域认证、状态和独立删除接口
- [src/providers/coros_credentials.py](../../src/providers/coros_credentials.py) — Mobile 重放载荷加密存储
- [web/templates/setup.html](../../web/templates/setup.html) — 分域账号说明、区域和默认自动鉴权
- [web/templates/profile.html](../../web/templates/profile.html) — 两套状态、按钮和关闭入口
- [tests/test_providers.py](../../tests/test_providers.py) — 覆盖 Mobile 成功、失败和活动 token 保留
- [tests/test_registration.py](../../tests/test_registration.py) — 覆盖重新授权失败不返回成功
- [docs/product/data-source-sync.md](../product/data-source-sync.md) — 补充失败状态与验收标准
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — ��确两阶段认证和错误契约

## 验证

- 模拟 Mobile 业务错误码，确认 Training Hub token 与重登凭据仍持久化且错误原因可读取。
- 模拟存量 Coros 用户睡眠认证失败，确认返回 HTTP 400 并停留在睡眠表单，连接仍为 active。
- 确认手机号在调用 Mobile Provider 前被拒绝；两个按钮和接口 scope 均不重复。
- 确认 Mobile `1019` 只更新 Mobile Token，明确拒绝只删除睡眠密文。
- 使用真实账号重新授权，确认页面展示高驰 Mobile 的实际结果；成功后再执行批量同步验证
  睡眠落库。完整 `pytest` 为 `214 passed`，真实账号验收仍待用户执行。
