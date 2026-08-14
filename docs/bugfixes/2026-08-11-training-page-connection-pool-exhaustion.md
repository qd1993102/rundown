# Bug: 训练页读取历史活动时连接池耗尽导致页面一直加载

- **发现日期**: 2026-08-11
- **修复日期**: 2026-08-11
- **严重程度**: major
- **影响范围**: 训练页实时方案与历史训练事实读取

## 现象

`/training` 页面长期停留在“正在读取实时训练方案”，服务日志出现
`QueuePool limit ... connection timed out`。

## 根因

活动查询首选 SQL 在旧用户数据库缺少 `distance_meters` 列时抛错，异常路径没有关闭
SQLAlchemy session；随后 fallback 查询持续占用连接，最终耗尽连接池。

## 修复方案

为活动主表首选查询、fallback 查询，以及 `activity_details`、`activity_splits`、
`activity_summary_facts` 查询增加 `finally: session.close()`，确保 SQL 异常也释放连接。
重启服务清理已耗尽的旧进程和连接池。

## 相关文件

- [src/memory.py](../../src/memory.py) — 修复活动查询异常路径的 session 回收。
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 服务运行与健康检查约定。

## 验证

- `pytest -q tests/test_memory.py tests/test_training.py` → 81 passed。
- 服务以 `MCP_PORT=8080` 启动并输出 Uvicorn running。
