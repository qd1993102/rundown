# Bug: ECS 发布健康检查窗口短于启动耗时导致新/旧版本均被误判失败

- **发现日期**: 2026-08-20
- **修复日期**: 2026-08-20
- **严重程度**: major
- **影响范围**: ECS 发布流程（`scripts/deploy-ecs.sh` 健康检查与自动回滚）、Web 启动恢复

## 现象

发布 `02071ca` 时日志显示 Uvicorn 已在 21:06:11 成功监听 8080，但部署脚本仍输出
"错误：neurun 新版本启动后健康检查未通过"，随后回滚旧版本（`ef4919b`）并输出
"错误：旧版本已恢复，但健康检查仍未通过"，发布任务以失败退出，尽管服务实际已经启动。

## 根因

`wait_for_health` 只轮询 20 次、每次失败后 `sleep 1`（约 20 秒窗口）。而 Web 启动时
`cmd_serve` 会先同步恢复所有用户的 SQLite 备份（本次 33 个用户实际恢复 + 10 个跳过），
再初始化 FastMCP/Uvicorn 监听 8080；从 21:05:52 进程启动到 21:06:11 就绪约 19 秒，
紧贴甚至超过 20 秒窗口，健康检查在窗口内持续 `Connection refused`，被误判失败。
旧版本同样包含启动恢复逻辑，回滚重启后同样超过窗口，所以回滚健康检查也失败。
这不是应用崩溃，也不是旧版本损坏，而是健康检查预算小于实际启动耗时。

另外，无条件"每次启动把备份覆盖回 data.db"本身会把上次同步之后写入数据库的本地改动
回滚掉，并让启动时间随用户数线性增长。

## 修复方案

1. 健康检查轮询次数从固定 20 改为默认 60（环境变量 `HEALTH_CHECK_ATTEMPTS` 可调，
   非正整数时明确报错退出），给 40+ 用户的启动恢复留足余量。
2. 启动恢复改为按需执行：仅当 data.db 缺失、为空、损坏（`PRAGMA quick_check` 非 ok）
   或备份比当前库新时才从备份恢复；正常情况直接使用现有 data.db。
   既保留崩溃/损坏后的备份兜底，又消除每次启动的全量回滚与线性耗时。

## 相关文件

- [scripts/deploy-ecs.sh](../scripts/deploy-ecs.sh) — 健康检查窗口 20 → 60，新增 `HEALTH_CHECK_ATTEMPTS`
- [src/main.py](../src/main.py) — `_restore_all_users` 改为按需恢复，新增 `_restore_reason` / `_db_is_healthy`
- [docs/design/13-sae-deployment.md](../design/13-sae-deployment.md) — 部署设计同步健康检查窗口与按需恢复语义

## 验证

- `bash -n scripts/deploy-ecs.sh` 语法通过；`tests/test_main.py` 新增按需恢复用例、
  `tests/test_packaging.py` 新增环境变量断言，相关测试全部通过
- 重新发布后观察 journalctl 与 `curl -fsS http://127.0.0.1:8080/healthz` 返回的新 release SHA
