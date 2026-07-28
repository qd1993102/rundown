"""首次设置页面的渐进式表单契约。"""

from pathlib import Path


_SETUP_HTML = (
    Path(__file__).resolve().parents[1] / "web" / "templates" / "setup.html"
).read_text(encoding="utf-8")


def test_personalization_forms_are_optional_and_collapsed_by_default():
    assert "步骤 1/2" in _SETUP_HTML
    assert "步骤 2/2 · 选填" in _SETUP_HTML
    assert '<details class="optional-section" id="profile-section">' in _SETUP_HTML
    assert '<details class="optional-section" id="coaching-section">' in _SETUP_HTML
    assert "跳过，先开始使用" in _SETUP_HTML
    assert "之后可在「我的」页面补填或修改" in _SETUP_HTML


def test_optional_forms_save_independently_without_blocking_home():
    assert "function finishSetup(){location.href='/'}" in _SETUP_HTML
    assert "async function doProfile()" in _SETUP_HTML
    assert "async function doCoaching()" in _SETUP_HTML
    assert "goStep(3)" not in _SETUP_HTML
    assert "跳过时不会创建空档案或默认偏好" in _SETUP_HTML
