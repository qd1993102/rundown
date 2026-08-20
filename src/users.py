"""用户管理模块 — 应用账号、API Key 与数据路径映射。

Web 用户通过邀请码创建昵称/邮箱/密码账号。服务端仍用随机 API Key
作为 Cookie 会话标识和数据目录键，运动平台凭证与应用账号分开管理。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .local_files import atomic_write_private, ensure_private_dir

logger = logging.getLogger(__name__)

_KEY_PREFIX = "rd_"
_PASSWORD_SCHEME = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


class UserExistsError(ValueError):
    """注册邮箱已存在。"""


def generate_api_key() -> str:
    """生成用户 API Key。"""
    return _KEY_PREFIX + secrets.token_hex(16)


def normalize_email(email: str) -> str:
    """生成用于登录与唯一性比较的邮箱。"""
    return email.strip().casefold()


def hash_password(password: str) -> str:
    """使用带随机盐的 scrypt 保存密码。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join([
        _PASSWORD_SCHEME,
        str(_SCRYPT_N),
        str(_SCRYPT_R),
        str(_SCRYPT_P),
        salt.hex(),
        digest.hex(),
    ])


def verify_password(password: str, encoded: str) -> bool:
    """校验 scrypt 密码哈希；格式错误返回 False。"""
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if scheme != _PASSWORD_SCHEME:
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


@dataclass
class UserRecord:
    """用户注册记录；只保存应用密码哈希和运动平台 Token 状态。"""

    api_key: str
    nickname: str = ""
    email: str = ""
    password_hash: str = ""
    provider: str = "garmin"
    garmin_domain: str = "garmin.com"
    garmin_email: str = ""
    created: str = ""
    last_sync: str = ""
    token_status: str = "none"  # none | active | expired

    @property
    def has_account(self) -> bool:
        """是否具备可通过邮箱密码重新登录的应用账号。"""
        return bool(self.email and self.password_hash)

    def to_dict(self) -> dict[str, str]:
        return {
            "api_key": self.api_key,
            "nickname": self.nickname,
            "email": self.email,
            "password_hash": self.password_hash,
            "provider": self.provider,
            "garmin_domain": self.garmin_domain,
            "garmin_email": self.garmin_email,
            "created": self.created,
            "last_sync": self.last_sync,
            "token_status": self.token_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserRecord":
        return cls(
            api_key=str(data.get("api_key", "")),
            nickname=str(data.get("nickname", "")),
            email=str(data.get("email", "")),
            password_hash=str(data.get("password_hash", "")),
            provider=str(data.get("provider") or "garmin"),
            garmin_domain=str(data.get("garmin_domain") or "garmin.com"),
            garmin_email=str(data.get("garmin_email", "")),
            created=str(data.get("created", "")),
            last_sync=str(data.get("last_sync", "")),
            token_status=str(data.get("token_status") or "none"),
        )


class UserManager:
    """用户管理器 — 注册表读写、应用登录与路径映射。

    每个用户存储在 ``data_dir/users/<api_key>.json``。
    """

    def __init__(self, data_dir: str):
        self._data_dir = Path(data_dir)
        self._users_dir = self._data_dir / "users"
        ensure_private_dir(self._data_dir)
        ensure_private_dir(self._users_dir)
        self._cache: dict[str, UserRecord] = {}
        self._lock = threading.RLock()

    def register_account(self, nickname: str, email: str, password: str) -> UserRecord:
        """创建可登录的应用账号。调用方须先完成邀请码校验。"""
        normalized_email = normalize_email(email)
        with self._lock:
            if self.find_by_email(normalized_email) is not None:
                raise UserExistsError("该邮箱已注册，请直接登录")

            record = UserRecord(
                api_key=generate_api_key(),
                nickname=nickname.strip(),
                email=normalized_email,
                password_hash=hash_password(password),
                created=str(date.today()),
            )
            self._save(record)
            self._cache[record.api_key] = record
        logger.info("新应用用户注册: key=%s", record.api_key)
        return record

    def authenticate(self, email: str, password: str) -> UserRecord | None:
        """按应用邮箱和密码登录。"""
        record = self.find_by_email(email)
        if record is None or not record.password_hash:
            return None
        return record if verify_password(password, record.password_hash) else None

    def change_password(
        self,
        api_key: str,
        current_password: str,
        new_password: str,
    ) -> UserRecord | None:
        """校验当前密码后更新为新密码哈希；当前密码错误或账号不存在返回 None。"""
        with self._lock:
            record = self.get(api_key)
            if record is None or not record.password_hash:
                return None
            if not verify_password(current_password, record.password_hash):
                return None
            record.password_hash = hash_password(new_password)
            self._save(record)
            self._cache[api_key] = record
            return record

    def find_by_email(self, email: str) -> UserRecord | None:
        """按规范化邮箱查找应用账号。"""
        target = normalize_email(email)
        if not target:
            return None
        for record in self.list_all():
            if normalize_email(record.email) == target:
                return record
        return None

    def get(self, api_key: str) -> UserRecord | None:
        """通过 API Key 查找用户。"""
        if api_key in self._cache:
            return self._cache[api_key]
        path = self._user_path(api_key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            record = UserRecord.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError):
            logger.warning("无法读取用户文件: %s", path)
            return None
        if not record.has_account:
            return None
        self._cache[api_key] = record
        return record

    def update(self, api_key: str, **kwargs) -> UserRecord | None:
        """更新用户字段。"""
        with self._lock:
            record = self.get(api_key)
            if record is None:
                return None
            for key, value in kwargs.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            self._save(record)
            self._cache[api_key] = record
            return record

    def delete_account(self, api_key: str) -> None:
        """删除刚创建但未能完成邀请码核销的账号。"""
        self._cache.pop(api_key, None)
        try:
            self._user_path(api_key).unlink(missing_ok=True)
        except OSError:
            logger.exception("回滚未完成注册失败: key=%s", api_key)

    def list_all(self) -> list[UserRecord]:
        """列出所有注册用户。"""
        records = []
        for path in sorted(self._users_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                record = UserRecord.from_dict(data)
                if record.has_account:
                    records.append(record)
            except Exception:
                logger.warning("跳过无效用户文件: %s", path)
        return records

    def get_token_dir(self, api_key: str) -> str:
        return str(self._data_dir / api_key / "tokens")

    def get_memory_dir(self, api_key: str) -> str:
        return str(self._data_dir / api_key / "memory")

    def get_db_path(self, api_key: str) -> str:
        return str(self._data_dir / api_key / "data.db")

    def get_sync_tasks_path(self, api_key: str) -> str:
        return str(self._data_dir / api_key / "sync-tasks.json")

    def get_backup_dir(self, api_key: str) -> str:
        return str(self._data_dir / "backup")

    def get_backup_path(self, api_key: str) -> str:
        return str(self._data_dir / "backup" / f"{api_key}.db")

    def _user_path(self, api_key: str) -> Path:
        return self._users_dir / f"{api_key}.json"

    def _save(self, record: UserRecord) -> None:
        path = self._user_path(record.api_key)
        atomic_write_private(
            path,
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n",
        )

    def ensure_dirs(self, api_key: str) -> None:
        """确保用户数据目录存在。"""
        ensure_private_dir(Path(self.get_db_path(api_key)).parent)
        ensure_private_dir(self.get_token_dir(api_key))
        ensure_private_dir(self.get_memory_dir(api_key))
        ensure_private_dir(self.get_backup_dir(api_key))
