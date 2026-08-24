# Bug: 训练首页重复读取重叠活动窗口导致接口响应过慢

- **发现日期**: 2026-08-25
- **修复日期**: 2026-08-25
- **严重程度**: major
- **影响范围**: `GET /api/training/home` 训练首页读模型、训练服务活动与 setup 上下文装配

## 现象

请求 `GET /api/training/home` 响应时间过长。接口本身不访问 Garmin 等外部服务，但一次读模型组装会读取多组相互重叠的活动日期窗口，并反复解析 setup 上下文，导致 SQLite 查询和本地事实构建重复发生。

## 根因

训练首页同时需要计划周、本周活动、近 42 天配速活动、近 55 天能力活动和历史训练状态。原实现让这些调用分别进入活动加载器；同一请求内重复执行活动范围查询、逐活动训练分析/摘要事实查询以及训练历史装配。`TrainingService.home()` 还会在能力与配速投影中多次读取同一目标日 setup 上下文。

## 修复方案

1. 在训练服务工厂中建立请求级活动快照，首次查询使用所需的最大日期范围，后续重叠窗口只从快照切片。
2. 活动训练分析和 `activity_summary_facts` 只在快照建立时 enrich 一次，并对返回对象做深拷贝，避免业务投影修改缓存。
3. 训练历史状态复用同一请求级快照；setup 上下文按目标日期缓存并在返回时深拷贝。
4. 对训练方案和记忆 Markdown 增加带 `mtime + size` 校验的进程级解析缓存，写入时主动失效；保持 `/api/training/home` 响应字段、同步新鲜度和数据口径不变。

## 相关文件

- [src/training_service_factory.py](../../src/training_service_factory.py) — 重叠活动窗口、活动事实和历史状态的请求级快照
- [src/training.py](../../src/training.py) — setup 上下文按目标日复用
- [tests/test_training_service_factory.py](../../tests/test_training_service_factory.py) — 验证重叠窗口只触发一次活动查询
- [docs/design/training-system.md](../design/training-system.md) — 训练首页低延迟读模型约束

## 验证

- `pytest -q tests/test_training_service_factory.py tests/test_training.py tests/test_web.py`：149 passed
- `python3 -m py_compile src/training.py src/training_service_factory.py tests/test_training_service_factory.py`
- `git diff --check`
