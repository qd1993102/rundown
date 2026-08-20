"""图片生成模块 — 跨平台高清 PNG 渲染。

1. Playwright (自带 Chromium, 跨平台)
2. Chrome headless (回退)

安装: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from .local_files import atomic_write_private, restrict_private_file

logger = logging.getLogger(__name__)


def render_image_playwright(
    html_path: str,
    output_path: str,
    theme: str = "sport",
    width: int = 800,
    scale: int = 2,
    height: int | None = None,
) -> str:
    """使用 Playwright (Chromium) 渲染 HTML 为高清 PNG。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "需要安装 Playwright: pip install playwright && playwright install chromium"
        )

    html_file = Path(html_path).resolve()
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    html = html_file.read_text(encoding="utf-8")
    html = re.sub(r'<body data-theme="[^"]*"', f'<body data-theme="{theme}"', html)
    atomic_write_private(html_file, html, private_parent=False)

    vp_height = height if height is not None else int(width * 1.6)
    logger.info("🎭 Playwright (theme=%s, %dx%d@%dx)...", theme, width, vp_height, scale)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": vp_height},
            device_scale_factor=scale,
        )
        page.goto(f"file://{html_file}", wait_until="networkidle")
        try:
            # 等待字体加载完成后再截图，避免 full_page 高度基于未应用字体的布局而截断
            page.wait_for_function(
                "() => (document.fonts ? document.fonts.ready.then(() => true) : Promise.resolve(true))",
                timeout=5000,
            )
        except Exception:
            pass
        page.screenshot(path=str(out_file), full_page=True)
        browser.close()

    restrict_private_file(out_file)

    logger.info("✅ PNG: %s (%d KB)", out_file, out_file.stat().st_size // 1024)
    return str(out_file)


def render_image_chrome(
    html_path: str,
    output_path: str,
    theme: str = "sport",
    width: int = 800,
    scale: int = 2,
    height: int | None = None,
) -> str:
    """使用 Chrome headless 渲染 HTML 为 PNG（回退方案）。"""
    import shutil
    import subprocess

    html_file = Path(html_path).resolve()
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    html = html_file.read_text(encoding="utf-8")
    html = re.sub(r'<body data-theme="[^"]*"', f'<body data-theme="{theme}"', html)
    atomic_write_private(html_file, html, private_parent=False)

    chrome_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "google-chrome", "chromium",
    ]
    chrome = None
    for p in chrome_paths:
        if shutil.which(p) or Path(p).exists():
            chrome = p
            break
    if chrome is None:
        raise RuntimeError("未找到 Chrome，请安装 Playwright: pip install playwright && playwright install chromium")

    vp_height = height if height is not None else int(width * 1.6)
    logger.info("🌐 Chrome headless (theme=%s, %dx%d)...", theme, width, vp_height)

    if height is None:
        # 内容高度自适应：先注入测量脚本并读取内容高度，再按实际高度截图
        measured = _measure_content_height_chrome(
            chrome, html_file, width, vp_height,
        )
        if measured and measured > 0:
            # 预留少量安全余量，吸收字体/渲染差异，避免底部内容被裁切
            vp_height = measured + 20
            logger.info("📐 内容自适应高度: %dpx", vp_height)

    result = subprocess.run(
        [chrome, "--headless=new", f"--screenshot={out_file.name}",
         f"--window-size={width},{vp_height}",
         f"--force-device-scale-factor={scale}",
         "--no-sandbox", "--disable-gpu", f"file://{html_file}"],
        capture_output=True, text=True, timeout=30,
        cwd=str(out_file.parent),
    )

    if result.returncode != 0 or not out_file.exists():
        # fallback: try current dir
        fallback = Path.cwd() / out_file.name
        if fallback.exists():
            shutil.move(str(fallback), str(out_file))

    if not out_file.exists():
        raise RuntimeError(f"Chrome 截图失败: {result.stderr[:300]}")

    restrict_private_file(out_file)

    logger.info("✅ PNG: %s (%d KB)", out_file, out_file.stat().st_size // 1024)
    return str(out_file)


def _measure_content_height_chrome(
    chrome: str, html_file: Path, width: int, vp_height: int,
) -> int | None:
    """用 Chrome headless 测量 HTML 内容实际高度。

    等待 document.fonts.ready 后再取值，并配合 --virtual-time-budget
    推进虚拟时间，避免字体未应用时测量偏小导致截图截断。
    """
    try:
        html = html_file.read_text(encoding="utf-8")
        script = (
            "<script>"
            "(function(){"
            "var KEY='data-content-height';"
            "function mark(){"
            "var h=Math.max(document.body.scrollHeight||0,"
            "document.documentElement.scrollHeight||0);"
            "document.body.setAttribute(KEY,String(h));"
            "}"
            "mark();"
            "if(document.fonts&&document.fonts.ready){"
            "document.fonts.ready.then(mark);}"
            "window.addEventListener('load',mark);"
            "})();"
            "</script>"
        )
        html = html.replace("</body>", script + "</body>")
        atomic_write_private(html_file, html, private_parent=False)
        result = subprocess.run(
            [chrome, "--headless=new", "--dump-dom",
             "--virtual-time-budget=3000",
             f"--window-size={width},{vp_height}",
             "--no-sandbox", "--disable-gpu", f"file://{html_file}"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    m = re.search(r'data-content-height="(\d+)"', result.stdout)
    if not m:
        return None
    measured = int(m.group(1))
    return measured if measured > 0 else None


def render_image(
    html_path: str,
    output_path: str,
    theme: str = "sport",
    width: int = 800,
    scale: int = 2,
    height: int | None = None,
) -> str:
    """渲染 HTML 为 PNG，自动选择可用引擎。"""
    if height is None:
        # Playwright full_page 已按内容裁切；此处仅用于传给引擎的初始视口
        vp_default = int(width * 1.6)
        try:
            return render_image_playwright(
                html_path, output_path, theme, width, scale, height=vp_default,
            )
        except (ImportError, Exception) as e:
            logger.info("Playwright 不可用 (%s)，尝试 Chrome...", e)
            return render_image_chrome(
                html_path, output_path, theme, width, scale, height=None,
            )
    # 1. Playwright
    try:
        return render_image_playwright(html_path, output_path, theme, width, scale, height=height)
    except (ImportError, Exception) as e:
        logger.info("Playwright 不可用 (%s)，尝试 Chrome...", e)

    # 2. Chrome fallback
    return render_image_chrome(html_path, output_path, theme, width, scale, height=height)


def render_daily_image(memory, output_path: str | None = None,
                       theme: str = "sport") -> str:
    """生成日报 PNG 图片。"""
    if output_path is None:
        output_path = f"output/{memory.id}.png"

    from .render import render_daily_html

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp.close()
    html_path = str(Path(tmp.name))
    try:
        render_daily_html(memory, html_path)
        return render_image(html_path, output_path, theme=theme)
    finally:
        Path(html_path).unlink(missing_ok=True)
