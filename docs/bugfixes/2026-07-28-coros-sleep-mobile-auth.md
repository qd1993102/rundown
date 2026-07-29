# Bug: Coros 已绑定用户无法同步睡眠数据

- **发现日期**: 2026-07-28
- **修复日期**: 2026-07-28
- **严重程度**: major
- **影响范围**: Coros 绑定、每日健康同步、存量用户重新授权、历史睡眠补齐

## 现象

Coros 用户可以同步活动、静息心率和 HRV，但本地每日健康记录中的总睡眠、深睡和
REM 始终为空。已有用户即使再次执行普通同步也不会补齐睡眠。

## 根因

Coros 睡眠来自独立的 Mobile API，需要与 Training Hub token 不同的
`mobile_access_token`。项目绑定时显式使用 `skip_mobile=True`，因此所有现有用户凭据都
缺少睡眠权限；`CorosHealth` 也没有调用依赖库的 `fetch_sleep()` 或将睡眠阶段映射到
`DailyHealth`。

此外，Web 原先拒绝所有已激活用户再次进入绑定流程，非 Garmin 健康入库遇到已有日期时
只跳过、不更新。即使补上 Mobile token，存量用户也没有重新授权入口，历史健康记录也
无法原地补齐。

## 修复方案

- Coros 绑定在同一次账号密码提交中同时申请 Training Hub 与 Mobile token，仍不保存
  明文密码；Mobile 授权失败时保留活动授权。
- `CorosHealth.fetch_health_range()` 按日期范围批量读取睡眠，映射总睡眠、深睡、REM
  和占比，并在 `extra` 保留睡眠评分、清醒及小睡分钟数。
- 睡眠 token 缺失、过期刷新失败或接口异常时只降级睡眠能力，继续同步活动、RHR 和
  HRV；刷新后的 Mobile token 回写当前 neurun 用户私有 token 文件。
- “我的”页面仅向 Coros 用户展示睡眠权限状态和重新授权入口；已激活用户只能对原
  Coros 平台重新授权，不能借此切换数据源。
- 非 Garmin 健康重同步改为按范围获取并合并更新已有日期，只用本次实际取得的值覆盖，
  避免 Mobile 接口临时失败时用零值清空历史睡眠。

## 相关文件

- [src/providers/coros.py](../../src/providers/coros.py) — Mobile token、睡眠批量读取与统一模型映射
- [src/main.py](../../src/main.py) — 已有健康记录的非破坏性合并更新
- [src/web.py](../../src/web.py) — 存量 Coros 用户受限重新授权和睡眠能力状态
- [web/templates/setup.html](../../web/templates/setup.html) — Coros 睡眠重新授权流程
- [web/templates/profile.html](../../web/templates/profile.html) — 睡眠权限状态和入口
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — Coros 健康能力与降级契约

## 验证

- 绑定测试确认调用 Coros 登录时启用 Mobile 登录，并将 Mobile token 与加密刷新载荷
  保存到用户私有目录。
- 构造 450 分钟睡眠，确认映射为 7.5 小时，并正确计算深睡和 REM 小时及占比。
- 模拟 Mobile 睡眠不可用，确认已有 RHR 正常返回且同步不抛错。
- 先写入 RHR、再普通重同步睡眠，确认同一天补齐睡眠并保留原 RHR。
- 确认已激活 Coros 用户可重新授权，而普通已激活用户仍不能换绑平台。
- 运行完整 `pytest`，确认零失败。
