# 设计方案 — 前端设计系统

> 版本: v1.0 · 日期: 2026-08-20
> 状态: Proposed — 设计审计完成，方案已产出，待评审确认后进入实现
> 审计范围: training.html / dashboard.html / reports.html / sync.html / profile.html / output/*.html (静态日报)
> 审计框架: frontend-design skill (排版、配色、布局、签名、动效、文案、一致性)

---

## 1. 审计发现摘要

2026-08-20 对全站六个前端页面进行了统一设计审计。以下是核心发现：

**做得好的**：

- 三主题 token 系统 (`fresh` / `sport` / `dark`) 语义色 token 设计合理，跨页映射一致
- 底部导航栏、主题切换器、品牌标识在四个 web app 页面间完全一致
- 中文文案自然、术语一致，符合产品文档定位
- 训练页的 proposal 对比卡片和草稿日历是设计品质最高的交互组件
- 产品文档 (daily-report.md) 的 57 条验收标准覆盖了深层数据完整性逻辑

**需要改进的**：

- 全站使用同一系统字体栈，无展示/正文/数据字体区分，数值无等宽对齐
- `dark` 主题 (`#0b1120` + `#34d399`) 直接命中 AI 生成设计的默认配色模式
- `fresh` 主题 (`#e8efe9` 米绿底 + 白卡片 + 圆角阴影) 接近另一类 AI 默认
- 全站缺失签名元素——没有让用户记住"这是 neurun"的视觉锚点
- 动效几乎为零，连 Tab 切换都没有过渡
- 按钮样式在页面间不一致：`border-radius` 12px vs 20px，`font-weight` 600 vs 700
- 静态输出品牌名 "RUNDOWN" 与 web app 品牌名 "neurun" 不一致
- 全站 CSS token 定义在每个 HTML 文件中复制粘贴，无共享源

---

## 2. 设计 Token 系统

### 2.1 提取共享 Token 文件

当前 token 定义在 6 个文件中各自复制粘贴。实现时提取为独立 CSS 文件，由各页面引用：

```
web/static/
├── tokens.css          # 三主题 CSS 变量定义
├── reset.css           # 全局 reset + 基础排版
├── components.css      # 共享组件 (nav, theme-bar, btn, spinner, card)
└── utilities.css       # 工具类
```

各页面 HTML 改为 `<link rel="stylesheet" href="/static/tokens.css">`。

### 2.2 保留的三主题

三个主题 (`fresh` / `sport` / `dark`) 保留，但配色调整：

#### fresh（自然绿）

```css
[data-theme="fresh"] {
  --bg: #e8efe9;
  --bg-card: #fff;
  --text: #1a2e23;
  --text-secondary: #5a7d6e;
  --text-muted: #8aa89a;
  --accent: #2d9d6f;
  --accent-glow: rgba(45, 157, 111, .1);
  --recovery: #3b82b6;
  --performance: #22a86e;
  --sleep: #7c6fcf;
  --warning: #d4a017;
  --danger: #dc5b51;
  --hrv: #3b9fc6;
  --card-shadow: 0 2px 8px rgba(0, 0, 0, .06);
  --card-shadow-hover: 0 8px 30px rgba(0, 0, 0, .1);
  --border-subtle: #dde5df;
  --bg-subtle: #f0f4f0;
  /* 新增 */
  --surface-raised: #f6f9f6;
  --accent-strong: #1f7a52;
}
```

> 保持现有色值，追加 `--surface-raised`（用于 hover 浮层）和 `--accent-strong`（用于强调文字）。

#### sport（运动橙）

```css
[data-theme="sport"] {
  --bg: #f0f0f0;           /* 考虑改为 #e8e8e8 增加对比度 */
  --bg-card: #fff;
  --text: #171717;
  --text-secondary: #525252;
  --text-muted: #a3a3a3;
  --accent: #f15b2a;
  --accent-glow: rgba(241, 91, 42, .08);
  --recovery: #2563eb;
  --performance: #16a34a;
  --sleep: #7c3aed;
  --warning: #eab308;
  --danger: #ef4444;
  --hrv: #0891b2;
  --card-shadow: 0 2px 10px rgba(0, 0, 0, .07);
  --card-shadow-hover: 0 10px 35px rgba(0, 0, 0, .1);
  --border-subtle: #ebebeb;
  --bg-subtle: #f5f5f5;
  /* 新增 */
  --surface-raised: #fafafa;
  --accent-strong: #d1431e;
}
```

> `sport` 是三主题中最有辨识度的（橙色 = 跑步 app 语义），保留现有色值。可选：`--bg` 从 `#f0f0f0` 调整为 `#e8e8e8` 增加与白卡片的对比度。

#### dark（暗色）

**需要重设计。** 当前 `#0b1120` 深蓝黑底 + `#34d399` 荧光绿是 AI 生成设计最常见的暗色模式。

替换方向：**暖色暗底**——用深灰绿代替纯蓝黑，用琥珀/金代替荧光绿，形成"夜跑头灯下的暖光"意象：

```css
[data-theme="dark"] {
  --bg: #141a14;             /* 深灰绿底，而非纯蓝黑 */
  --bg-card: #1e261e;        /* 卡片稍亮 */
  --text: #e6e8e3;           /* 暖白文字，而非冷白 */
  --text-secondary: #9aa89a;
  --text-muted: #5a6b5a;
  --accent: #f0a030;         /* 琥珀/暖金，而非荧光绿 */
  --accent-glow: rgba(240, 160, 48, .12);
  --recovery: #6ba8e0;       /* 柔和蓝 */
  --performance: #5cb878;    /* 柔和绿 */
  --sleep: #9a8ad0;          /* 柔和紫 */
  --warning: #e8c040;
  --danger: #e86050;
  --hrv: #50b8c8;
  --card-shadow: 0 2px 10px rgba(0, 0, 0, .35);
  --card-shadow-hover: 0 10px 30px rgba(0, 0, 0, .5);
  --border-subtle: #2a342a;
  --bg-subtle: #1a201a;
  /* 新增 */
  --surface-raised: #243024;
  --accent-strong: #d08820;
}
```

> 设计依据：暖色暗底在长时间阅读（报告、训练方案）时比蓝黑底更舒适；琥珀色 accent 与跑步的"头灯/夜晚/能量"意象一致，且不会与任何市面跑步 app 撞色。

### 2.3 新增全局 Token

```css
:root {
  /* 排版 */
  --font-display: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-body: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans SC', sans-serif;
  --font-mono: 'SF Mono', 'JetBrains Mono', 'Cascadia Code', monospace;

  /* 间距 */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 12px;
  --space-lg: 16px;
  --space-xl: 24px;
  --space-2xl: 32px;

  /* 圆角 */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-xl: 22px;
  --radius-full: 9999px;

  /* 按钮统一 */
  --btn-radius: 12px;
  --btn-font-weight: 650;
  --btn-min-height: 44px;

  /* 容器 */
  --container-narrow: 560px;   /* 同步、我的 */
  --container-normal: 720px;   /* 仪表盘、报告 */
  --container-wide: 760px;     /* 训练 */

  /* 动效 */
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --duration-fast: 150ms;
  --duration-normal: 250ms;
  --duration-slow: 400ms;
}
```

---

## 3. 排版系统

### 3.1 字体分层

当前全站使用同一系统字体栈。改为三层：

| 层级 | 字体 | 用途 | 说明 |
|------|------|------|------|
| **Display** | Inter (Google Fonts, weight 600–900) | 大数字、日期、Hero 数值 | 几何感 sans-serif，数字清晰，支持 tabular figures |
| **Body** | 系统栈 + PingFang SC / Noto Sans SC | 正文、标签、按钮 | 中文优先用系统字体，西文 fallback 到 Inter |
| **Data** | SF Mono / JetBrains Mono | 配速表、心率区间、数据表格 | 等宽字体，数值对齐 |

### 3.2 等宽数字（Tabular Figures）

所有数值显示区域添加 `font-feature-settings: "tnum"`：

```css
.hero-value,
.metric-val,
.load-score,
.load-stat .val,
.session-metric .val,
.plan-val,
table td:not(:first-child) {
  font-feature-settings: "tnum";
  font-variant-numeric: tabular-nums;
}
```

> 这是改动最小、收益最大的单项改进。跑者阅读配速 `4'26"`、距离 `16.01`、心率 `132` 时，等宽数字让纵向扫描对齐，减少视觉疲劳。

### 3.3 字号层级

```css
/* 字号层级 (mobile-first, 桌面放大见媒体查询) */
.text-display-xl { font: 800 42px/1.1 var(--font-display); letter-spacing: -1.5px; }  /* 日报日期 */
.text-display-lg { font: 800 38px/1 var(--font-display); letter-spacing: -1px; }      /* Hero 大数字 */
.text-display-md { font: 700 26px/1 var(--font-display); letter-spacing: -0.5px; }    /* 指标数字 */
.text-heading   { font: 700 18px/1.3 var(--font-body); }                               /* 区块标题 */
.text-subhead   { font: 650 14px/1.4 var(--font-body); }                               /* 卡片标题 */
.text-body      { font: 400 14px/1.6 var(--font-body); }                               /* 正文 */
.text-caption   { font: 500 11px/1.4 var(--font-body); }                               /* 辅助文字 */
.text-eyebrow   { font: 800 10px/1 var(--font-body); letter-spacing: 1.5px; text-transform: uppercase; }
```

---

## 4. 组件规范

### 4.1 按钮

**统一之前**：仪表盘/报告 `border-radius: 12px`，同步/我的 `border-radius: 20px`，字重 600 vs 700。

**统一之后**：

```css
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: var(--btn-min-height);        /* 44px */
  padding: 9px 16px;
  border-radius: var(--btn-radius);         /* 12px */
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: var(--btn-font-weight);     /* 650 */
  letter-spacing: 0.2px;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  white-space: nowrap;
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}

.btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.btn.primary:hover { opacity: 0.88; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn:focus-visible {
  outline: 3px solid var(--accent-glow);
  outline-offset: 2px;
}

/* 危险按钮（我的页） */
.btn.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.btn.danger:hover {
  background: var(--danger);
  color: #fff;
}
```

### 4.2 卡片

```css
.card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);          /* 16px */
  padding: 20px;
  box-shadow: var(--card-shadow);
  border: 1px solid var(--border-subtle);
  transition: box-shadow var(--duration-normal) var(--ease-out),
              transform var(--duration-normal) var(--ease-out);
}

.card:hover {
  box-shadow: var(--card-shadow-hover);
}

.card--interactive:hover {
  transform: translateY(-2px);
  box-shadow: var(--card-shadow-hover);
}
```

### 4.3 底部导航栏

当前五页一致，保持现有设计。唯一改动：从 inline CSS 提取到 `components.css`。

### 4.4 主题切换器

当前 `theme-btn` 在五页中一致，保持现有设计。

### 4.5 品牌标识

**统一品牌名**：静态输出 (`output/*.html`) 中的 "RUNDOWN" 改为 "neurun"，logo 形式从渐变色文字改为纯色方块 + 文字（与 web app 一致）。

```html
<!-- 统一品牌标识 -->
<div class="app-brand">
  <div class="app-brand-mark">n</div>
  <span>neurun</span>
</div>
```

### 4.6 Section 标题

**当前**：使用 emoji 前缀 (`📈 训练负荷`、`🎯 今日建议`、`🤖 AI 教练洞察`)。

**改进**：用左侧色条替代 emoji 前缀，颜色编码 section 语义：

```css
.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 750;
  letter-spacing: -0.2px;
  margin-bottom: 12px;
  padding-left: 12px;
  border-left: 3px solid var(--accent);
}

.section-title--load     { border-left-color: var(--performance); }  /* 训练负荷 */
.section-title--advice   { border-left-color: var(--accent); }       /* 训练建议 */
.section-title--ai       { border-left-color: var(--sleep); }        /* AI 洞察 */
.section-title--recovery { border-left-color: var(--recovery); }     /* 恢复 */
.section-title--sleep    { border-left-color: var(--sleep); }        /* 睡眠 */
.section-title--warning  { border-left-color: var(--warning); }      /* 警告 */
```

> 技能要求："Structural devices should encode something true about the content, not decorate it." 颜色编码携带了 section 语义信息，而 emoji 是纯装饰。

---

## 5. 签名元素

### 5.1 设计目标

跑步 app 的视觉世界可以来自：跑道标记线、分段计时、心率区间色带、鞋底纹路、田径场弯道弧度。

选择 **心率区间色带环** 作为 neurun 的签名元素。理由：

- 心率区间是跑者训练的核心维度（有氧、阈值、无氧），是 neurun 区别于"只看距离配速"的通用工具的关键
- 色带环可以出现在报告 Hero 区域、训练课卡片、方案预览中，形成跨页视觉锚点
- 实现成本可控：纯 CSS/SVG，不需要图片资源

### 5.2 心率区间环规格

```
┌─────────────────────────────────┐
│  ┌─────────────────────────┐    │
│  │  ████  Z1 恢复    <120 │    │
│  │  ████  Z2 有氧  120-140│    │
│  │  ████  Z3 节奏  140-160│    │
│  │  ████  Z4 阈值  160-175│    │
│  │  ████  Z5 无氧    >175 │    │
│  └─────────────────────────┘    │
│         今日训练强度分布          │
└─────────────────────────────────┘
```

五色系统：

```css
--hr-zone-1: #60a5fa;  /* 蓝 — 恢复/热身 */
--hr-zone-2: #34d399;  /* 绿 — 有氧 */
--hr-zone-3: #fbbf24;  /* 黄 — 节奏 */
--hr-zone-4: #f87171;  /* 橙 — 阈值 */
--hr-zone-5: #ef4444;  /* 红 — 无氧 */
```

色带环以水平条形式出现，用百分比宽度表示各区间时间占比。

### 5.3 出现位置

| 页面 | 位置 | 形式 |
|------|------|------|
| 仪表盘 | Hero 区域下方，活动卡片上方 | 水平五段色条 |
| 报告页 | 日报详情训练课区域 | 水平五段色条 |
| 训练页 | 训练课执行卡片 | 水平五段色条 |
| 静态输出 | 训练课区域 | 水平五段色条 |

---

## 6. 动效系统

### 6.1 原则

- 编排一个时刻，而非散落效果（"An orchestrated moment usually lands harder than scattered effects"）
- 尊重 `prefers-reduced-motion`
- 动效时长 ≤ 400ms，不阻塞交互

### 6.2 动效清单

| # | 动效 | 触发时机 | 实现 | 优先级 |
|---|------|---------|------|--------|
| 1 | Tab 切换过渡 | 日报 ↔ 周复盘切换 | `opacity` + `transform: translateY(4px)` 交叉淡入淡出 | P0 |
| 2 | 数据揭示序列 | 报告/仪表盘加载完成 | Hero 数字→指标条→课程卡片→AI 洞察 stagger 淡入 | P1 |
| 3 | 按钮反馈 | 点击/触摸 | `transform: scale(0.97)` 瞬间回弹 | P1 |
| 4 | 同步进度动画 | 同步任务轮询中 | 进度条平滑过渡 | P2 |
| 5 | 训练方案生成进度 | 草稿生成中 | 阶段指示器脉冲动画 | P2 |
| 6 | 卡片 hover | 可交互卡片 | `translateY(-2px)` + shadow 过渡（已有） | 已有 |

### 6.3 实现参考

```css
/* Tab 切换 */
.tab-panel {
  transition: opacity var(--duration-normal) var(--ease-out),
              transform var(--duration-normal) var(--ease-out);
}
.tab-panel[hidden] {
  display: block; /* 覆盖 hidden 以支持过渡 */
  opacity: 0;
  transform: translateY(4px);
  pointer-events: none;
}

/* 数据揭示 stagger */
.reveal {
  opacity: 0;
  transform: translateY(8px);
  animation: reveal var(--duration-slow) var(--ease-out) forwards;
}
.reveal:nth-child(1) { animation-delay: 0ms; }
.reveal:nth-child(2) { animation-delay: 80ms; }
.reveal:nth-child(3) { animation-delay: 160ms; }
.reveal:nth-child(4) { animation-delay: 240ms; }

@keyframes reveal {
  to { opacity: 1; transform: translateY(0); }
}

/* 按钮按压反馈 */
.btn:active {
  transform: scale(0.97);
  transition: transform 80ms var(--ease-out);
}

/* 尊重用户偏好 */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 7. 品牌一致性

### 7.1 品牌名统一

| 位置 | 当前 | 改为 |
|------|------|------|
| 静态输出 `output/*.html` header | "RUNDOWN" | "neurun" |
| 静态输出 `output/*.html` footer | "RUNDOWN" | "neurun" |
| 静态输出 `<title>` | "RUNDOWN · ..." | "neurun · ..." |
| 静态输出 logo 形式 | 渐变色文字 | 纯色方块 + 文字（与 web app 一致） |
| Web app 四页 | "neurun" | 不变 |

### 7.2 页面 Title 统一格式

```
所有页面 <title> 格式: neurun — <页面名>

- 仪表盘: neurun — 训练日报
- 报告:   neurun — 报告中心
- 训练:   neurun — 训练方案
- 同步:   neurun — 同步
- 我的:   neurun — 我的
- 静态:   neurun · 2026-06-27 · Daily Report
```

---

## 8. 实现 Checklist

### Phase 1: 基础设施（成本低，收益高）

- [ ] **提取共享 CSS 文件**：创建 `web/static/` 目录，迁移 token、reset、组件、工具类到独立文件
- [ ] **统一按钮样式**：合并 `border-radius` (12px)、`font-weight` (650) 到 `components.css`
- [ ] **等宽数字**：给所有数值显示区域加 `font-feature-settings: "tnum"`
- [ ] **统一品牌名**：静态输出中 RUNDOWN → neurun，logo 统一为方块+文字
- [ ] **统一页面 Title**：所有页面 `<title>` 格式统一为 `neurun — <页面名>`
- [ ] **添加 `prefers-reduced-motion` 媒体查询**：全局禁用动效

### Phase 2: 视觉升级（成本中，影响面大）

- [ ] **Dark 主题重配色**：`#0b1120`/`#34d399` → `#141a14`/`#f0a030`（暖色暗底+琥珀 accent）
- [ ] **Section 标题去 emoji 化**：emoji 前缀 → 左侧色条，按语义颜色编码
- [ ] **引入 Display 字体**：Inter (Google Fonts) 用于大数字和日期
- [ ] **字号层级标准化**：统一 `.text-display-*` / `.text-heading` / `.text-body` / `.text-caption` 类
- [ ] **间距 Token 标准化**：用 `--space-*` 变量替换硬编码的 margin/padding 值

### Phase 3: 签名元素（成本中，品牌辨识度）

- [ ] **心率区间色带环**：实现五段水平色条组件，应用到仪表盘/报告/训练/静态输出
- [ ] **色带环数据接入**：从心率数据计算各区间时间占比，驱动色条宽度
- [ ] **色带环空状态**：无心率数据时显示灰色占位，标注"心率数据未同步"

### Phase 4: 动效（成本中，体验提升）

- [ ] **Tab 切换过渡**：日报 ↔ 周复盘交叉淡入淡出
- [ ] **数据揭示序列**：仪表盘/报告加载时 stagger 淡入
- [ ] **按钮按压反馈**：`:active` 缩放 0.97
- [ ] **同步进度条平滑过渡**：进度条宽度 transition
- [ ] **草稿生成脉冲指示器**：阶段指示器脉冲动画

### Phase 5: 文档与维护

- [ ] **更新 `docs/design/index.md`**：添加本文件的索引条目
- [ ] **更新 `docs/CHANGELOG.md`**：记录设计系统变更
- [ ] **更新 `docs/product/daily-report.md`**：如 section 标题交互规则变化，更新验收标准

---

## 9. 风险与约束

| 风险 | 缓解 |
|------|------|
| 提取共享 CSS 后页面加载增加一次 HTTP 请求 | 文件小（< 5KB），浏览器缓存；也可考虑构建时内联回 HTML |
| Inter 字体加载增加页面权重 | 仅加载 wght 600-900 子集 + latin + tabular figures，约 30KB |
| Dark 主题重配色影响已有用户偏好 | 保留 `fresh` 和 `sport` 不变，仅改 `dark`；用户可随时切换 |
| 心率区间色带环依赖心率数据可用性 | 无数据时显示灰色占位组件，不凭空渲染 |
| 动效可能影响低端设备性能 | 全部使用 `opacity` + `transform`（GPU 加速），避免 `height`/`width` 动画 |

---

## 10. 关联文档

- 产品文档: [daily-report.md](../product/daily-report.md) — 报告中心验收标准
- 产品文档: [training-experience.md](../product/training-experience.md) — 训练体验验收标准
- 产品文档: [data-source-sync.md](../product/data-source-sync.md) — 同步验收标准
- 技术设计: [03-architecture.md](03-architecture.md) — 整体架构
- 技术设计: [04-modules.md](04-modules.md) — 模块设计
- 审计来源: [frontend-design skill](../../.agents/skills/frontend-design/SKILL.md)

---

> **实现前确认**: 本方案标记为 Proposed。Phase 1 基础设施改动低风险，可直接实施。
> Phase 2–4 的视觉改动（Dark 主题重配色、签名元素、动效）需要用户确认后再进入实现。
