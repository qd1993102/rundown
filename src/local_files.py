"""本地敏感数据的私有目录与原子文件写入工具。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class LocalPersistenceError(RuntimeError):
    """本地持久化目录或文件不可安全读写。"""


def _error(action: str, path: Path, exc: OSError) -> LocalPersistenceError:
    return LocalPersistenceError(
        f"无法{action} {path}: {exc}；请确认运行用户拥有该路径，"
        f"必要时执行 chown -R <服务用户>:<服务组> {path.parent}"
    )


def ensure_private_dir(path: str | Path) -> Path:
    """创建目录并将权限收紧为仅当前用户可访问。"""
    target = Path(path)
    try:
        target.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        os.chmod(target, PRIVATE_DIR_MODE)
    except OSError as exc:
        raise _error("创建或收紧私有目录", target, exc) from exc
    return target


def restrict_private_file(path: str | Path) -> Path:
    """将已有文件权限收紧为仅当前用户可读写。"""
    target = Path(path)
    if not target.exists():
        return target
    try:
        os.chmod(target, PRIVATE_FILE_MODE)
    except OSError as exc:
        raise _error("收紧私有文件权限", target, exc) from exc
    return target


def read_private_text(path: str | Path, *, encoding: str = "utf-8") -> str:
    """收紧权限后读取敏感文本，并保留路径与所有者修复提示。"""
    target = Path(path)
    restrict_private_file(target)
    try:
        return target.read_text(encoding=encoding)
    except OSError as exc:
        raise _error("读取私有文件", target, exc) from exc


def atomic_write_private(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    private_parent: bool = True,
) -> Path:
    """使用同目录临时文件原子写入敏感文本，最终权限固定为 0600。"""
    target = Path(path)
    if private_parent:
        ensure_private_dir(target.parent)
    else:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _error("创建文件目录", target.parent, exc) from exc
    temp = target.parent / (
        f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    )
    fd: int | None = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            fd = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        os.chmod(target, PRIVATE_FILE_MODE)
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise _error("原子写入私有文件", target, exc) from exc
    return target
