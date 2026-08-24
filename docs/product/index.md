# 产品文档索引

`docs/product/` 维护 neurun 各用户旅程的产品真相源，回答“产品应当怎样工作”。
技术实现、接口和数据结构由配套的 `docs/design/` 文档维护。

## 用户旅程

| 产品文档 | 配套技术设计 | 状态 |
|---|---|---|
| [account.md](account.md) — 应用账号：邀请码注册、邮箱密码登录、退出登录、我的页修改密码 | [04-modules.md](../design/04-modules.md) | 已确认并实现 |
| [user-data-archive.md](user-data-archive.md) — 服务器管理员通过独立脚本按用户昵称打包脱敏关联数据为 tar；覆盖重名消歧、敏感数据排除与机器可读结果 | [user-data-archive.md](../design/user-data-archive.md) | 已确认并实现；不纳入 neurun 主命令或 MCP |
| [data-source-sync.md](data-source-sync.md) — Garmin、Coros、Huawei 绑定与同步；Strava OAuth 活动同步（Proposed） | [12-multi-platform.md](../design/12-multi-platform.md) | 三平台已确认并实现；Strava v1 待产品确认 |
| [daily-report.md](daily-report.md) — 报告中心、日报、周复盘、数据完整性、目标进程与下一阶段建议 | [memory-system.md](../design/memory-system.md) | 报告主线、日报、独立周复盘与报告建议到训练待确认提案的接口/深链已实现；完整自动衔接与真实 Provider 补同步链路待验收 |
| [share-card.md](share-card.md) — 日报与周复盘的浏览器 Canvas 分享卡、隐私白名单和 PNG 导出 | [share-card.md](../design/share-card.md) | Canvas 已实现，待真实浏览器 E2E 验收；MCP/CLI 分享卡下线，旧图片 URL 过渡为 410 |
| [training-experience.md](training-experience.md) — 训练方案、处方执行、执行反馈与交互式调整 | [training-system.md](../design/training-system.md) | 报告结论落地的边界已确认；训练方案、Workout Steps v2、个体化动态配速 v1、报告建议待确认提案入口与 `goal_intent` API/存储同步已实现；真实 Provider 逐段执行匹配、真实训练数据验收及配速后续能力待完成 |
| [contact-community.md](contact-community.md) — 站内联系我们入口与内测交流群二维码 | [contact-community.md](../design/contact-community.md) | 已确认并实现 |

## 维护规则

1. 新功能或新的用户旅程在实现前必须先确定产品文档所有者，并与配套技术设计同步评审。
2. 已有用户旅程的优化、Bug 修复和行为调整必须更新原产品文档，不另建互相平行的临时方案。
3. 产品文档维护用户问题、范围、流程、状态、交互规则和验收标准；不记录模块、类、表结构等实现细节。
4. 技术设计维护架构、模块职责、数据模型、接口、迁移、安全边界和测试方案。
5. `docs/process/` 只记录实际开发过程，`README.md` 只描述已经可用的用户能力，`docs/CHANGELOG.md` 记录已经发生的仓库变更。
