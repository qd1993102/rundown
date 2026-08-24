"""单用户脱敏 tar 导出测试。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.user_data_archive as archive_module
from src.user_data_archive import (
    ArchiveError,
    EXIT_CONSISTENCY,
    EXIT_INPUT,
    EXIT_IO,
    EXIT_PERMISSION,
    export_user_data,
)


FIXED_NOW = datetime(2026, 8, 24, 8, 9, 10, tzinfo=timezone.utc)
FIXED_REQUEST_ID = "abcdef123456"


def _write_user(
    data_root: Path,
    *,
    api_key: str,
    nickname: str,
    email: str,
    with_memory: bool = True,
    with_sync: bool = True,
    with_backup: bool = True,
) -> Path:
    users_dir = data_root / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "api_key": api_key,
        "nickname": nickname,
        "email": email,
        "password_hash": "scrypt$secret-password-hash",
        "provider": "garmin",
        "garmin_domain": "garmin.cn",
        "garmin_email": "private-garmin@example.com",
        "created": "2026-08-01",
        "last_sync": "2026-08-23T01:02:03Z",
        "token_status": "active",
        "provider_token": "provider-secret-token",
    }
    (users_dir / f"{api_key}.json").write_text(
        json.dumps(record, ensure_ascii=False),
        encoding="utf-8",
    )

    user_root = data_root / api_key
    user_root.mkdir()
    (user_root / "data.db").write_bytes(b"sqlite-user-data")
    if with_memory:
        (user_root / "memory" / "reports").mkdir(parents=True)
        (user_root / "memory" / "reports" / "daily.md").write_text(
            "# Daily\n",
            encoding="utf-8",
        )
        (user_root / "memory" / "profile.md").write_text(
            "profile\n",
            encoding="utf-8",
        )
    if with_sync:
        (user_root / "sync-tasks.json").write_text('{"status":"done"}\n', encoding="utf-8")
    if with_backup:
        (data_root / "backup").mkdir(exist_ok=True)
        (data_root / "backup" / f"{api_key}.db").write_bytes(b"sqlite-backup")
    return user_root


def _export(data_root: Path, destination: Path, **kwargs):
    return export_user_data(
        data_root=data_root,
        destination=destination,
        request_id_factory=lambda: FIXED_REQUEST_ID,
        now_factory=lambda: FIXED_NOW,
        **kwargs,
    )


def _load_script_module():
    script_path = Path(__file__).parents[1] / "scripts" / "export_user_data.py"
    spec = importlib.util.spec_from_file_location("export_user_data_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unique_nickname_exports_manifest_hashes_and_private_mode(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    api_key = "rd_secret_user_key"
    _write_user(
        data_root,
        api_key=api_key,
        nickname="  跑者甲  ",
        email="runner@example.com",
    )

    result = _export(data_root, destination, nickname=" 跑者甲 ")

    assert result.path.name == "neurun-user-data-abcdef123456-20260824T080910Z.tar"
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    archive_bytes = result.path.read_bytes()
    assert result.size == len(archive_bytes)
    assert result.sha256 == hashlib.sha256(archive_bytes).hexdigest()
    assert api_key.encode() not in archive_bytes

    with tarfile.open(result.path, "r:") as archive:
        names = archive.getnames()
        assert names == [
            "account.json",
            "data/data.db",
            "memory/profile.md",
            "memory/reports/daily.md",
            "sync/sync-tasks.json",
            "backup/data.db",
            "manifest.json",
        ]
        manifest = json.load(archive.extractfile("manifest.json"))
        account = json.load(archive.extractfile("account.json"))
        for entry in manifest["members"]:
            content = archive.extractfile(entry["path"]).read()
            info = archive.getmember(entry["path"])
            assert entry["size"] == len(content)
            assert entry["mtime"] == int(info.mtime)
            assert entry["sha256"] == hashlib.sha256(content).hexdigest()

    assert set(account) == {
        "nickname",
        "email",
        "provider",
        "garmin_domain",
        "created",
        "last_sync",
        "token_status",
    }
    assert manifest["missing_categories"] == []
    assert "manifest.json" not in {entry["path"] for entry in manifest["members"]}


def test_duplicate_nickname_requires_email_and_masks_candidates(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    _write_user(data_root, api_key="rd_first", nickname="Same", email="alice@example.com")
    _write_user(data_root, api_key="rd_second", nickname="Same", email="bob@example.com")

    with pytest.raises(ArchiveError) as error:
        _export(data_root, destination, nickname="Same")

    assert error.value.error_code == "nickname_ambiguous"
    assert error.value.exit_code == EXIT_INPUT
    assert error.value.details == {
        "candidate_count": 2,
        "candidate_emails": ["a***@example.com", "b***@example.com"],
    }
    assert not list(destination.iterdir())


def test_email_disambiguation_selects_same_account(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    _write_user(data_root, api_key="rd_first", nickname="Same", email="alice@example.com")
    _write_user(data_root, api_key="rd_second", nickname="Same", email="bob@example.com")

    result = _export(
        data_root,
        destination,
        nickname="Same",
        email=" BOB@EXAMPLE.COM ",
    )

    with tarfile.open(result.path, "r:") as archive:
        account = json.load(archive.extractfile("account.json"))
    assert account["email"] == "bob@example.com"


def test_email_disambiguation_mismatch_fails_without_archive(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    _write_user(data_root, api_key="rd_first", nickname="Same", email="alice@example.com")
    _write_user(data_root, api_key="rd_second", nickname="Same", email="bob@example.com")

    with pytest.raises(ArchiveError) as error:
        _export(data_root, destination, nickname="Same", email="other@example.com")

    assert error.value.error_code == "disambiguation_mismatch"
    assert error.value.exit_code == EXIT_INPUT
    assert not list(destination.iterdir())


def test_sensitive_fields_token_dirs_and_other_users_are_excluded(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    api_key = "rd_private_key"
    user_root = _write_user(
        data_root,
        api_key=api_key,
        nickname="Runner",
        email="runner@example.com",
    )
    (user_root / "tokens").mkdir()
    (user_root / "tokens" / "oauth.json").write_text("provider-secret-token", encoding="utf-8")
    (user_root / "huawei-tokens").mkdir()
    (user_root / "huawei-tokens" / "token").write_text("huawei-secret", encoding="utf-8")
    _write_user(
        data_root,
        api_key="rd_other_user",
        nickname="Other",
        email="other@example.com",
    )

    result = _export(data_root, destination, nickname="Runner")
    archive_bytes = result.path.read_bytes()

    for secret in (
        api_key,
        "scrypt$secret-password-hash",
        "private-garmin@example.com",
        "provider-secret-token",
        "huawei-secret",
        "rd_other_user",
    ):
        assert secret.encode() not in archive_bytes
    with tarfile.open(result.path, "r:") as archive:
        assert not any("token" in name for name in archive.getnames())


def test_optional_missing_categories_are_reported(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    _write_user(
        data_root,
        api_key="rd_minimal",
        nickname="Minimal",
        email="minimal@example.com",
        with_memory=False,
        with_sync=False,
        with_backup=False,
    )

    result = _export(data_root, destination, nickname="Minimal")

    assert result.missing_categories == ("memory", "sync_tasks", "backup")
    with tarfile.open(result.path, "r:") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        assert manifest["missing_categories"] == ["memory", "sync_tasks", "backup"]


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_memory_symlink_or_special_file_is_rejected(tmp_path, unsafe_kind):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    user_root = _write_user(
        data_root,
        api_key="rd_unsafe",
        nickname="Unsafe",
        email="unsafe@example.com",
    )
    unsafe_path = user_root / "memory" / "unsafe"
    if unsafe_kind == "symlink":
        unsafe_path.symlink_to(user_root / "data.db")
    else:
        os.mkfifo(unsafe_path)

    with pytest.raises(ArchiveError) as error:
        _export(data_root, destination, nickname="Unsafe")

    assert error.value.error_code == "unsafe_source"
    assert error.value.exit_code == EXIT_CONSISTENCY
    assert not list(destination.iterdir())


@pytest.mark.parametrize(
    ("walk_error", "error_code", "exit_code"),
    [
        (PermissionError(13, "permission denied"), "permission_denied", EXIT_PERMISSION),
        (OSError(5, "input/output error"), "source_io_error", EXIT_IO),
    ],
)
def test_memory_walk_errors_fail_closed(monkeypatch, tmp_path, walk_error, error_code, exit_code):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    _write_user(
        data_root,
        api_key="rd_walk_error",
        nickname="WalkError",
        email="walk-error@example.com",
    )

    def failing_walk(root, *, followlinks, onerror):
        assert followlinks is False
        onerror(walk_error)
        yield str(root), [], []

    monkeypatch.setattr(archive_module.os, "walk", failing_walk)

    with pytest.raises(ArchiveError) as error:
        _export(data_root, destination, nickname="WalkError")

    assert error.value.error_code == error_code
    assert error.value.exit_code == exit_code
    assert not list(destination.iterdir())


def test_dangling_optional_symlink_is_rejected_instead_of_reported_missing(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    user_root = _write_user(
        data_root,
        api_key="rd_dangling",
        nickname="Dangling",
        email="dangling@example.com",
        with_sync=False,
    )
    (user_root / "sync-tasks.json").symlink_to(user_root / "missing-sync.json")

    with pytest.raises(ArchiveError) as error:
        _export(data_root, destination, nickname="Dangling")

    assert error.value.error_code == "unsafe_source"
    assert error.value.exit_code == EXIT_CONSISTENCY


def test_missing_destination_and_existing_collision_fail(tmp_path):
    data_root = tmp_path / "data"
    _write_user(data_root, api_key="rd_user", nickname="Runner", email="runner@example.com")

    with pytest.raises(ArchiveError) as missing_error:
        _export(data_root, tmp_path / "missing", nickname="Runner")
    assert missing_error.value.error_code == "destination_not_found"
    assert missing_error.value.exit_code == EXIT_IO

    destination = tmp_path / "exports"
    destination.mkdir()
    collision = destination / "neurun-user-data-abcdef123456-20260824T080910Z.tar"
    collision.write_bytes(b"keep-me")
    with pytest.raises(ArchiveError) as collision_error:
        _export(data_root, destination, nickname="Runner")
    assert collision_error.value.error_code == "archive_collision"
    assert collision_error.value.exit_code == EXIT_CONSISTENCY
    assert collision.read_bytes() == b"keep-me"
    assert list(destination.iterdir()) == [collision]


def test_source_mutation_fails_and_cleans_temporary_files(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    user_root = _write_user(
        data_root,
        api_key="rd_mutating",
        nickname="Mutating",
        email="mutating@example.com",
    )
    mutated = False

    def mutate_source(path: Path) -> None:
        nonlocal mutated
        if path == user_root / "data.db" and not mutated:
            mutated = True
            path.write_bytes(path.read_bytes() + b"changed")

    with pytest.raises(ArchiveError) as error:
        export_user_data(
            data_root=data_root,
            destination=destination,
            nickname="Mutating",
            request_id_factory=lambda: FIXED_REQUEST_ID,
            now_factory=lambda: FIXED_NOW,
            before_second_stat=mutate_source,
        )

    assert error.value.error_code == "source_changed"
    assert error.value.exit_code == EXIT_CONSISTENCY
    assert error.value.retryable is True
    assert not list(destination.iterdir())


def test_missing_required_database_is_consistency_failure(tmp_path):
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    user_root = _write_user(
        data_root,
        api_key="rd_missing_db",
        nickname="NoDB",
        email="nodb@example.com",
    )
    (user_root / "data.db").unlink()

    with pytest.raises(ArchiveError) as error:
        _export(data_root, destination, nickname="NoDB")

    assert error.value.error_code == "required_source_missing"
    assert error.value.exit_code == EXIT_CONSISTENCY


def test_script_json_output_and_exit_codes(tmp_path, monkeypatch, capsys):
    module = _load_script_module()
    data_root = tmp_path / "data"
    destination = tmp_path / "exports"
    destination.mkdir()
    _write_user(data_root, api_key="rd_cli", nickname="CLI", email="cli@example.com")
    monkeypatch.setenv("NEURUN_DATA_DIR", str(data_root))

    success_code = module.main([
        "--nickname", "CLI", "--destination", str(destination), "--output", "json",
    ])
    success_output = capsys.readouterr()
    assert success_code == 0
    assert success_output.out.count("\n") == 1
    assert json.loads(success_output.out)["status"] == "success"
    assert success_output.err == ""

    exit_code = module.main([
        "--nickname", "Missing", "--destination", str(destination), "--output", "json",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == EXIT_INPUT
    assert payload["status"] == "failed"
    assert payload["error_code"] == "user_not_found"
    assert payload["retryable"] is False
    assert captured.err == ""

    missing_destination_code = module.main([
        "--nickname", "CLI", "--destination", str(tmp_path / "none"), "--output", "json",
    ])
    missing_destination_output = capsys.readouterr()
    assert missing_destination_code == EXIT_IO
    assert json.loads(missing_destination_output.out)["error_code"] == "destination_not_found"

    def permission_failure(**kwargs):
        raise ArchiveError("permission_denied", "无权访问", EXIT_PERMISSION)

    monkeypatch.setattr(module, "export_user_data", permission_failure)
    permission_code = module.main([
        "--nickname", "CLI", "--destination", str(destination), "--output", "json",
    ])
    permission_output = capsys.readouterr()
    assert permission_code == EXIT_PERMISSION
    assert json.loads(permission_output.out)["error_code"] == "permission_denied"


def test_script_missing_destination_returns_json_without_interactive_prompt(capsys):
    module = _load_script_module()
    exit_code = module.main(["--nickname", "Runner", "--output", "json"])
    captured = capsys.readouterr()
    assert exit_code == EXIT_INPUT
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == {
        "status": "failed",
        "error_code": "invalid_arguments",
        "retryable": False,
        "message": "the following arguments are required: --destination/-d",
    }
    assert captured.err == ""


def test_neurun_cli_and_mcp_do_not_expose_archive_command():
    root = Path(__file__).parents[1]
    main_source = (root / "src" / "main.py").read_text(encoding="utf-8")
    mcp_source = (root / "src" / "mcp_server.py").read_text(encoding="utf-8")
    assert "export_user_data" not in main_source
    assert "export_user_data" not in mcp_source
    assert "user-data archive" not in main_source
    assert "user-data archive" not in mcp_source
