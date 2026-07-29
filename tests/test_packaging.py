"""运行依赖与部署镜像的打包契约测试。"""

import grp
import os
import pwd
import subprocess
import tomllib
from pathlib import Path


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
    assert "LimitNOFILE=8192" in script


def test_ecs_deploy_keeps_current_release_when_candidate_install_fails(tmp_path):
    """候选版本依赖安装失败时，不得切换软链接或操作在线服务。"""
    source_dir = tmp_path / "work" / "code_deploy_application"
    source_dir.mkdir(parents=True)
    (source_dir / "pyproject.toml").write_text("[project]\nname='candidate'\n")

    old_release = tmp_path / "releases" / "old"
    old_release.mkdir(parents=True)
    (old_release / "marker").write_text("running")
    current_link = tmp_path / "current"
    current_link.symlink_to(old_release)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = bin_dir / "systemctl"
    fake_systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SYSTEMCTL_LOG\"\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    fake_venv_python = bin_dir / "venv-python"
    fake_venv_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = '-m' ] && [ \"$2\" = 'pip' ] && [ \"$4\" = '--upgrade' ]; then exit 0; fi\n"
        "echo 'simulated dependency install failure' >&2\n"
        "exit 17\n",
        encoding="utf-8",
    )
    fake_venv_python.chmod(0o755)

    fake_python = bin_dir / "python3.12"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = '-c' ]; then exit 0; fi\n"
        "if [ \"$1\" = '-m' ] && [ \"$2\" = 'venv' ]; then\n"
        "  mkdir -p \"$4/bin\"\n"
        "  cp \"$FAKE_VENV_PYTHON\" \"$4/bin/python\"\n"
        "  chmod 755 \"$4/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WORK_DIR": str(tmp_path / "work"),
        "SOURCE_DIR": str(source_dir),
        "RELEASES_DIR": str(tmp_path / "releases"),
        "CURRENT_LINK": str(current_link),
        "DATA_DIR": str(tmp_path / "data"),
        "ENV_DIR": str(tmp_path / "etc"),
        "SERVICE_FILE": str(tmp_path / "neurun.service"),
        "SYSTEMCTL_BIN": str(fake_systemctl),
        "SYSTEMCTL_LOG": str(systemctl_log),
        "FAKE_VENV_PYTHON": str(fake_venv_python),
        "RUN_USER": pwd.getpwuid(os.getuid()).pw_name,
        "RUN_GROUP": grp.getgrgid(os.getgid()).gr_name,
    })
    result = subprocess.run(
        ["bash", str(_ROOT / "scripts" / "deploy-ecs.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 17
    assert "simulated dependency install failure" in result.stderr
    assert "当前 release、软链接和在线服务均未修改" in result.stderr
    assert current_link.resolve() == old_release.resolve()
    assert (old_release / "marker").read_text() == "running"
    assert not systemctl_log.exists()
