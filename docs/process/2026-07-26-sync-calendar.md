# Web 同步日历

- **日期**: 2026-07-26
- **类型**: feature

## 背景与动机

同步页原来只有单日和批量表单，用户执行后只能看到应用账号上的最后同步日期，无法判断
某个历史日期是否已经处理，也无法区分休息日、未同步日和失败日。仅通过活动或健康数据
反推同步状态会把“平台返回空数据但同步成功”的日期误判为未同步。

## 方案选择

复用 garmy 已有的 `sync_status` 表，并保留一个 neurun 自有指标类型
`neurun_provider_sync`。范围同步开始、成功和异常退出时，分别为范围内每一天写入
`pending`、`completed`、`failed`。这样不需要新增数据库迁移，也能准确记录空数据日。

查询时仍聚合旧的 garmy 指标状态与活动、日健康数据，兼容升级前数据库。旧数据只有落库
证据而没有整体完成标记时显示为“部分完成”，避免做过度推断。

前端采用七列 CSS Grid，月份切换和日期选择都保持在普通文档流中。状态同时使用颜色、
格内文字、图例和可访问名称表达；点击日期只回填单日表单，不自动发起同步写操作。

## 实现步骤

1. 更新 README 与现有项目目标、模块、数据流、Web 部署设计，定义状态语义和 API 契约。
2. 增加失败测试，覆盖空数据日、旧数据聚合、成功/失败范围标记、月份 API 与模板契约。
3. 在 `Storage` 增加范围标记和逐日聚合方法。
4. 在 `_do_data_sync()` 中围绕三平台同步维护 `pending/completed/failed` 标记。
5. 新增只读 `GET /api/sync/calendar?month=YYYY-MM`。
6. 在同步页增加月份日历、状态摘要、图例、日期回填及同步后刷新。
7. 执行完整测试，并在 800px 与 390px 视口检查布局和交互。

## 遇到的问题与解决

- 非 Garmin Provider 原来不写 garmy 指标级 `sync_status`，不能直接拿旧表作为完整覆盖证明；
  增加 Provider 级保留指标后，三平台使用同一日历契约。
- 活动为零不代表未同步，因此完成状态必须来自同步流程标记，而不是数据条数。
- 完整长页面截图在极窄视口的预览缩放不可靠，改用首屏截图与 DOM 实际宽度共同确认；
  390px 下页面 `scrollWidth` 与视口一致，七列日期格约 45px。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/01-project-goals.md](../design/01-project-goals.md)、[docs/design/04-modules.md](../design/04-modules.md)、[docs/design/05-data-flow.md](../design/05-data-flow.md)、[docs/design/13-sae-deployment.md](../design/13-sae-deployment.md)
- Bugfix: 不适用，本次为新增同步可见性能力
