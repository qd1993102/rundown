"""一次性邀请码的 JSON 存储与管理员操作。"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class InvitationError(ValueError):
    """邀请码不可用、目标不存在或配置无效。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mask_code(code: str) -> str:
    """生成可用于列表输出的邀请码掩码。"""
    if len(code) <= 10:
        return code[:2] + "***" + code[-2:]
    return code[:6] + "…" + code[-4:]


@dataclass(frozen=True)
class Invitation:
    """严格单次使用的邀请码记录。"""

    id: str
    code: str
    enabled: bool
    created_at: str
    used_by: str | None = None
    used_at: str | None = None

    @property
    def available(self) -> bool:
        return self.enabled and self.used_by is None

    def to_admin_dict(self, *, reveal: bool = False) -> dict[str, Any]:
        """转换为管理员输出；默认隐藏完整邀请码。"""
        data = asdict(self)
        data["code"] = self.code if reveal else _mask_code(self.code)
        data["available"] = self.available
        return data


class InvitationStore:
    """JSON 邀请码仓库。

    完整邀请码保存在管理员文件中。每个记录只能成功核销一次，
    不支持共享次数或自动过期。
    """

    def __init__(self, path: str):
        self._path = Path(path)
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def create(self, count: int = 1) -> list[Invitation]:
        """生成 count 个加密安全随机邀请码并写入仓库。"""
        if not 1 <= count <= 100:
            raise InvitationError("count 必须在 1 到 100 之间")
        with self._lock:
            data = self._read(create_if_missing=True)
            created = []
            existing_codes = {
                entry.get("code") for entry in data["codes"] if isinstance(entry, dict)
            }
            existing_ids = {
                entry.get("id") for entry in data["codes"] if isinstance(entry, dict)
            }
            for _ in range(count):
                while True:
                    code = "neurun_" + secrets.token_urlsafe(24)
                    invitation_id = "inv_" + secrets.token_hex(8)
                    if code not in existing_codes and invitation_id not in existing_ids:
                        break
                invitation = Invitation(
                    id=invitation_id,
                    code=code,
                    enabled=True,
                    created_at=_utc_now(),
                )
                data["codes"].append(asdict(invitation))
                created.append(invitation)
                existing_codes.add(code)
                existing_ids.add(invitation_id)
            self._write(data)
            return created

    def list_all(self) -> list[Invitation]:
        """列出全部邀请码记录，按创建顺序返回。"""
        with self._lock:
            return [self._parse(entry) for entry in self._read()["codes"]]

    def get(self, invitation_id: str) -> Invitation:
        """按管理员 ID 获取邀请码。"""
        with self._lock:
            entry = self._find_by_id(self._read(), invitation_id.strip())
            if entry is None:
                raise InvitationError(f"邀请码不存在: {invitation_id}")
            return self._parse(entry)

    def revoke(self, invitation_id: str) -> Invitation:
        """停用邀请码；已使用的邀请码也保留停用状态用于审计。"""
        with self._lock:
            data = self._read()
            entry = self._find_by_id(data, invitation_id.strip())
            if entry is None:
                raise InvitationError(f"邀请码不存在: {invitation_id}")
            entry["enabled"] = False
            self._write(data)
            return self._parse(entry)

    def validate(self, code: str) -> bool:
        """返回邀请码当前是否可用；配置错误会抛出 InvitationError。"""
        normalized = code.strip()
        if not normalized:
            return False
        with self._lock:
            entry = self._find_by_code(self._read(), normalized)
            return self._parse(entry).available if entry is not None else False

    def consume(self, code: str, account_email: str) -> None:
        """核销邀请码并原子写回 JSON。"""
        normalized = code.strip()
        with self._lock:
            data = self._read()
            entry = self._find_by_code(data, normalized)
            if entry is None or not self._parse(entry).available:
                raise InvitationError("邀请码无效、已停用或已经使用")
            entry["used_by"] = account_email
            entry["used_at"] = _utc_now()
            self._write(data)

    def _read(self, *, create_if_missing: bool = False) -> dict[str, Any]:
        if not self._path.exists():
            if create_if_missing:
                return {"codes": []}
            raise InvitationError(
                f"邀请码文件不存在: {self._path}；请先运行 neurun invite create"
            )
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvitationError(f"无法读取邀请码文件 {self._path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("codes"), list):
            raise InvitationError("邀请码文件格式错误：根对象必须包含 codes 数组")
        seen_ids: set[str] = set()
        seen_codes: set[str] = set()
        for entry in data["codes"]:
            invitation = self._parse(entry)
            if invitation.id in seen_ids or invitation.code in seen_codes:
                raise InvitationError("邀请码文件包含重复的 id 或 code")
            seen_ids.add(invitation.id)
            seen_codes.add(invitation.code)
        return data

    @staticmethod
    def _find_by_code(data: dict[str, Any], code: str) -> dict[str, Any] | None:
        for entry in data["codes"]:
            if isinstance(entry, dict) and entry.get("code") == code:
                return entry
        return None

    @staticmethod
    def _find_by_id(data: dict[str, Any], invitation_id: str) -> dict[str, Any] | None:
        for entry in data["codes"]:
            if isinstance(entry, dict) and entry.get("id") == invitation_id:
                return entry
        return None

    @staticmethod
    def _parse(entry: Any) -> Invitation:
        if not isinstance(entry, dict):
            raise InvitationError("邀请码记录必须是对象")
        if "max_uses" in entry or "uses" in entry:
            raise InvitationError("邀请码只允许单次使用，不支持 max_uses 或 uses")
        required = ("id", "code", "created_at")
        if any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in required):
            raise InvitationError("邀请码记录缺少非空 id、code 或 created_at")
        if not isinstance(entry.get("enabled", True), bool):
            raise InvitationError(f"邀请码 {entry['id']} 的 enabled 必须是布尔值")
        used_by = entry.get("used_by")
        used_at = entry.get("used_at")
        if used_by is not None and not isinstance(used_by, str):
            raise InvitationError(f"邀请码 {entry['id']} 的 used_by 必须是字符串或 null")
        if used_at is not None and not isinstance(used_at, str):
            raise InvitationError(f"邀请码 {entry['id']} 的 used_at 必须是字符串或 null")
        if (used_by is None) != (used_at is None):
            raise InvitationError(f"邀请码 {entry['id']} 的 used_by 与 used_at 必须同时设置")
        return Invitation(
            id=entry["id"],
            code=entry["code"],
            enabled=entry.get("enabled", True),
            created_at=entry["created_at"],
            used_by=used_by,
            used_at=used_at,
        )

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            temp_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self._path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise InvitationError(f"无法更新邀请码文件 {self._path}: {exc}") from exc
