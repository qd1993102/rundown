# Bug: 跑步已入库仍显示部分完成且今日标识与状态重叠

- **发现日期**: 2026-07-27
- **修复日期**: 2026-07-27
- **严重程度**: minor
- **影响范围**: Web 同步日历状态判定与窄屏今日日期展示

## 现象

某日已经存在跑步记录，但同步日历仍显示“部分完成”。在今日日期格中，日期数字的下划线
还会与第二行“部分完成”等状态文案视觉重叠。

## 根因

日历原先优先采用 Provider 范围标记和 garmy 指标级状态。Garmin 活动使用独立直同步链路，
活动成功入库后可能保留 `activities=pending`，从而把日期降级为 `partial`。同时，旧活动表
没有持久化标准化运动类型，无法稳定区分跑步与其他运动。

今日样式使用带 `text-underline-offset` 的文字下划线；日期格高度较小时，下划线会进入第二行
状态文本占用的空间。

## 修复方案

三平台同步统一补齐并写入 `activities.activity_type`。同步日历优先识别当天跑步记录：新数据
按标准类型识别，升级前数据按活动名称中的“跑步”或 `run` 兼容识别；一旦存在跑步记录，
该日直接判为 `synced`。API 增加逐日 `running_count` 供前端展示。

今日标识改为日期按钮外轮廓，并设置 `aria-current="date"`，不再占用日期格内部文本空间。
月份摘要同步由“完整同步”改为“已同步”，避免暗示健康指标也全部成功。

## 相关文件

- [src/main.py](../../src/main.py) — 三平台活动入库时补齐并保存标准运动类型
- [src/storage.py](../../src/storage.py) — 跑步记录优先的日历聚合与旧数据兼容
- [web/templates/sync.html](../../web/templates/sync.html) — 跑步数量及今日外轮廓样式
- [docs/design/04-modules.md](../design/04-modules.md) — 数据字段与状态优先级
- [docs/design/05-data-flow.md](../design/05-data-flow.md) — 活动写入和日历聚合流程
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — Web API 与页面契约

## 验证

- 单元测试覆盖标准 `activity_type` 和旧活动名称两种跑步识别方式，且在范围标记失败时仍判定为已同步。
- 同步测试覆盖已有活动补写 `activity_type`。
- 模板测试确认今日日期使用外轮廓和 `aria-current`，并移除原日期下划线。
- 执行完整 `pytest`，要求零失败。
