# Bug: Coros 自动鉴权默认勾选状态显示不清晰

- **发现日期**: 2026-07-31
- **修复日期**: 2026-07-31
- **严重程度**: minor
- **影响范围**: Coros 首次绑定、运动数据重新授权、睡眠数据重新授权

## 现象

Coros 绑定与重新授权表单的“Token 失效后自动鉴权”选项看起来没有默认勾选。服务端已经配置
`NEURUN_COROS_CREDENTIAL_KEY`，能力接口也报告安全存储可用。

## 根因

复选框位于 `.form-group` 内，会继承面向账号文本输入框的宽度、内边距、背景和边框。模板虽然设置了
`checked`，但已有的 `.check-row` 复选框样式没有挂到对应 `label`，勾选标记因错误样式而不清晰。
重新授权分支还在最终统一初始化前额外调用一次 `selectProvider('coros')`，造成重复能力请求。

## 修复方案

- 将 Coros 自动鉴权标签接入专用 `check-row` 样式，明确重置复选框的尺寸、内边距、外边距和勾选色。
- 移除重新授权分支中的提前 Provider 初始化，所有页面只在脚本末尾初始化一次。
- 保留安全边界：只有能力接口确认服务端配置加密密钥时才启用并默认勾选；缺少密钥或查询失败时仍关闭。

## 相关文件

- [web/templates/setup.html](../../web/templates/setup.html) — 修正复选框渲染并消除重复初始化
- [tests/test_web.py](../../tests/test_web.py) — 固化专用样式与单次初始化契约
- [docs/product/data-source-sync.md](../product/data-source-sync.md) — 补充可见勾选状态验收规则
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 记录样式隔离与初始化路径
- [README.md](../../README.md) — 说明首次绑定和重新授权页的显示行为

## 验证

- 回归测试先在旧模板上失败，修复后通过。
- 服务启动时实际读取到 `NEURUN_COROS_CREDENTIAL_KEY`，安全存储能力为启用状态。
- 模板契约确认复选框使用专用样式，且重新授权不再提前重复调用 Provider 初始化。
- 完整测试结果见本次变更交付记录。
