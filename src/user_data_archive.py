"""只读打包单个应用账号的脱敏关联数据。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import stat
import tarfile
from errno import EACCES, EPERM
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


EXIT_SUCCESS = 0
EXIT_INPUT = 2
EXIT_PERMISSION = 3
EXIT_IO = 4
EXIT_CONSISTENCY = 5

_ACCOUNT_FIELDS = (
    "nickname",
    "email",
    "provider",
    "garmin_domain",
    "created",
    "last_sync",
    "token_status",
)
_OPTIONAL_CATEGORIES = ("memory", "sync_tasks", "backup")


class ArchiveError(Exception):
    """稳定、可映射为 CLI 结果的导出错误。"""

    def __init__(
        self,
        error_code: str,
        message: str,
        exit_code: int,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.exit_code = exit_code
        self.retryable = retryable
        self.details = details or {}

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "failed",
            "error_code": self.error_code,
            "retryable": self.retryable,
            "message": self.message,
        }
        result.update(self.details)
        return result


@dataclass(frozen=True)
class SourceMember:
    archive_path: str
    data: bytes
    mtime: int

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "path": self.archive_path,
            "size": len(self.data),
            "mtime": self.mtime,
            "sha256": hashlib.sha256(self.data).hexdigest(),
        }


@dataclass(frozen=True)
class ArchiveResult:
    path: Path
    size: int
    sha256: str
    request_id: str
    member_count: int
    missing_categories: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success",
            "archive_path": str(self.path),
            "archive_size": self.size,
            "archive_sha256": self.sha256,
            "request_id": self.request_id,
            "member_count": self.member_count,
            "missing_categories": list(self.missing_categories),
        }


def export_user_data(
    *,
    data_root: str | Path,
    nickname: str,
    destination: str | Path,
    email: str | None = None,
    request_id_factory: Callable[[], str] | None = None,
    now_factory: Callable[[], datetime] | None = None,
    before_second_stat: Callable[[Path], None] | None = None,
) -> ArchiveResult:
    """按昵称查找唯一账号并原子生成未压缩 tar。"""
    root = Path(data_root)
    destination_path = Path(destination)
    _validate_destination(destination_path)
    _require_directory(root, "data_root_unsafe")

    account = _select_account(root, nickname, email)
    user_root = root / str(account["api_key"])
    members, missing_categories = _collect_members(
        root,
        user_root,
        account,
        before_second_stat=before_second_stat,
    )

    request_id = _make_request_id(request_id_factory)
    now = (now_factory or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"neurun-user-data-{request_id}-{timestamp}.tar"
    final_path = destination_path / filename
    temp_path = destination_path / f".{filename}.{secrets.token_hex(8)}.tmp"

    if final_path.exists() or final_path.is_symlink():
        raise ArchiveError(
            "archive_collision",
            "目标归档文件已存在，未覆盖",
            EXIT_CONSISTENCY,
        )

    manifest = {
        "format_version": 1,
        "created_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "request_id": request_id,
        "archive_format": "tar",
        "compression": "none",
        "privacy_profile": "redacted-user-data",
        "members": [member.manifest_entry() for member in members],
        "missing_categories": missing_categories,
        "excluded_categories": [
            "api_key",
            "password_hash",
            "garmin_email",
            "tokens",
            "huawei-tokens",
            "credentials",
            "other_users",
            "global_configuration",
            "logs",
            "source_code",
            "symlinks",
            "special_files",
        ],
    }
    manifest_bytes = _json_bytes(manifest)

    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(
            temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "wb") as output:
            file_descriptor = None
            with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for member in members:
                    _add_tar_bytes(archive, member)
                _add_tar_bytes(
                    archive,
                    SourceMember("manifest.json", manifest_bytes, int(now.timestamp())),
                )
            output.flush()
            os.fsync(output.fileno())

        if final_path.exists() or final_path.is_symlink():
            raise ArchiveError(
                "archive_collision",
                "目标归档文件已存在，未覆盖",
                EXIT_CONSISTENCY,
            )
        os.replace(temp_path, final_path)
        os.chmod(final_path, 0o600)
        _fsync_directory(destination_path)
    except ArchiveError:
        _cleanup_temp(temp_path)
        raise
    except PermissionError as exc:
        _cleanup_temp(temp_path)
        raise ArchiveError(
            "permission_denied",
            "无权创建或发布归档文件",
            EXIT_PERMISSION,
        ) from exc
    except OSError as exc:
        _cleanup_temp(temp_path)
        raise ArchiveError(
            "archive_io_error",
            "创建或发布归档文件失败",
            EXIT_IO,
            retryable=True,
        ) from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)

    archive_bytes = _read_regular_file(final_path)
    return ArchiveResult(
        path=final_path,
        size=len(archive_bytes),
        sha256=hashlib.sha256(archive_bytes).hexdigest(),
        request_id=request_id,
        member_count=len(members) + 1,
        missing_categories=tuple(missing_categories),
    )


def _select_account(root: Path, nickname: str, email: str | None) -> dict[str, Any]:
    target_nickname = nickname.strip()
    if not target_nickname:
        raise ArchiveError("invalid_nickname", "昵称不能为空", EXIT_INPUT)

    users_dir = root / "users"
    if not _path_entry_exists(users_dir):
        raise ArchiveError("user_not_found", "未找到匹配用户", EXIT_INPUT)
    _require_directory(users_dir, "users_registry_unsafe")

    matches: list[dict[str, Any]] = []
    try:
        entries = sorted(users_dir.iterdir(), key=lambda path: path.name)
    except PermissionError as exc:
        raise ArchiveError(
            "permission_denied",
            "无权读取用户注册表",
            EXIT_PERMISSION,
        ) from exc
    except OSError as exc:
        raise ArchiveError(
            "source_io_error",
            "读取用户注册表失败",
            EXIT_IO,
            retryable=True,
        ) from exc

    for path in entries:
        if path.suffix != ".json":
            continue
        if path.is_symlink():
            raise ArchiveError(
                "unsafe_source",
                "用户注册表包含符号链接",
                EXIT_CONSISTENCY,
            )
        raw = _read_regular_file(path)
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ArchiveError(
                "invalid_user_record",
                "用户注册表包含无效记录",
                EXIT_CONSISTENCY,
            ) from exc
        if not isinstance(record, dict):
            raise ArchiveError(
                "invalid_user_record",
                "用户注册表包含无效记录",
                EXIT_CONSISTENCY,
            )
        api_key = str(record.get("api_key", ""))
        if (
            not api_key
            or api_key in {".", ".."}
            or path.name != f"{api_key}.json"
            or "/" in api_key
            or "\\" in api_key
        ):
            raise ArchiveError(
                "invalid_user_record",
                "用户注册记录与文件名不一致",
                EXIT_CONSISTENCY,
            )
        if str(record.get("nickname", "")).strip() == target_nickname:
            matches.append(record)

    if not matches:
        raise ArchiveError("user_not_found", "未找到匹配用户", EXIT_INPUT)

    if len(matches) > 1 and email is None:
        raise ArchiveError(
            "nickname_ambiguous",
            "昵称匹配到多个账号，请提供邮箱消歧",
            EXIT_INPUT,
            details={
                "candidate_count": len(matches),
                "candidate_emails": [_mask_email(str(item.get("email", ""))) for item in matches],
            },
        )

    if email is not None:
        target_email = email.strip().casefold()
        email_matches = [
            item for item in matches
            if str(item.get("email", "")).strip().casefold() == target_email
        ]
        if len(email_matches) != 1:
            raise ArchiveError(
                "disambiguation_mismatch",
                "邮箱未指向该昵称的唯一账号",
                EXIT_INPUT,
            )
        return email_matches[0]

    return matches[0]


def _collect_members(
    root: Path,
    user_root: Path,
    account: dict[str, Any],
    *,
    before_second_stat: Callable[[Path], None] | None,
) -> tuple[list[SourceMember], list[str]]:
    account_data = {field: str(account.get(field, "")) for field in _ACCOUNT_FIELDS}
    members = [
        SourceMember("account.json", _json_bytes(account_data), 0),
    ]
    missing_categories: list[str] = []

    _require_directory(user_root, "user_data_root_unsafe")
    db_path = user_root / "data.db"
    members.append(
        _read_source_member(
            db_path,
            "data/data.db",
            before_second_stat=before_second_stat,
        )
    )

    memory_root = user_root / "memory"
    if not _path_entry_exists(memory_root):
        missing_categories.append("memory")
    else:
        _require_directory(memory_root, "unsafe_source")
        memory_members = list(
            _enumerate_memory(memory_root, before_second_stat=before_second_stat),
        )
        if memory_members:
            members.extend(memory_members)
        else:
            missing_categories.append("memory")

    sync_path = user_root / "sync-tasks.json"
    if not _path_entry_exists(sync_path):
        missing_categories.append("sync_tasks")
    else:
        members.append(
            _read_source_member(
                sync_path,
                "sync/sync-tasks.json",
                before_second_stat=before_second_stat,
            )
        )

    backup_path = root / "backup" / f"{account['api_key']}.db"
    if not _path_entry_exists(backup_path):
        missing_categories.append("backup")
    else:
        _require_directory(backup_path.parent, "unsafe_source")
        members.append(
            _read_source_member(
                backup_path,
                "backup/data.db",
                before_second_stat=before_second_stat,
            )
        )

    return members, [category for category in _OPTIONAL_CATEGORIES if category in missing_categories]


def _enumerate_memory(
    memory_root: Path,
    *,
    before_second_stat: Callable[[Path], None] | None,
) -> Iterable[SourceMember]:
    for current_root, directory_names, file_names in os.walk(
        memory_root,
        followlinks=False,
        onerror=_raise_memory_walk_error,
    ):
        directory_names.sort()
        file_names.sort()
        current_path = Path(current_root)
        for directory_name in list(directory_names):
            path = current_path / directory_name
            try:
                mode = path.lstat().st_mode
            except PermissionError as exc:
                raise ArchiveError(
                    "permission_denied",
                    "无权读取 memory 目录",
                    EXIT_PERMISSION,
                ) from exc
            except OSError as exc:
                raise ArchiveError(
                    "source_io_error",
                    "读取 memory 目录失败",
                    EXIT_IO,
                    retryable=True,
                ) from exc
            if not stat.S_ISDIR(mode):
                raise ArchiveError(
                    "unsafe_source",
                    "memory 目录包含符号链接或特殊目录项",
                    EXIT_CONSISTENCY,
                )
        for file_name in file_names:
            path = current_path / file_name
            relative_path = path.relative_to(memory_root)
            archive_path = str(PurePosixPath("memory", *relative_path.parts))
            yield _read_source_member(
                path,
                archive_path,
                before_second_stat=before_second_stat,
            )


def _raise_memory_walk_error(error: OSError) -> None:
    """把 os.walk 的目录读取错误转为 fail-closed 的稳定归档错误。"""
    if isinstance(error, PermissionError) or error.errno in (EACCES, EPERM):
        raise ArchiveError(
            "permission_denied",
            "无权读取 memory 目录",
            EXIT_PERMISSION,
        ) from error
    raise ArchiveError(
        "source_io_error",
        "读取 memory 目录失败",
        EXIT_IO,
        retryable=True,
    ) from error


def _read_source_member(
    path: Path,
    archive_path: str,
    *,
    before_second_stat: Callable[[Path], None] | None,
) -> SourceMember:
    _validate_archive_path(archive_path)
    try:
        first_stat = path.lstat()
        if not stat.S_ISREG(first_stat.st_mode):
            raise ArchiveError(
                "unsafe_source",
                "源数据包含符号链接或特殊文件",
                EXIT_CONSISTENCY,
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise ArchiveError(
                    "unsafe_source",
                    "源数据包含符号链接或特殊文件",
                    EXIT_CONSISTENCY,
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
            if before_second_stat is not None:
                before_second_stat(path)
            opened_after_stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        second_stat = path.lstat()
    except FileNotFoundError as exc:
        error_code = "required_source_missing" if archive_path == "data/data.db" else "source_changed"
        raise ArchiveError(
            error_code,
            "必需数据文件缺失" if error_code == "required_source_missing" else "源数据在读取期间发生变化",
            EXIT_CONSISTENCY,
            retryable=error_code == "source_changed",
        ) from exc
    except PermissionError as exc:
        raise ArchiveError(
            "permission_denied",
            "无权读取源数据",
            EXIT_PERMISSION,
        ) from exc
    except ArchiveError:
        raise
    except OSError as exc:
        raise ArchiveError(
            "source_io_error",
            "读取源数据失败",
            EXIT_IO,
            retryable=True,
        ) from exc

    if (
        first_stat.st_dev != second_stat.st_dev
        or first_stat.st_ino != second_stat.st_ino
        or first_stat.st_dev != opened_stat.st_dev
        or first_stat.st_ino != opened_stat.st_ino
        or opened_stat.st_dev != opened_after_stat.st_dev
        or opened_stat.st_ino != opened_after_stat.st_ino
        or first_stat.st_size != second_stat.st_size
        or opened_stat.st_size != opened_after_stat.st_size
        or first_stat.st_mtime_ns != second_stat.st_mtime_ns
        or opened_stat.st_mtime_ns != opened_after_stat.st_mtime_ns
        or len(data) != first_stat.st_size
    ):
        raise ArchiveError(
            "source_changed",
            "源数据在读取期间发生变化",
            EXIT_CONSISTENCY,
            retryable=True,
        )
    return SourceMember(archive_path, data, int(first_stat.st_mtime))


def _read_regular_file(path: Path) -> bytes:
    return _read_source_member(path, "internal/read", before_second_stat=None).data


def _validate_destination(destination: Path) -> None:
    try:
        mode = destination.lstat().st_mode
    except FileNotFoundError as exc:
        raise ArchiveError(
            "destination_not_found",
            "目标目录不存在",
            EXIT_IO,
        ) from exc
    except PermissionError as exc:
        raise ArchiveError(
            "permission_denied",
            "无权访问目标目录",
            EXIT_PERMISSION,
        ) from exc
    except OSError as exc:
        raise ArchiveError(
            "destination_io_error",
            "无法检查目标目录",
            EXIT_IO,
        ) from exc
    if not stat.S_ISDIR(mode):
        raise ArchiveError(
            "destination_not_directory",
            "目标路径不是目录",
            EXIT_IO,
        )
    if not os.access(destination, os.W_OK | os.X_OK):
        raise ArchiveError(
            "destination_not_writable",
            "目标目录不可写",
            EXIT_PERMISSION,
        )


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except PermissionError as exc:
        raise ArchiveError("permission_denied", "无权检查源数据", EXIT_PERMISSION) from exc
    except OSError as exc:
        raise ArchiveError("source_io_error", "无法检查源数据", EXIT_IO) from exc
    return True


def _require_directory(path: Path, error_code: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ArchiveError(
            "required_source_missing",
            "必需数据目录缺失",
            EXIT_CONSISTENCY,
        ) from exc
    except PermissionError as exc:
        raise ArchiveError("permission_denied", "无权读取源数据目录", EXIT_PERMISSION) from exc
    except OSError as exc:
        raise ArchiveError("source_io_error", "无法检查源数据目录", EXIT_IO) from exc
    if not stat.S_ISDIR(mode):
        raise ArchiveError(error_code, "源数据目录不安全", EXIT_CONSISTENCY)


def _validate_archive_path(archive_path: str) -> None:
    path = PurePosixPath(archive_path)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ArchiveError(
            "unsafe_archive_path",
            "归档逻辑路径不安全",
            EXIT_CONSISTENCY,
        )


def _add_tar_bytes(archive: tarfile.TarFile, member: SourceMember) -> None:
    _validate_archive_path(member.archive_path)
    info = tarfile.TarInfo(member.archive_path)
    info.size = len(member.data)
    info.mtime = member.mtime
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info, io.BytesIO(member.data))


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _mask_email(email: str) -> str:
    local, separator, domain = email.strip().partition("@")
    if not separator:
        return "***"
    visible = local[:1] if local else ""
    return f"{visible}***@{domain}"


def _make_request_id(factory: Callable[[], str] | None) -> str:
    request_id = (factory or (lambda: secrets.token_hex(6)))()
    if len(request_id) != 12 or any(character not in "0123456789abcdef" for character in request_id):
        raise ArchiveError(
            "invalid_request_id",
            "内部请求 ID 格式无效",
            EXIT_CONSISTENCY,
        )
    return request_id


def _cleanup_temp(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
