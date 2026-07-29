"""Coros Training Hub 可重放登录凭据的加密持久化。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..local_files import (
    LocalPersistenceError,
    atomic_write_private,
    read_private_text,
)


class CorosCredentialError(RuntimeError):
    """Coros 自动重登凭据不可安全使用。"""


class CorosCredentialKeyUnavailable(CorosCredentialError):
    """服务端没有可用的凭据加密密钥。"""


class CorosCredentialCorrupted(CorosCredentialError):
    """加密凭据损坏、密钥不匹配或内容不合法。"""


class CorosReloginCredentialStore:
    """按用户保存加密的 Training Hub 登录重放对象。"""

    _FILENAME = "coros-relogin.enc"

    def __init__(self, token_dir: str | None, key: str):
        self._token_dir_configured = bool(token_dir)
        self.path = Path(token_dir or ".") / self._FILENAME
        self._key = key.strip()

    @property
    def configured(self) -> bool:
        return bool(self._key)

    @property
    def enabled(self) -> bool:
        return self.configured and self.path.exists()

    def _fernet(self) -> Fernet:
        if not self._key:
            raise CorosCredentialKeyUnavailable(
                "未配置 Coros 自动续期加密密钥"
            )
        try:
            return Fernet(self._key.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise CorosCredentialKeyUnavailable(
                "Coros 自动续期加密密钥格式无效"
            ) from exc

    def save(self, account: str, password: str, region: str) -> None:
        """保存加密重放对象；明文密码和摘要都不得落盘。"""
        if not self._token_dir_configured:
            raise CorosCredentialError("Coros 自动续期缺少用户 Token 目录")
        payload = {
            "account": account.strip(),
            "accountType": 2,
            "pwd": hashlib.md5(
                password.encode("utf-8"), usedforsecurity=False,
            ).hexdigest(),
            "region": region.strip(),
            "version": 1,
        }
        if not payload["account"] or not password or not payload["region"]:
            raise CorosCredentialError("Coros 自动续期凭据不完整")
        plaintext = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        ciphertext = self._fernet().encrypt(plaintext).decode("ascii")
        atomic_write_private(self.path, ciphertext + "\n")

    def load(self) -> dict[str, object]:
        """解密并校验重放对象，不在异常中包含任何凭据。"""
        if not self.path.exists():
            raise CorosCredentialError("Coros 自动续期未启用")
        try:
            ciphertext = read_private_text(self.path).strip().encode("ascii")
            plaintext = self._fernet().decrypt(ciphertext)
            payload = json.loads(plaintext.decode("utf-8"))
        except CorosCredentialKeyUnavailable:
            raise
        except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CorosCredentialCorrupted(
                "Coros 自动续期凭据无法解密或内容损坏"
            ) from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise CorosCredentialCorrupted("Coros 自动续期凭据版本不受支持")
        required = ("account", "accountType", "pwd", "region")
        if any(not payload.get(field) for field in required):
            raise CorosCredentialCorrupted("Coros 自动续期凭据内容不完整")
        if (
            payload.get("accountType") != 2
            or payload.get("region") not in {"eu", "us", "cn", "asia"}
            or not re.fullmatch(r"[0-9a-f]{32}", str(payload.get("pwd")))
        ):
            raise CorosCredentialCorrupted("Coros 自动续期凭据字段不合法")
        return payload

    def delete(self) -> None:
        """幂等删除自动重登凭据。"""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise LocalPersistenceError(
                f"无法删除 Coros 自动续期凭据 {self.path}: {exc}"
            ) from exc


class CorosMobileCredentialStore(CorosReloginCredentialStore):
    """按用户保存加密的 Coros Mobile 登录重放对象。"""

    _FILENAME = "coros-mobile-relogin.enc"

    def save(self, login_payload: dict[str, object], region: str) -> None:
        if not self._token_dir_configured:
            raise CorosCredentialError("Coros 睡眠自动鉴权缺少用户 Token 目录")
        payload = {
            "login_payload": login_payload,
            "region": region.strip(),
            "version": 1,
        }
        if (
            not login_payload
            or payload["region"] not in {"eu", "us", "cn", "asia"}
        ):
            raise CorosCredentialError("Coros 睡眠自动鉴权凭据不完整")
        try:
            plaintext = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CorosCredentialError("Coros 睡眠自动鉴权凭据格式无效") from exc
        ciphertext = self._fernet().encrypt(plaintext).decode("ascii")
        atomic_write_private(self.path, ciphertext + "\n")

    def load(self) -> dict[str, object]:
        if not self.path.exists():
            raise CorosCredentialError("Coros 睡眠自动鉴权未启用")
        try:
            ciphertext = read_private_text(self.path).strip().encode("ascii")
            plaintext = self._fernet().decrypt(ciphertext)
            payload = json.loads(plaintext.decode("utf-8"))
        except CorosCredentialKeyUnavailable:
            raise
        except (
            InvalidToken, UnicodeError, ValueError, TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise CorosCredentialCorrupted(
                "Coros 睡眠自动鉴权凭据无法解密或内容损坏"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("region") not in {"eu", "us", "cn", "asia"}
            or not isinstance(payload.get("login_payload"), dict)
            or not payload["login_payload"]
        ):
            raise CorosCredentialCorrupted("Coros 睡眠自动鉴权凭据内容不合法")
        return payload
