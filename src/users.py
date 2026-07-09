"""用户管理模块 — 注册表、API Key、路径映射。

Web 模式下每个用户通过 API Key 标识，数据按 Key 隔离。
不依赖微信 openid — 服务端自闭环生成 Key，通过 Cookie 维持会话。
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import date, datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# API Key 前缀 + 随机 hex（32 字符 = 128 bit 熵）
_KEY_PREFIX = "rd_"


def generate_api_key() -> str:
    """生成用户 API Key。"""
    return _KEY_PREFIX + secrets.token_hex(16)


@dataclass
class UserRecord:
    """用户注册记录。不存 Garmin 密码，只存 Token 状态。"""

    api_key: str
    provider: str = "garmin"
    garmin_domain: str = "garmin.com"
    garmin_email: str = ""
    created: str = ""
    last_sync: str = ""
    token_status: str = "none"  # none | active | expired

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "provider": self.provider,
            "garmin_domain": self.garmin_domain,
            "garmin_email": self.garmin_email,
            "created": self.created,
            "last_sync": self.last_sync,
            "token_status": self.token_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserRecord":
        return cls(**{k: data.get(k, "") for k in [
            "api_key", "provider", "garmin_domain", "garmin_email",
            "created", "last_sync", "token_status",
        ]})


class UserManager:
    """用户管理器 — 注册表读写、路径映射。

    注册表存储在 data_dir/users.json，每用户一个 JSON 文件。
    """

    def __init__(self, data_dir: str):
        self._data_dir = Path(data_dir)
        self._users_dir = self._data_dir / "users"
        self._users_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, UserRecord] = {}

    # ── 注册表 CRUD ────────────────────────────

    def register(self, garmin_email: str = "",
                 garmin_domain: str = "garmin.com",
                 provider: str = "garmin") -> UserRecord:
        """注册新用户，返回含 api_key 的记录。"""
        api_key = generate_api_key()
        record = UserRecord(
            api_key=api_key,
            provider=provider,
            garmin_domain=garmin_domain,
            garmin_email=garmin_email,
            created=str(date.today()),
            token_status="none",
        )
        self._save(record)
        self._cache[api_key] = record
        logger.info("新用户注册: key=%s email=%s", api_key, garmin_email)
        return record

    def get(self, api_key: str) -> UserRecord | None:
        """通过 API Key 查找用户。"""
        if api_key in self._cache:
            return self._cache[api_key]

        path = self._user_path(api_key)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        record = UserRecord.from_dict(data)
        self._cache[api_key] = record
        return record

    def update(self, api_key: str, **kwargs) -> UserRecord | None:
        """更新用户字段。"""
        record = self.get(api_key)
        if record is None:
            return None

        for k, v in kwargs.items():
            if hasattr(record, k):
                setattr(record, k, v)

        self._save(record)
        self._cache[api_key] = record
        return record

    def list_all(self) -> list[UserRecord]:
        """列出所有注册用户。"""
        records = []
        for path in sorted(self._users_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append(UserRecord.from_dict(data))
            except Exception:
                logger.warning("跳过无效用户文件: %s", path)
        return records

    # ── 路径映射 ───────────────────────────────

    def get_token_dir(self, api_key: str) -> str:
        return str(self._data_dir / api_key / "tokens")

    def get_memory_dir(self, api_key: str) -> str:
        return str(self._data_dir / api_key / "memory")

    def get_db_path(self, api_key: str) -> str:
        return str(self._data_dir / api_key / "data.db")

    def get_backup_dir(self, api_key: str) -> str:
        return str(self._data_dir / "backup")

    def get_backup_path(self, api_key: str) -> str:
        return str(self._data_dir / "backup" / f"{api_key}.db")

    # ── 内部方法 ───────────────────────────────

    def _user_path(self, api_key: str) -> Path:
        return self._users_dir / f"{api_key}.json"

    def _save(self, record: UserRecord) -> None:
        path = self._user_path(record.api_key)
        path.write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def ensure_dirs(self, api_key: str) -> None:
        """确保用户数据目录存在。"""
        Path(self.get_token_dir(api_key)).mkdir(parents=True, exist_ok=True)
        Path(self.get_memory_dir(api_key)).mkdir(parents=True, exist_ok=True)
        Path(self.get_backup_dir(api_key)).mkdir(parents=True, exist_ok=True)
        Path(self.get_db_path(api_key)).parent.mkdir(parents=True, exist_ok=True)
