# Bug: 生成日报后按钮仍显示"生成日报"，需再点一次才出现"查看日报"

- **发现日期**: 2026-08-15
- **修复日期**: 2026-08-15
- **严重程度**: major
- **影响范围**: 报告中心日报页（`web/templates/reports.html`）生成日报后的状态按钮

## 现象

点击"生成日报"成功后，状态区按钮仍显示"生成日报"（可再次点击），不会切换为"查看日报"；
再点第二次后才会出现"查看日报"。

## 根因

`genReport` 成功路径存在**异步竞态**：

```js
if (j.status === 'ok') {
  msg.innerHTML = '✅ 已生成...';
  load();                       // 异步刷新列表（未 await），内部会调用 renderDailyStatus
} else { ... }
renderDailyStatus();            // 同步执行：此时列表还没刷新，selectedDaily() 为空 → 走 checkReadiness()
```

- `load()` 是异步的，`renderDailyStatus()` 同步先执行：`selectedDaily()` 尚未包含新生成的日报，
  走 `checkReadiness()` 分支（显示"生成日报"按钮）；
- `checkReadiness()` 又发起异步 readiness 请求，其结果**晚于** `load()` 完成后的
  `renderDailyStatus()`（已正确显示"查看日报"），把按钮**覆盖回**"生成日报"。

## 修复方案

- 成功路径改为 `await load()`：等待列表刷新完成（`selectedDaily()` 拿到新日报）后，
  `loadDailyPage` 内部的 `renderDailyStatus()` 直接渲染"查看日报"，不再有竞态窗口；
- 移除成功路径末尾同步的 `renderDailyStatus()`（避免触发 checkReadiness 覆盖）；
- 失败路径（`else` / `catch`）仍调用 `renderDailyStatus()` 刷新为可重试状态，按钮不会卡在禁用。

## 相关文件

- [web/templates/reports.html](../../web/templates/reports.html) — `genReport` 成功路径 `await load()`，移除竞态调用
- [tests/test_web.py](../../tests/test_web.py) — 模板断言：成功路径必须 `await load()`

## 验证

- Playwright：mock 完整生成流程——初始显示"生成日报" → 点击生成 → 状态立即切换为"查看日报"，
  等待 1.5 秒仍稳定（不再有 readiness 请求发出、按钮不被覆盖）；
- `node --check` JS 语法通过；全量 pytest 零失败。
