# Bug: 点击"训练前说明"报 Cannot set properties of null (setting 'disabled')

- **发现日期**: 2026-08-15
- **修复日期**: 2026-08-15
- **严重程度**: major
- **影响范围**: 训练页异步操作按钮——"训练前说明"、"比赛策略"、方案修订/反馈提交、提案确认/拒绝

## 现象

点击训练页"训练前说明"按钮，控制台报错：

```
Uncaught (in promise) TypeError: Cannot set properties of null (setting 'disabled')
    at HTMLButtonElement.loadSessionBrief
```

按钮在请求完成后不会复位（`disabled` 无法关闭），请求结果面板也因异常中断未渲染。

## 根因

`loadSessionBrief` 等异步回调在 `await` **之后**访问 `event.currentTarget`：

```js
async function loadSessionBrief(event){
  event.currentTarget.disabled = true;
  try{ ... await api(...) ... }
  finally{ event.currentTarget.disabled = false }   // ← await 后 currentTarget 已为 null
}
```

浏览器标准行为：`event.currentTarget` 只在事件派发期间有效，异步回调跨过 `await` 后
`event` 的 `currentTarget` 被重置为 `null`，赋值即抛 TypeError。同类问题共 5 处：
`loadSessionBrief`、`loadRaceStrategy`（`finally`）、`submitSchemeRevision`、
`submitFeedback`（`catch` 内 `event.submitter`）、`approveProposal` / `rejectProposal`
（`catch` 内 `event.currentTarget`）。

## 修复方案

异步回调在**同步阶段**捕获按钮引用，后续全部使用捕获的变量：

```js
async function loadSessionBrief(event){
  const button = event.currentTarget;   // 同步阶段捕获，不受 await 影响
  button.disabled = true;
  try{ ... }
  finally{ if(button) button.disabled = false }
}
```

`event.submitter` 同理改为 `const submitter = event.submitter`；`approveProposal` 中
`body` 构造的 `event.currentTarget.dataset.version` 一并改用捕获的 `button`。

## 相关文件

- [web/templates/training.html](../../web/templates/training.html) — 6 个异步回调改为同步捕获按钮引用
- [tests/test_web.py](../../tests/test_web.py) — 新增断言：模板不得再出现 `await` 后访问
  `event.currentTarget.disabled` / `event.submitter.disabled` 的模式

## 验证

- Playwright：点击"训练前说明"成功渲染面板、无 console 错误；重复点击按钮 `disabled` 正常复位；
- `node --check` JS 语法通过；全量 pytest 零失败。
