# Bug: 训练页脚本被游离 async 中断，草稿一直停留在“请求中”

- **发现日期**: 2026-08-13
- **修复日期**: 2026-08-13
- **严重程度**: critical
- **影响范围**: 训练页（/training）整个前端脚本；方案建立向导、草稿生成/轮询、激活确认、反馈与调整全部不可用

## 现象

登录 `qd1993102@126.com` 后打开训练页，页面永远停留在“正在读取实时训练方案…”，
草稿（方案建立向导）一直处于“请求中”，没有任何交互入口；浏览器控制台报
`ReferenceError: async is not defined`。

## 根因

`web/templates/training.html` 中 `activateDraft` 函数与 `intensityZoneLabel` 常量之间
残留了一行孤立的 `async` 语句：

```html
async
const intensityZoneLabel={...};
```

`async` 单独成行在语法上是合法的标识符表达式语句，`node --check` 不会报错；但浏览器
执行到该语句时因 `async` 未定义抛出 ReferenceError，整个经典脚本被中断。而页面初始化
代码（`load(!draftTaskId)`）位于脚本末尾，永远没有机会执行，因此：
- `load()` 从不被调用，`#app` 停留在初始“正在读取实时训练方案…”加载块；
- 底部恢复 `localStorage` 草稿任务并轮询 `pollDraftTask` 的逻辑同样不执行，草稿永远
  显示为“请求中/生成中”，不生成也不失败。

## 修复方案

删除 `training.html` 中游离的 `async` 行，使脚本正常解析执行到初始化代码。

## 相关文件

- [web/templates/training.html](../../web/templates/training.html) — 删除游离 `async` 语句
- [tests/test_web.py](../../tests/test_web.py) — 同步修正过时断言（“跳过并生成/提交并生成”
  已随模板演进移除，改为“生成草稿”）

## 验证

- Playwright 无头浏览器加载 `/training`：控制台无 `pageerror`，向导正常渲染；
- 完整走通“草稿 → 修改约束 → 重新生成草稿”流程，任务状态轮询至成功，
  “草稿已生成，请先查看依据和首周结构。”；
- `pytest` 全量零失败（504 passed, 1 xfailed）。
