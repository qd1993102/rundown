"""运行依赖与部署镜像的打包契约测试。"""

from pathlib import Path
import tomllib


_ROOT = Path(__file__).resolve().parents[1]


def test_default_install_includes_coros_runtime_dependency():
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert any(
        dependency.partition(" ")[0].lower() == "coros-mcp"
        for dependency in project["dependencies"]
    )
    assert "coros" not in project.get("optional-dependencies", {})


def test_docker_image_installs_git_for_coros_dependency():
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "apt-get install -y --no-install-recommends libsqlite3-0 git" in dockerfile
