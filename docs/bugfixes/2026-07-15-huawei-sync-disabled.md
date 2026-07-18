# Bug: Huawei sync 认证后直接退出

- **发现日期**: 2026-07-15
- **修复日期**: 2026-07-15
- **严重程度**: major
- **影响范围**: Huawei 活动列表、详情和本地同步

## 现象

Huawei `rundown auth` 成功后，执行 `rundown sync` 仍只显示尚未接入提示，不发送数据请求。

## 根因

`cmd_sync` 对 Huawei 分支硬编码提前返回，`HuaweiActivities` 的列表和详情方法也仍抛出 `NotImplementedError`。

## 修复方案

接入 Huawei `/healthkit/v2/activityRecords`：列表使用毫秒起止时间，详情使用 `activityRecordId` 查询参数。将 `activitySummary.dataSummary` 中的距离、心率、卡路里和海拔字段映射为 `ActivityData`，并让 Huawei 与 Coros 共用非 Garmin 标准入库流程。每日健康数据在字段口径确认前返回空，不影响活动同步。

## 相关文件

- [src/providers/huawei.py](../../src/providers/huawei.py) — 活动 API 客户端、兼容解析和详情查询
- [src/main.py](../../src/main.py) — Huawei 接入通用 Provider 同步流程
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 记录端点、参数和字段映射

## 验证

单元测试覆盖活动列表参数、嵌套字段映射、毫秒时长计算和详情查询。真实用户目录验证了认证、路径隔离以及 Huawei API 端点参数校验；CrewPals 刷新接口当前返回 404，待服务恢复后完成真实数据回归。
