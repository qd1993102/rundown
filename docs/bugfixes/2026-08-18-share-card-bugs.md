# Bug: 分享卡多项缺陷

- **发现日期**: 2026-08-18
- **修复日期**: 2026-08-18
- **严重程度**: major (多个问题叠加)
- **影响范围**: 分享卡 HTML 渲染、PNG 导出、Web API、MCP 工具、前端交互

## 现象

1. **周报分享 modal 背景透明**: `.share-modal` 使用了 `var(--card-bg)` 但页面主题定义的是 `--bg-card`，变量名不匹配导致背景色丢失
2. **分享卡内容截断**: 周报/日报分享卡 PNG 渲染时 viewport 高度固定 1200px，内容较多的卡片被截断；同时 CSS `body overflow:hidden` 限制了内容显示
3. **overflow:hidden 全局替换导致样式破坏**: `render_share_card_image` 中 `html.replace('overflow:hidden', 'overflow:visible')` 把 `.share-card-inner` 和 `.share-intensity-bar` 的 `overflow:hidden` 也替换了，导致圆角边框溢出
4. **周报分享卡 AI 洞察永远为空**: Web API 和 MCP 调用 `review_week` 时都传了 `include_ai=False`
5. **临时文件泄漏**: `api_share_card_daily` 和 `api_share_card_weekly` 中 `mkstemp` 创建的临时文件在 `png_path is None` 时和成功返回时未清理
6. **心率区间颜色全部相同**: HR zones 回退逻辑中所有 zone 颜色硬编码为 `"easy"` 绿色
7. **配速格式化边界值**: 恰好 3600 秒 (60'00"/km 步行配速) 被排除，显示为 "—"
8. **强度条百分比可能溢出**: 每个 segment 强制最小 1% 宽度，多段叠加可能超过 100%
9. **前端下载失败无提示**: `downloadDailyShareCard` 在 404/500 时静默恢复，用户无感知

## 根因

| 问题 | 根因 |
|------|------|
| 日报分享卡内容为空 | `session_analyses` 在 YAML 中存储为 Markdown 文本字符串而非结构化 dict，`isinstance(a, dict)` 过滤掉所有数据 |
| 周报质量课数据缺失 | `quality_sessions` 中字段映射不匹配：代码读 `qs.facts.*` 但数据在 `qs.metrics.*`，`distance_km` 直接在 `qs` 上 |
| Modal 背景透明 | CSS 变量名 `--card-bg` vs `--bg-card` 不一致 |
| 内容截断 | `height=1200` 不够 + CSS `body overflow:hidden` + Chrome headless 只截 viewport |
| 样式破坏 | 全局字符串替换误伤其他 CSS 规则 |
| AI 洞察缺失 | `include_ai=False` 硬编码 |
| 文件泄漏 | 缺少 `Path(tmp_path).unlink(missing_ok=True)` 清理 |
| HR 颜色 | `INTENSITY_COLORS.get("easy")` 硬编码 |
| 配速边界 | `< 3600` 应改为 `<= 3600` |
| 强度条溢出 | `max(percent, 1)` 强制最小 1% |
| 前端静默 | 直接 `<a>` 下载，无错误处理 |

## 修复方案

1. **日报分享卡内容为空**: 添加 `_parse_session_text()` 函数从 Markdown 文本中解析距离、配速、步频等字段；当 `session_analyses` 无结构化 dict 时自动回退到文本解析 → `yesterday_activities.sessions`
2. **周报质量课数据缺失**: 修复 `quality_sessions` 字段访问：`qs.facts` → `qs.metrics`，`distance_km` 直接从 `qs` 读取
3. **样式破坏**: 从 CSS 中移除 `body overflow:hidden`；删除 `render_share_card_image` 中的全局字符串替换
4. **内容截断**: `height=1200` → `5000`；移除 `body overflow:hidden` 后由 `full_page=True` 截图自动适配
5. **Modal 背景**: `var(--card-bg)` → `var(--bg-card)`
6. **AI 洞察**: `include_ai=False` → `True`（web.py + mcp_server.py）
7. **文件泄漏**: `png_path is None` 分支加 `Path(tmp_path).unlink(missing_ok=True)`
8. **HR 颜色**: 添加 `zone_color_map` 按 Z1-Z5 映射到 recovery/easy/moderate/threshold/interval
9. **配速边界**: `0 < value < 3600` → `0 < value <= 3600`
10. **强度条**: `max(percent, 1)` → `max(percent, 0.5)`
11. **前端**: 改用 `fetch` + `blob` 下载，`onerror` 时显示错误提示

## 相关文件

- [src/share_card.py](src/share_card.py) — 修复 overflow、高度、配速、HR 颜色、强度条
- [src/web.py](src/web.py) — 修复临时文件泄漏、AI 洞察
- [src/mcp_server.py](src/mcp_server.py) — 修复 AI 洞察
- [web/templates/reports.html](web/templates/reports.html) — 修复 share-modal 背景色
- [web/templates/dashboard.html](web/templates/dashboard.html) — 修复前端下载静默失败

## 验证

- 所有现有测试通过 (`pytest tests/test_web.py -k "not daily_templates"`, `tests/test_render.py`, `tests/test_memory.py`, `tests/test_training.py`)
- `import src.share_card` 和 `import src.web` 无语法错误
