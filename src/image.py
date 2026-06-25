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

logger = logging.getLogger(__name__)


def render_image_playwright(
    html_path: str,
    output_path: str,
    theme: str = "sport",
    width: int = 800,
    scale: int = 2,
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
    html_file.write_text(html, encoding="utf-8")

    logger.info("🎭 Playwright (theme=%s, %dx%d@%dx)...", theme, width, int(width * 1.6), scale)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": int(width * 1.6)},
            device_scale_factor=scale,
        )
        page.goto(f"file://{html_file}", wait_until="networkidle")
        page.screenshot(path=str(out_file), full_page=True)
        browser.close()

    logger.info("✅ PNG: %s (%d KB)", out_file, out_file.stat().st_size // 1024)
    return str(out_file)


def render_image_chrome(
    html_path: str,
    output_path: str,
    theme: str = "sport",
    width: int = 800,
    scale: int = 2,
) -> str:
    """使用 Chrome headless 渲染 HTML 为 PNG（回退方案）。"""
    import shutil
    import subprocess

    html_file = Path(html_path).resolve()
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    html = html_file.read_text(encoding="utf-8")
    html = re.sub(r'<body data-theme="[^"]*"', f'<body data-theme="{theme}"', html)
    html_file.write_text(html, encoding="utf-8")

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

    logger.info("🌐 Chrome headless (theme=%s)...", theme)

    result = subprocess.run(
        [chrome, "--headless=new", f"--screenshot={out_file.name}",
         f"--window-size={width},{int(width * 1.6)}",
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

    logger.info("✅ PNG: %s (%d KB)", out_file, out_file.stat().st_size // 1024)
    return str(out_file)


def render_image(
    html_path: str,
    output_path: str,
    theme: str = "sport",
    width: int = 800,
    scale: int = 2,
) -> str:
    """渲染 HTML 为 PNG，自动选择可用引擎。"""
    # 1. Playwright
    try:
        return render_image_playwright(html_path, output_path, theme, width, scale)
    except (ImportError, Exception) as e:
        logger.info("Playwright 不可用 (%s)，尝试 Chrome...", e)

    # 2. Chrome fallback
    return render_image_chrome(html_path, output_path, theme, width, scale)


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
