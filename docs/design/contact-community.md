# 技术设计 — 联系入口与可更新群二维码

> 版本: v1.0 · 日期: 2026-07-29
> 状态: 已实现并完成自动化验证
> 产品真相源: [contact-community.md](../product/contact-community.md)

## 1. 设计范围

本设计为 neurun Web 页面增加统一联系组件和受会话保护的二维码读取端点。组件继续使用
当前零构建、无前端框架的 HTML/CSS/JavaScript 体系，不引入新依赖，也不新增 CLI 或 MCP tool。

## 2. 方案选择

联系组件由 `src/web.py::_html_response()` 在已有 ARMS RUM 注入流程旁统一注入，而不是复制到
每个模板。这样登录用户访问当前页面或后续新增页面时都会得到相同行为，且四份业务模板不会漂移。

二维码使用稳定 URL `/contact/qr`。服务端按以下优先级读取图片：

1. `<data_dir>/contact-wechat.jpg`：部署环境可直接替换的持久化覆盖文件；
2. `web/assets/contact-wechat.jpg`：随版本发布的默认图片。

首次实现已将用户提供的 `IMG_0233.JPG` 裁出 720 × 720 的原始二维码区域作为默认图片，未重绘
二维码模块。该图片注明 2026-08-04 前有效，因此上线前
应优先把更新后的二维码放入持久化覆盖路径；以后替换文件不修改 HTML 或路由。

## 3. HTTP 与权限

| 方法 | 路径 | 权限 | 行为 |
|---|---|---|---|
| `GET` | `/contact/qr` | 已登录应用账号 | 返回当前二维码 JPEG；缺失时返回 404 |

- 响应使用 `Content-Type: image/jpeg`、`Cache-Control: no-store` 和
  `Content-Disposition: inline; filename="neurun-wechat-group.jpg"`。
- 未登录请求按图片端点语义返回 401，不重定向到 HTML 登录页。
- 路径由服务端固定拼接，客户端不能传入文件路径，避免任意文件读取。
- 图片响应不经过 `_html_response()`，不会注入脚本。

## 4. HTML 注入边界

`_html_response(html, user)` 仅当 `user` 非空时注入联系组件；登录和注册页面保持现状。
注入顺序为页面原内容、联系组件、ARMS RUM 脚本、`</body>`。组件需要稳定且唯一的 DOM id，
如果页面已经包含该组件标记则跳过，避免重复注入。

主要语义结构：

```html
<aside data-contact-widget>
  <button aria-expanded="false" aria-controls="contactPanel">联系我们</button>
  <section id="contactPanel" role="dialog" aria-modal="false" hidden>
    <img src="/contact/qr" alt="NeuRun 内测交流群二维码">
    <a href="/contact/qr" download="neurun-wechat-group.jpg">保存二维码</a>
  </section>
</aside>
```

## 5. 状态机与事件

组件维护两个前端状态：

- `preview`: hover/focus 产生的临时打开状态；
- `pinned`: click 产生的保持打开状态。

`pinned` 优先于 `preview`。按钮点击切换 `pinned`；支持 hover 的设备通过 `pointerenter` /
`pointerleave` 切换 `preview`；`focusin` 打开预览；外部 pointer、再次点击或 `Escape` 清空状态。
每次渲染同步 `hidden` 和 `aria-expanded`。关闭事件由键盘触发时将焦点返回按钮。

图片 `error` 事件隐藏图片和保存操作，展示可读错误状态；图片 `load` 后恢复正常状态。

## 6. CSS 与响应式约束

- 浮动按钮和弹层是平台级 overlay，允许使用 `position: fixed`；其定位只依赖视口和安全区。
- 320–640 px：按钮底边位于 `calc(92px + env(safe-area-inset-bottom))`，避开现有底部导航；
  卡片宽度使用 `min(320px, calc(100vw - 24px))`。
- 641 px 以上：按钮距右、下各 24 px，卡片锚定在按钮上方。
- hover 样式和自动预览只写在 `@media (hover:hover) and (pointer:fine)` 中。
- 使用现有主题变量，并为登录设置页等变量不完整的页面提供局部 fallback。
- `prefers-reduced-motion: reduce` 下关闭位移和渐变动画。

## 7. 测试方案

- 路由测试：已登录返回 JPEG 与 no-store；未登录返回 401；覆盖文件优先于默认文件；缺失返回 404。
- 注入测试：登录 HTML 含单个组件，匿名 HTML 不含组件，重复处理不重复注入。
- 合同测试：组件包含 `aria-expanded`、`aria-controls`、`Escape`、外部关闭、移动安全区和
  fine-pointer hover media query。
- 回归测试：运行完整 `pytest`，确保现有页面路由、RUM 注入和四页导航合同不变。

2026-07-29 实现验证：`tests/test_web.py` 31 项通过，完整 `pytest` 146 项通过；注入脚本通过
Node 语法检查，默认 JPEG 已确认是 720 × 720 且裁切区域完整。当前环境的浏览器安全策略阻止
访问本地预览地址，因此没有把浏览器自动截图作为完成证据；320 px 与桌面布局由 CSS/DOM 合同
测试覆盖，正式发布后仍应进行一次真机扫码验收。

## 8. 运维更新

运营侧更新二维码时，把新的 JPEG 原子替换到 `<data_dir>/contact-wechat.jpg`，保持服务账号可读。
由于响应禁用缓存，刷新页面后立即读取新文件。替换前应先确认新图可扫码，并记录图片的失效日期；
本期不引入自动过期检测或管理后台上传。
