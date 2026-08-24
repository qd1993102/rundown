# Bug: 分享卡教练分析文字过长被省略

- **发现日期**: 2026-08-24
- **修复日期**: 2026-08-24
- **严重程度**: major
- **影响范围**: 浏览器 Canvas 分享卡的 AI 教练分析区

## 现象

教练分析文字较长时，分享卡只绘制固定行数，尾部显示省略号，用户无法在导出的 PNG 中看到完整分析。

## 根因

Canvas 文本布局此前为普通分析和教练结论分别设置了 2 行和 3 行上限，达到上限后主动丢弃剩余字符并追加省略号。结构化摘要的 70/90 字符限制属于 AI 输出 Schema 合同，不是布局截断。

## 修复方案

教练分析按 Canvas 实测宽度完整换行，测量得到的行数直接参与分享卡高度计算和绘制，不再对教练文字追加省略号。AI 输出在 Schema 阶段限制为 70/90 字符，前端仅对绕过 Schema 的异常或历史字段保留防御性上限。活动名称等单行事实标签继续使用单行省略，以保持列表布局稳定；整张卡仍受 2048 px 逻辑高度上限保护，超限时整体失败而不导出残缺图片。

## 相关文件

- [web/static/share-card.js](../../web/static/share-card.js) — 完整换行教练分析，并保留单行活动标签截断。
- [tests/test_web.py](../../tests/test_web.py) — 覆盖长教练文字完整绘制且不出现省略号。
- [docs/product/share-card.md](../product/share-card.md) — 更新长文本交互规则。
- [docs/design/share-card.md](../design/share-card.md) — 更新 Canvas 布局合同。

## 验证

运行 `pytest -q tests/test_web.py`，并通过 Node Canvas mock 回归测试确认长教练摘要的首段和末段均进入绘制调用且不生成省略号。
