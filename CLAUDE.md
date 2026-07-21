# CLAUDE.md — neurun Project Instructions

## Project Overview

neurun is a Python CLI app for Garmin data sync, AI coaching, and training knowledge base.
Built on the `garmy` library. See [README.md](README.md) and [docs/design/](docs/design/) for full context.

## Documentation Sync Rule (MANDATORY)

**每次代码变更后，必须同步更新文档，这是一条硬性规则：**

### 1. 技术设计文档 — [docs/design/](docs/design/)

以下情况必须更新 `docs/design/` 下的对应子文档（按模块选择）：
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
- [docs/design/](docs/design/) — <文档同步更新了什么>

## 验证
<如何确认修复有效>
```

### 4. 变更日志 — [docs/CHANGELOG.md](docs/CHANGELOG.md)

**每次改动必须追加 CHANGELOG 条目**，按日期倒序，使用 [Keep a Changelog](https://keepachangelog.com/) 格式。

#### 分类标签

| 标签 | 适用场景 |
|------|---------|
| **Added** | 新增功能、模块、接口、能力 |
| **Changed** | 行为变更、参数调整、重构、接口修改 |
| **Fixed** | Bug 修复 |
| **Removed** | 废弃/删除的功能 |

#### 条目格式

每条包含：改动描述 + 影响范围 + 关联文件（链接到具体文档）。

示例：
```markdown
- **改动描述**: 新增 `NEURUN_HOME` 环境变量，设后所有相对路径基于此目录解析。
- **影响范围**: config.py, fastmcp.json
- **关联文档**: [docs/design/04-modules.md](docs/design/04-modules.md)
```

#### 索引规则

- Bug 修复 → CHANGELOG 标注 `Fixed`，同时创建 `docs/bugfixes/YYYY-MM-DD-<描述>.md`
- 新功能 → CHANGELOG 标注 `Added`，关联对应 `docs/design/` 子文档
- 大型重构 → CHANGELOG 标注 `Changed`，关联 design/ 子文档 + 过程记录

## AI System Compatibility Rule (MANDATORY)

**所有 CLI 命令 / 参数调整必须考虑 AI 系统（OpenClaw 等）的调用便利性，同步更新 MCP 能力：**

### 原则

- **CLI 接口设计优先考虑可脚本化**：每个命令都应能被 AI agent 通过 shell 直接调用，避免交互式 prompt（如 `input()`、`confirm()`）阻塞 AI 调用链路
- **参数命名清晰、一致**：使用 `--kebab-case` 长选项，提供 `-s` 短选项，参数含义自解释，减少 AI 猜测成本
- **输出格式机器可读**：关键输出使用结构化格式（JSON、CSV、固定列宽表格）；人类可读的 rich 美化输出通过 `--output json` 切换
- **错误信息包含可操作提示**：exit code 非零时 stderr 输出应包含明确的错误原因和建议操作，方便 AI 自动重试或修复

### MCP 同步

**每次 CLI 命令变更（新增、修改、删除子命令或参数）时，必须同步更新项目 MCP server 的工具定义：**

- 新增命令 → 对应新增 MCP tool
- 修改参数 → 同步更新 tool 的 inputSchema
- 删除命令 → 移除对应 tool
- 确认所有 tool description 准确描述用途和参数，AI 模型依赖这些描述来做 tool selection

### 检查清单

命令调整后确认：
- [ ] 命令可通过一行 shell 调用完成（无交互步骤）
- [ ] 有 `--output json` 或等效的机器可读输出选项
- [ ] 错误信息 self-contained，无需人工解读
- [ ] MCP tool 定义已同步更新

## Code Conventions

- Python 3.12+，类型标注
- CLI 入口：[src/main.py](src/main.py)，使用 `argparse` + `rich` 美化输出
- 配置管理：[src/config.py](src/config.py)，环境变量驱动
- 数据持久化：SQLite（通过 garmy LocalDB）
- 记忆存储：Markdown + YAML Front Matter（`memory/` 目录）
- Commit message 使用中文，描述清晰

## Testing Rule (MANDATORY)

**每次代码改动必须编写或更新单元测试。**

| 改动类型 | 测试要求 |
|---------|---------|
| 新增模块/函数 | 必须写对应测试 |
| Bug 修复 | 先写复现测试 → 再修复 → 确认测试通过 |
| 重构 | 确保已有测试通过，必要时更新测试 |
| 配置/文档变更 | 不强制 |

测试目录：[tests/](tests/)，使用 `pytest`。

**运行测试**：提交前必须通过 `pytest`（零失败）。

## Process Documentation Rule

**大型改动（跨模块重构、架构调整、新能力引入）必须记录开发过程。**

创建 `docs/process/YYYY-MM-DD-<主题>.md`，包含：

```markdown
# <主题>

- **日期**: YYYY-MM-DD
- **类型**: feature / refactor / architecture

## 背景与动机
<为什么要做这个改动>

## 方案选择
<考虑过哪些方案，为什么选当前方案，舍弃了什么>

## 实现步骤
<逐步记录实现过程>

## 遇到的问题与解决
<踩坑记录，方便后续回顾>

## 关联文档
- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/](../design/)
- Bugfix: [docs/bugfixes/](../bugfixes/)（如有）
```

小型改动（单文件修复、参数调整）不强制过程记录。

## Post-Update Review (MANDATORY)

**每次改动完成后、commit 前，必须执行以下 5 项一致性检查：**

1. **Design 同步检查**: 改动涉及的技术决策、接口变更、数据模型变更是否已在 `docs/design/` 对应子文档中反映？如有出入，先更新 design 再 commit。
2. **README 同步检查**: 改动是否影响用户可见行为（CLI 命令、环境变量、输出格式、目录结构）？若是，README 是否已对应更新？
3. **CHANGELOG 检查**: 本次改动是否已按分类（Added/Changed/Fixed/Removed）追加到 `docs/CHANGELOG.md`？
4. **测试检查**: 是否为新代码编写了测试？`pytest` 是否零失败？
5. **过程记录检查**: 若为大型改动，是否创建了 `docs/process/` 过程记录？

> 上述 5 项全部通过后，方可 commit。

## Pre-Commit Checklist

每次提交前确认（Post-Update Review 5 项 + 补充项）：

- [ ] **Design 同步**: `docs/design/` 下的对应子文档已同步更新（无出入）
- [ ] **README 同步**: `README.md` 相关部分已更新（如有用户可见变更）
- [ ] **CHANGELOG**: `docs/CHANGELOG.md` 已按分类追加条目
- [ ] **测试**: 新代码有对应测试，`pytest` 零失败
- [ ] **过程记录**: 大型改动已创建 `docs/process/` 记录（如适用）
- [ ] **Bug 修复**: 已记录到 `docs/bugfixes/`（如适用）
- [ ] **MCP 同步**: tool 定义已同步更新（如有 CLI 变更）
- [ ] **无遗留**: 无 debug print / console.log / 临时注释
