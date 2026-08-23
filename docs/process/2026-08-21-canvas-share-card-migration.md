# 分享卡浏览器 Canvas 迁移

- **日期**: 2026-08-21
- **类型**: architecture

## 背景与动机

日报和周复盘分享卡原先由服务端把 HTML 交给 Playwright、Chromium 或系统 Chrome 截图。该实现把纯展示能力扩散到 CLI、MCP、Python 运行时和 ECS 部署，增加浏览器下载、系统库、字体与运行目录维护成本。确认后的产品边界是：分享卡只属于 Web 展示能力，报告数据接口继续稳定，PNG 在登录用户浏览器内生成且不上传。

## 方案选择

采用共享的 `web/static/share-card.js`：先从既有日报或周复盘 JSON 构建白名单 ViewModel，再用 Canvas 2D 专用绘制并编码 PNG。没有采用 DOM 截图或 `html2canvas`，避免页面隐私字段、布局状态、跨域资源和字体加载进入图片合同。旧图片路由不直接删除，保留鉴权后固定 `410 Gone` JSON，给旧调用方明确迁移提示。

## 实现步骤

1. 保持 `GET /api/dashboard` 与 `GET /api/reports/weekly` 的 URL、鉴权和字段语义不变。
2. 新增共享 Canvas 模块，输出 375 逻辑宽、3 倍像素密度的 PNG，并提供文件分享与下载回退。
3. Dashboard 使用已加载日报 JSON；报告中心周复盘使用已加载归档 JSON，不调用生成接口。
4. 旧分享卡图片路由收敛为鉴权后的 `410` JSON，未登录继续返回 `401`。
5. 删除服务端图片模块、`render.py` 浏览器截图出口、CLI PNG 输出和 MCP 图片工具。
6. 删除生产 Playwright extra、ECS Chromium 安装、浏览器环境变量与冒烟检查。
7. 补充接口合同、模板钩子、CLI/MCP 下线和部署依赖测试。

## 遇到的问题与解决

报告中心的日报列表只包含摘要，无法直接构建白名单分享卡；点击日报分享时读取稳定的 `/api/dashboard?date=...` JSON 后再绘制。周复盘列表已经持有完整归档对象，因此直接缓存对象并绘制，避免调用 POST 重新生成。预览 Blob URL 在替换、关闭弹窗和页面卸载时释放；下载使用的临时 URL 在点击后立即异步释放。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Product: [docs/product/share-card.md](../product/share-card.md)
- Design: [docs/design/share-card.md](../design/share-card.md)
