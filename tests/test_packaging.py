"""运行依赖与部署镜像的打包契约测试。"""

import grp
import os
import pwd
import subprocess
import tomllib
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


def _init_git_source(source_dir: Path) -> str:
    """创建可供部署脚本校验的最小干净 Git 暂存仓库。"""
    source_dir.mkdir(parents=True)
    (source_dir / "pyproject.toml").write_text("[project]\nname='candidate'\nversion='1.0.0'\n")
    subprocess.run(["git", "init", "-q", str(source_dir)], check=True)
    subprocess.run(["git", "-C", str(source_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(source_dir), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(source_dir), "add", "pyproject.toml"], check=True)
    subprocess.run(["git", "-C", str(source_dir), "commit", "-q", "-m", "candidate"], check=True)
    return subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_fake_systemctl(bin_dir: Path, systemctl_log: Path) -> Path:
    fake_systemctl = bin_dir / "systemctl"
    fake_systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SYSTEMCTL_LOG\"\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    return fake_systemctl


def _write_fake_flock(bin_dir: Path, *, exit_code: int = 0) -> None:
    fake_flock = bin_dir / "flock"
    fake_flock.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    fake_flock.chmod(0o755)


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
            "SOURCE_DIR": str(tmp_path / "deploy" / "code_deploy_application"),
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

    assert 'SOURCE_DIR="${SOURCE_DIR:-}"' in script
    assert 'EXPECTED_COMMIT="${EXPECTED_COMMIT:-}"' in script
    assert 'git -C "${SOURCE_DIR}" rev-parse HEAD' in script
    assert 'flock -n 9' in script
    assert 'RELEASES_DIR="${RELEASES_DIR:-/opt/neurun-releases}"' in script
    assert 'CURRENT_LINK="${CURRENT_LINK:-/opt/neurun-current}"' in script
    assert "WorkingDirectory=${CURRENT_LINK}" in script
    assert "ExecStart=${CURRENT_LINK}/.venv/bin/python" in script
    assert "Environment=NEURUN_RELEASE_SHA=${release_sha}" in script
    assert 'printf \'%s\\n\' "${SOURCE_COMMIT}" > "${RELEASE_DIR}/.neurun-release"' in script
    assert 'if wait_for_health "${SOURCE_COMMIT}"; then' in script
    assert "Coros 自动鉴权不会默认开启" in script
    assert "LimitNOFILE=8192" in script


def test_ecs_console_entry_deploys_platform_selected_checkout_without_fixed_branch():
    """控制台入口应发布平台选定的 HEAD，不得另外固定分支。"""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    entry_start = readme.index("阿里云控制台的“启动脚本”使用：")
    entry_end = readme.index("不要在上述校验前执行", entry_start)
    entry = readme[entry_start:entry_end]

    assert 'EXPECTED_COMMIT="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"' in entry
    assert 'EXPECTED_COMMIT="${EXPECTED_COMMIT}"' in entry
    assert "DEPLOY_REF" not in entry
    assert "git -C \"${SOURCE_DIR}\" fetch" not in entry
    assert "git -C \"${SOURCE_DIR}\" merge" not in entry


def test_ecs_deploy_rejects_stale_source_commit_before_systemd(tmp_path):
    """平台给出旧 checkout 时不得把它当作成功候选版本。"""
    source_dir = tmp_path / "work" / "code_deploy_application"
    actual_commit = _init_git_source(source_dir)
    expected_commit = "0" * 40
    assert actual_commit != expected_commit

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = _write_fake_systemctl(bin_dir, systemctl_log)
    _write_fake_flock(bin_dir)

    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WORK_DIR": str(tmp_path / "work"),
        "SOURCE_DIR": str(source_dir),
        "EXPECTED_COMMIT": expected_commit,
        "SYSTEMCTL_BIN": str(fake_systemctl),
        "SYSTEMCTL_LOG": str(systemctl_log),
        "DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    })
    result = subprocess.run(
        ["bash", str(_ROOT / "scripts" / "deploy-ecs.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "候选提交与预期不一致" in result.stderr
    assert actual_commit in result.stderr
    assert expected_commit in result.stderr
    assert not systemctl_log.exists()


def test_ecs_deploy_rejects_concurrent_release_before_systemd(tmp_path):
    """已有发布持锁时，新任务必须快速失败且不能操作在线服务。"""
    source_dir = tmp_path / "work" / "code_deploy_application"
    expected_commit = _init_git_source(source_dir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = _write_fake_systemctl(bin_dir, systemctl_log)
    _write_fake_flock(bin_dir, exit_code=1)

    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "WORK_DIR": str(tmp_path / "work"),
        "SOURCE_DIR": str(source_dir),
        "EXPECTED_COMMIT": expected_commit,
        "SYSTEMCTL_BIN": str(fake_systemctl),
        "SYSTEMCTL_LOG": str(systemctl_log),
        "DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    })
    result = subprocess.run(
        ["bash", str(_ROOT / "scripts" / "deploy-ecs.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "已有 neurun 发布任务正在执行" in result.stderr
    assert not systemctl_log.exists()


def test_ecs_deploy_keeps_current_release_when_candidate_install_fails(tmp_path):
    """候选版本依赖安装失败时，不得切换软链接或操作在线服务。"""
    source_dir = tmp_path / "work" / "code_deploy_application"
    expected_commit = _init_git_source(source_dir)

    old_release = tmp_path / "releases" / "old"
    old_release.mkdir(parents=True)
    (old_release / "marker").write_text("running")
    current_link = tmp_path / "current"
    current_link.symlink_to(old_release)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = _write_fake_systemctl(bin_dir, systemctl_log)
    _write_fake_flock(bin_dir)

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
        "EXPECTED_COMMIT": expected_commit,
        "RELEASES_DIR": str(tmp_path / "releases"),
        "CURRENT_LINK": str(current_link),
        "DATA_DIR": str(tmp_path / "data"),
        "ENV_DIR": str(tmp_path / "etc"),
        "SERVICE_FILE": str(tmp_path / "neurun.service"),
        "SYSTEMCTL_BIN": str(fake_systemctl),
        "SYSTEMCTL_LOG": str(systemctl_log),
        "FAKE_VENV_PYTHON": str(fake_venv_python),
        "DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
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
