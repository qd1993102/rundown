# Bug: 用户目录继承全局账号凭证

- **发现日期**: 2026-07-15
- **修复日期**: 2026-07-15
- **严重程度**: major
- **影响范围**: `RUNDOWN_HOME` 多用户配置隔离

## 现象

Huawei 用户目录的 `.env` 未设置运动平台账号时，运行日志出现另一个全局 Garmin 用户的脱敏邮箱。

## 根因

`get_config()` 即使检测到 `RUNDOWN_HOME`，仍使用 `~/.rundown/.env` 补充缺失变量，造成用户配置跨目录继承。

## 修复方案

显式设置 `RUNDOWN_HOME` 时仅加载该目录 `.env`，不再加载全局 `.env`。真正需要共享的配置由进程环境显式注入。

## 相关文件

- [src/config.py](../../src/config.py) — 隔离用户目录配置加载
- [docs/design/12-multi-platform.md](../design/12-multi-platform.md) — 补充配置隔离规则

## 验证

复现测试创建相互冲突的用户 `.env` 和全局 `.env`，确认 Huawei 用户配置不再包含全局账号密码。
