# Bug: YAML !!python/tuple 序列化导致日报 Web 页面空白

- **发现日期**: 2026-08-20
- **修复日期**: 2026-08-20
- **严重程度**: major
- **影响范围**: Web 日报仪表盘、所有通过 `build_memory_file` 写入的日报文件

## 现象
通过 `http://127.0.0.1:8080/?date=2026-08-15` 访问日报，页面显示空白（无内容）。API `/api/dashboard?date=2026-08-15` 返回 `{"has_data": false}`。

## 根因
`build_memory_file` 使用 `yaml.dump()` 序列化 front matter，Python tuple 会被序列化为 `!!python/tuple` 标签。但 `parse_front_matter` 使用 `yaml.safe_load()` 读取，`safe_load` 禁止解析 Python 特定标签，导致 YAML 解析抛出 `ConstructorError` 并回退到空 `{}`。`_dashboard_data` 检测到 `front_matter` 为空后返回 `{"has_data": false}`。

`running_analysis` 模块生成的 `segments` 和 `quality_flags` 字段包含 tuple 类型，触发了此问题。

## 修复方案
1. `build_memory_file`: 改用 `yaml.safe_dump()` + `_sanitize_for_yaml()` 递归将 tuple 转 list，杜绝 `!!python/tuple` 标签的产生
2. `parse_front_matter` → `_parse_yaml_safe()`: 优先 safe_load，失败时回退到 `yaml.load(Loader=yaml.Loader)` 并同样调用 `_sanitize_for_yaml` 转换，兼容残留的旧文件
3. 重写 `2026-08-15.md` 日报文件，去除残留的 `!!python/tuple` 标签

## 相关文件
- [src/memory.py](src/memory.py) — `build_memory_file` 改用 safe_dump + 新增 `_sanitize_for_yaml`；`parse_front_matter` 改用 `_parse_yaml_safe` 回退策略
- [data/rd_321b036cac9d99d8d5a92ab4fc9701bf/memory/auto/daily/2026-08-15.md](data/rd_321b036cac9d99d8d5a92ab4fc9701bf/memory/auto/daily/2026-08-15.md) — 重写去除 `!!python/tuple` 标签

## 验证
- `parse_front_matter` + `build_memory_file` 往返读写正常，无 `!!python` 标签残留
- `_dashboard_data` 对 `2026-08-15` 返回 `has_data: true`，睡眠/恢复/HRV 数据正常解析
- 全仓库 grep 无其他 `!!python` 残留文件
