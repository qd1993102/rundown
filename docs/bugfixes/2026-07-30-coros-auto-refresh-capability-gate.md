# Bug: Coros 首次绑定无法默认开启或勾选自动鉴权

- **发现日期**: 2026-07-30
- **修复日期**: 2026-07-30
- **严重程度**: major
- **影响范围**: Coros 首次绑定、Training Hub 自动鉴权选项初始化

## 现象

用户首次选择 Coros 时，“允许 Training Hub Token 失效后自动鉴权”没有默认勾选，而且复选框不可
操作；页面提示自动鉴权不可用，即使服务端已经配置凭据加密密钥。

## 根因

设置页通过 `GET /api/profile` 查询 `coros_secure_credential_storage`。该接口面向已完成数据源绑定的
资料页；首次绑定用户尚无 active 数据源时请求失败或无法提供能力状态，前端将缺失字段解释为
`false`，从而取消勾选并禁用复选框。绑定能力与个人资料读取错误地共用了不同生命周期的门禁。

## 修复方案

- 增加只读 `GET /api/setup/capabilities`，仅要求有效 neurun 应用会话，返回非敏感的安全存储能力。
- 设置页改用绑定能力接口初始化自动鉴权；密钥存在时默认勾选并允许用户取消，缺少密钥时才禁用。
- 保留 `/api/profile` 的资料与已绑定状态职责，不为修复首次绑定而放宽其业务边界。

## 相关文件

- [src/web.py](../../src/web.py) — 增加绑定前能力接口
- [web/templates/setup.html](../../web/templates/setup.html) — 切换自动鉴权能力查询来源
- [tests/test_registration.py](../../tests/test_registration.py) — 覆盖未绑定、无密钥和未登录门禁
- [tests/test_web.py](../../tests/test_web.py) — 固化前端接口契约
- [docs/product/data-source-sync.md](../product/data-source-sync.md) — 回写首次绑定交互与验收标准
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 记录能力接口和认证边界

## 验证

- 服务端配置 Fernet key 时，未绑定用户读取能力返回 200 与 `true`。
- 未配置 key 时返回 200 与 `false`；未登录请求返回 401。
- 设置页不再调用 `/api/profile` 初始化自动鉴权。
- 完整 `pytest`：`217 passed`，并通过 Python、内联 JavaScript 和 diff 检查。
