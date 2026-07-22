# Web 同步与日报生成解耦

- **日期**: 2026-07-22
- **类型**: refactor

## 背景与动机

Web 的 `POST /api/sync` 复用了 CLI 的“同步 + 日报”总流程，导致用户只想补齐运动数据时也会自动创建或覆盖日报。日报页虽然已有“生成日报”按钮，实际仍通过 `skip_sync=true` 调用同一个同步端点，两个动作的职责和 API 语义没有真正分开。

另外，在线 AI 教练通过 `prompts/coach.md` 生成洞察后，旧流程只更新日报 Front Matter，没有用新洞察重新渲染 Markdown 正文，造成不同展示面可能看到不同内容。

## 方案选择

选择保留 CLI `neurun daily` 的一站式能力，同时在核心层抽出纯数据同步函数，并为 Web 提供独立的显式日报端点：

- `POST /api/sync` 只执行数据拉取、SQLite 持久化和备份；
- `POST /api/reports` 只读取本地 SQLite，生成用户选择日期的日报；
- 本地日报通过 SQLite 中唯一的 `user_id` 确定数据归属，不为生成日报访问运动平台；
- `coach.md` 洞察成功后，以该洞察再次调用日报生成器，使结构化字段和正文一致；
- 在线 AI 不可用时保留首次生成的本地规则洞察，不阻断日报能力。

没有沿用 `skip_sync` 复用 `/api/sync`，因为它会继续混淆端点职责；也没有在同步完成后自动排队生成日报，因为这仍违背用户自主触发的产品边界。

## 实现步骤

1. 更新 README 与 Web 设计，先明确同步和日报的独立职责及降级行为。
2. 增加失败测试，覆盖同步无日报副作用、显式日报端点和 AI 洞察正文重渲染。
3. 从 `_do_daily_sync()` 抽取 `_do_data_sync()`，Web 同步改用纯同步函数。
4. 增加 `POST /api/reports`，日报页按钮改用新端点。
5. 增加 `Storage.get_local_user_id()`，让显式日报生成只依赖用户本地 SQLite。
6. 更新页面文案、CHANGELOG，并执行完整回归测试。

## 遇到的问题与解决

- 旧的 `MemoryStore.generate_daily_report()` 委托签名没有透传 `ai_insight`。同步扩展其签名，将在线 AI 洞察传给 Writer 进行第二次一致性渲染。
- 用户数据库理论上只属于一个平台用户。读取本地 `user_id` 时显式拒绝包含多个 ID 的异常数据库，避免猜测日报归属。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/04-modules.md](../design/04-modules.md)、[docs/design/13-sae-deployment.md](../design/13-sae-deployment.md)
- Bugfix: 不适用，本次按产品行为变更记录为 `Changed`
