# 产品文档索引

`docs/product/` 维护 neurun 各用户旅程的产品真相源，回答“产品应当怎样工作”。
技术实现、接口和数据结构由配套的 `docs/design/` 文档维护。

## 用户旅程

| 产品文档 | 配套技术设计 | 状态 |
|---|---|---|
| [data-source-sync.md](data-source-sync.md) — Garmin、Coros、Huawei 绑定、区域一致性与数据同步 | [12-multi-platform.md](../design/12-multi-platform.md) | 已确认并实现 |
| [training-experience.md](training-experience.md) — 训练主界面、训练方案、执行反馈与交互式调整 | [training-system.md](../design/training-system.md) | 产品方向已确认，尚未实现 |
| [contact-community.md](contact-community.md) — 站内联系我们入口与内测交流群二维码 | [contact-community.md](../design/contact-community.md) | 已确认并实现 |

## 维护规则

1. 新功能或新的用户旅程在实现前必须先确定产品文档所有者，并与配套技术设计同步评审。
2. 已有用户旅程的优化、Bug 修复和行为调整必须更新原产品文档，不另建互相平行的临时方案。
3. 产品文档维护用户问题、范围、流程、状态、交互规则和验收标准；不记录模块、类、表结构等实现细节。
4. 技术设计维护架构、模块职责、数据模型、接口、迁移、安全边界和测试方案。
5. `docs/process/` 只记录实际开发过程，`README.md` 只描述已经可用的用户能力，`docs/CHANGELOG.md` 记录已经发生的仓库变更。
