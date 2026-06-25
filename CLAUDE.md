# CLAUDE.md — Rundown Project Instructions

## Project Overview

Rundown is a Python CLI app for Garmin data sync, AI coaching, and training knowledge base.
Built on the `garmy` library. See [README.md](README.md) and [docs/design.md](docs/design.md) for full context.

## Documentation Sync Rule (MANDATORY)

**每次代码变更后，必须同步更新文档，这是一条硬性规则：**

### 1. 技术设计文档 — [docs/design.md](docs/design.md)

以下情况必须更新 `docs/design.md`：
- 新增或修改模块、类、函数签名
- 架构调整（数据流、模块职责、依赖关系变更）
- 配置项新增或变更
- 命令行接口（CLI）新增或修改
- 数据模型 / Schema 变更
- 外部依赖变更

### 2. 用户文档 — [README.md](README.md)

以下情况必须更新 `README.md`：
- CLI 命令新增、修改或删除（子命令、参数、选项）
- 环境变量新增或变更
- 安装/配置步骤变化
- 输出格式或目录结构变化

### 3. Bug 修复文档 — [docs/bugfixes/](docs/bugfixes/)

**每次修 bug 必须记录**，创建独立文件 `docs/bugfixes/YYYY-MM-DD-<简短描述>.md`，包含：

```markdown
# Bug: <一句话描述>

- **发现日期**: YYYY-MM-DD
- **修复日期**: YYYY-MM-DD
- **严重程度**: critical / major / minor
- **影响范围**: <哪些模块/功能受影响>

## 现象
<bug 的具体表现，包含错误信息、异常行为>

## 根因
<为什么会发生，技术层面的根本原因>

## 修复方案
<做了什么修改，为什么这样做>

## 相关文件
- [src/xxx.py](src/xxx.py) — <改了什么>
- [docs/design.md](docs/design.md) — <文档同步更新了什么>

## 验证
<如何确认修复有效>
```

### 4. 变更日志 — [docs/CHANGELOG.md](docs/CHANGELOG.md)

每次发布或重要变更时，在 CHANGELOG 中追加条目，按日期倒序。

## Code Conventions

- Python 3.12+，类型标注
- CLI 入口：[src/main.py](src/main.py)，使用 `argparse` + `rich` 美化输出
- 配置管理：[src/config.py](src/config.py)，环境变量驱动
- 数据持久化：SQLite（通过 garmy LocalDB）
- 记忆存储：Markdown + YAML Front Matter（`memory/` 目录）
- Commit message 使用中文，描述清晰

## Pre-Commit Checklist

每次提交前确认：
- [ ] `docs/design.md` 已同步更新
- [ ] `README.md` 相关部分已更新
- [ ] bug fix 已记录到 `docs/bugfixes/`
- [ ] `docs/CHANGELOG.md` 已追加条目
- [ ] 代码通过 `pytest`（如有测试）
- [ ] 无遗留的 debug print / console.log
