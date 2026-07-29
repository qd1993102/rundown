"""运行依赖与部署镜像的打包契约测试。"""

from pathlib import Path
import os
import subprocess
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


def test_ecs_deploy_keeps_current_service_when_git_checkout_is_missing(tmp_path):
    """Git 下载失败时必须在任何 systemd 操作前终止。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = bin_dir / "systemctl"
    fake_systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SYSTEMCTL_LOG\"\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "WORK_DIR": str(tmp_path / "deploy"),
            "SYSTEMCTL_LOG": str(systemctl_log),
        }
    )
    result = subprocess.run(
        ["bash", str(_ROOT / "scripts" / "deploy-ecs.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Git 仓库未成功下载" in result.stderr
    assert not systemctl_log.exists()


def test_ecs_deploy_uses_release_directory_instead_of_download_checkout():
    script = (_ROOT / "scripts" / "deploy-ecs.sh").read_text(encoding="utf-8")

    assert 'SOURCE_DIR="${SOURCE_DIR:-${WORK_DIR}/code_deploy_application}"' in script
    assert 'RELEASES_DIR="${RELEASES_DIR:-/opt/neurun-releases}"' in script
    assert 'CURRENT_LINK="${CURRENT_LINK:-/opt/neurun-current}"' in script
    assert "WorkingDirectory=${CURRENT_LINK}" in script
    assert "ExecStart=${CURRENT_LINK}/.venv/bin/python" in script
