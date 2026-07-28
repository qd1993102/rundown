# Bug: Coros 注册绑定缺少 coros_mcp 运行模块

- **发现日期**: 2026-07-28
- **修复日期**: 2026-07-28
- **严重程度**: major
- **影响范围**: 默认安装、Docker 部署、Web Coros 注册后绑定

## 现象

用户在 Web 注册后选择 Coros 并提交账号密码，绑定接口返回：

```text
No module named 'coros_mcp'
```

Garmin 和 Huawei 路径不受影响。

## 根因

Coros Provider 在运行时导入 `coros_mcp`，但 `pyproject.toml` 把 `coros-mcp` 放在可选
依赖组 `project.optional-dependencies.coros`。README 和 Dockerfile 都执行默认的
`pip install .`，没有安装该 extras。Docker 构建仍能成功、Web 也能启动，直到用户实际
绑定 Coros 才触发延迟导入并暴露 `ModuleNotFoundError`。

此外，`coros-mcp` 使用 Git URL 安装，而原 Docker 基础镜像没有安装 `git`；仅把依赖移动到
默认组仍会令镜像在依赖安装阶段失败。

## 修复方案

- 将 `coros-mcp` 移入默认 `project.dependencies`，使本地默认安装和 Docker 镜像都包含
  Coros 绑定所需模块。
- Docker 基础系统依赖加入 `git`，支持 pip 安装 Git URL 依赖。
- Coros 登录捕获缺失模块并抛出包含重新安装命令的可操作错误，避免再次误判为账号密码错误。
- 增加打包契约测试，确保 Coros 运行依赖不会再次退回可选 extras。

## 相关文件

- [pyproject.toml](../../pyproject.toml) — 将 `coros-mcp` 调整为默认运行依赖
- [Dockerfile](../../Dockerfile) — 安装 Git URL 依赖所需的 `git`
- [src/providers/coros.py](../../src/providers/coros.py) — 输出可操作的缺依赖错误
- [tests/test_packaging.py](../../tests/test_packaging.py) — 固化默认依赖与 Docker 构建契约
- [docs/design/07-dependencies.md](../design/07-dependencies.md) — 同步依赖分层
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 同步 Coros Provider 运行约束

## 验证

- 修复前，打包契约测试确认 `coros-mcp` 不在默认依赖并失败。
- 修复后，打包契约测试确认默认安装声明 Coros 依赖，Docker 同时安装 `git`。
- Coros 缺依赖路径返回包含 `pip install -e .` 的可操作提示。
- 运行完整 `pytest`，确认零失败。
