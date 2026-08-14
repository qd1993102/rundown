# 日报引用运动员能力与近期负荷背景

- **日期**: 2026-08-12
- **类型**: feature

## 背景与动机

草稿流程沉淀了运动员能力画像（`TrainingService.capacity_profile(D)`：可持续周跑量、长距离、
近期配速、历史已证能力与中断背景、负荷边界）和近期负荷事实（前 4–8 完整自然周聚合）。
日报此前只有 ACWR 数值（7d acute / 28d chronic / 比值），缺少“这个数字放在个人能力画像与
近期负荷趋势里意味着什么”的解释背景。用户确认：只做“日报正文 + AI 洞察引用背景”，
日报直接读取训练域已暴露的只读 `GET /api/training/capacity` 口径，不重复实现能力计算。

## 方案选择

- **边界**：日报只消费训练域只读能力画像作为解释背景，不写回、不参与方案计算；
  能力画像计算仍由训练域负责，报告域不重复实现（符合“报告只观察与建议”原则）。
- **数据流**：日报生成编排层（CLI/Web 共用 `_do_daily_sync`、MCP `generate_local_report`）
  按 readiness 门禁调用 `build_capacity_athlete_context(config, target, omitted_sections)`，
  投影稳定字段写入 Front Matter `athlete_context`；`review-daily-training` 的 `daily_facts`
  注入同一份背景，单次调用内解释。
- **共享工厂**：从 Web 路由把活动加载器与上下文装配固化为 `training_service_factory.py`，
  Web 的 `training_service()` 委托工厂构建，CLI/MCP 复用同一口径，避免三入口两套加载逻辑。
- **门禁**：`training_load` 被省略的受限版不读取训练域（`status=unavailable`）；
  配置缺失、读取异常同样显式不可用，不阻断日报生成、不编造能力数值。
- **快照语义**：报告固化生成时点画像与 `facts_cutoff`；重新生成历史日期按
  `capacity_profile(D)` 口径读取，不引用 `D` 之后活动；生成后画像变化不反写历史日报。

## 实现步骤

1. 更新产品真相源 [报告中心与日报](../product/daily-report.md#311-能力与近期负荷背景引用已确认)：
   范围、门禁、快照语义、验收标准（42–45）。
2. 更新设计文档：记忆系统（`athlete_context` 字段与门禁）、训练系统（能力接口消费方）、
   模块设计（`training_service_factory`）。
3. 新建 `src/training_service_factory.py`：`build_training_service`（抽取自 web.py 的
   `load_week`/`load_setup`，保留 `storage_factory` 注入点以兼容既有测试）、
   `project_capacity_profile`（0 值转 None）、`build_capacity_athlete_context`（门禁 + 投影）。
4. Web：`training_service()` 委托工厂；CLI：`_do_daily_sync` 构造并传入 `athlete_context`；
   MCP：`generate_local_report` 同口径注入。
5. `memory.py`：`generate_daily_report` 新增 `athlete_context` 参数并落盘（写入器对
   `training_load` 省略做防御性强制不可用），正文“负荷状态”章节渲染紧凑能力背景行；
   `MemoryStore` 委托层透传参数。
6. `coach.py`：`daily_facts` 注入 `athlete_context`；`review-daily-training` SKILL 增加
   只读引用与“不编造能力数值”规则；`render.py` 负荷卡片增加能力参考行与 CSS。
7. 测试：工厂门禁/投影、memory 写入与正文渲染、CLI `_do_daily_sync` 透传、render 展示、
   coach fact pack 注入。

## 遇到的问题与解决

- **测试注入点失效**：既有 web 测试 monkeypatch `web.Storage`，工厂持有自己的 `Storage`
  引用导致 `coverage` 退回 unknown。解决：`build_training_service` 增加
  `storage_factory` 注入点，Web 传入自身 `Storage` 引用，保持既有测试行为不变。
- **受限版误传**：编排层传了可用背景但 readiness 已省略 `training_load` 时，写入器仍
  强制覆盖为不可用，避免“背景可信但负荷结论缺失”的不一致。
- **0 值能力**：无历史用户 `capacity_profile` 返回 `weekly_km=0`，投影时转为 `None`，
  防止把“没有历史事实”展示成能力数值。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/daily-report.md](../product/daily-report.md)
- Design: [docs/design/memory-system.md](../design/memory-system.md)、
  [docs/design/training-system.md](../design/training-system.md)、
  [docs/design/04-modules.md](../design/04-modules.md)
