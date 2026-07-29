# Coros Mobile 睡眠接入

- **日期**: 2026-07-28
- **类型**: feature

## 背景与动机

neurun 已能同步 Coros 活动、RHR 和 HRV，但绑定过程只获取 Training Hub token，导致依赖
库已经支持的 Mobile 睡眠数据无法进入每日健康模型。存量用户没有重新授权入口，已有
日期的健康记录也无法通过普通同步补齐。

## 方案选择

短期沿用现有 Coros Provider，在一次账号密码提交中补充 Mobile token，原因是它可以复用
当前用户目录、绑定界面、SQLite 和同步链路，对现有用户的操作变化最小。官方 COROS MCP
需要独立 OAuth 回调、多用户授权状态和后台定时调用能力，暂不在本次范围内。

Mobile API 不是稳定的公开 Web API，因此睡眠被设计为独立可降级能力：获取失败不能撤销
Training Hub 授权，也不能阻断活动与其他健康指标。

## 实现步骤

1. 用回归测试固定 Mobile 登录、token 持久化和睡眠阶段映射。
2. 绑定时启用 Mobile 登录，并提供 `sleep_available` 能力检查。
3. 按日期范围批量读取睡眠并映射到 `DailyHealth`。
4. 将非 Garmin 健康入库改为非破坏性合并更新，使历史日期可以补齐睡眠。
5. 为已激活 Coros 用户增加仅限原平台的重新授权入口。
6. 同步 README、多平台设计、Bug 记录和 CHANGELOG。

## 遇到的问题与解决

- 现有 token 没有明文密码，无法后台静默升级：要求用户从“我的”重新输入一次 Coros
  账号密码，不保存明文。
- 睡眠 token 刷新发生在依赖库内部：拉取完成后把更新后的认证模型重新写回当前 neurun
  用户 token 文件，避免只更新依赖库的全局存储。
- 已有健康记录会阻止历史补齐：改为按字段合并，只覆盖本次存在的值，避免临时缺失造成
  数据倒退。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/12-multi-platform.md](../design/12-multi-platform.md)
- Bugfix: [docs/bugfixes/2026-07-28-coros-sleep-mobile-auth.md](../bugfixes/2026-07-28-coros-sleep-mobile-auth.md)
