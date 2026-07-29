# Bug: Coros 睡眠重新授权表单被重置为 Garmin

- **发现日期**: 2026-07-29
- **修复日期**: 2026-07-29
- **严重程度**: major
- **影响范围**: 存量 Coros 用户睡眠重新授权页面和提交平台

## 现象

用户从“我的”进入 Coros 睡眠重新授权页后，标题和按钮显示 Coros，但表单仍显示
“Garmin 区域”和“账号（邮箱）”。提交后服务端收到 `provider=garmin`，返回当前账号不支持
换绑平台，用户无法取得 Mobile 睡眠权限。

## 根因

页面读取 `rebind=coros` 后先调用 `selectProvider('coros')`，但脚本末尾的通用首次绑定初始化
又无条件调用 `selectProvider('garmin')`。后一次调用覆盖了字段可见性、账号标签和提交使用的
Provider 状态。原测试只检查重新授权代码字符串存在，没有约束最终初始化表达式。

## 修复方案

页面底部只保留一次带条件的 Provider 初始化：重新授权模式选择 Coros，普通首次绑定选择
Garmin。新增回归契约，禁止初始化区再次出现无条件 `selectProvider('garmin')`。

## 相关文件

- [web/templates/setup.html](../../web/templates/setup.html) — 根据重新授权参数初始化最终 Provider
- [tests/test_web.py](../../tests/test_web.py) — 固定初始化顺序与最终 Provider 契约
- [docs/product/data-source-sync.md](../product/data-source-sync.md) — 补齐重新授权交互和验收标准
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 明确页面 Provider 真相源

## 验证

- 单元测试确认初始化区使用 `rebindProvider` 条件表达式，且不存在无条件 Garmin 初始化。
- 浏览器打开 `/setup?rebind=coros&scope=training`，确认显示 Training Hub 邮箱或手机号与 Coros
  区域；打开 `scope=sleep`，确认只显示 Coros App 邮箱。两个页面均隐藏 Garmin 区域并固定 Coros。
- 运行完整 `pytest`，确认零失败。
