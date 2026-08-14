"""Coros Provider — 基于 coros-mcp 库。

pip install git+https://github.com/cygnusb/coros-mcp.git
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..local_files import (
    LocalPersistenceError,
    atomic_write_private,
    ensure_private_dir,
    read_private_text,
)

from .base import (
    ActivityData, DailyHealth,
    AuthProvider, ActivityProvider, HealthProvider, DataProvider,
)
from .coros_credentials import (
    CorosCredentialError,
    CorosMobileCredentialStore,
    CorosReloginCredentialStore,
)

logger = logging.getLogger(__name__)

# Region-specific base URLs (ref: coros-mcp)
_BASE_URLS = {
    "eu": "https://teameuapi.coros.com", "us": "https://teamapi.coros.com",
    "cn": "https://teamcnapi.coros.com", "asia": "https://teamcnapi.coros.com",
}


class CorosDependencyError(RuntimeError):
    """Coros 运行依赖未随应用安装。"""


class CorosAuthenticationError(RuntimeError):
    """Coros Training Hub 凭据已明确失效。"""


class CorosReloginRejected(CorosAuthenticationError):
    """Coros 明确拒绝保存的重登凭据。"""


_RELOGIN_LOCKS: dict[str, threading.Lock] = {}
_RELOGIN_LOCKS_GUARD = threading.Lock()


def _relogin_lock(path: Path | None) -> threading.Lock:
    key = str(path) if path is not None else "unconfigured"
    with _RELOGIN_LOCKS_GUARD:
        return _RELOGIN_LOCKS.setdefault(key, threading.Lock())


def _is_training_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "result=1019" in message or "access token is invalid" in message


def _is_mobile_auth_error(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    message = str(exc).lower()
    return (
        code == "1019"
        or "result=1019" in message
        or "token invalid" in message
        or "no mobile api token available" in message
    )


def _format_mobile_auth_error(exc: Exception) -> str:
    """把上游 Mobile 登录异常转换为不包含凭据的用户可见原因。"""
    code = str(getattr(exc, "code", "") or "").strip()
    message = " ".join(str(exc).split())
    if code:
        return f"Coros Mobile 授权失败（{code}）：{message or '高驰拒绝了登录请求'}"
    if "No accessToken" in message:
        return "Coros Mobile 授权失败：高驰未返回睡眠访问凭据"
    if type(exc).__module__.startswith("httpx"):
        return "Coros Mobile 授权失败：无法连接高驰 Mobile 服务，请稍后重试"
    return "Coros Mobile 授权失败：高驰未完成 Mobile 登录"


def _base_for_auth(auth) -> str:
    if hasattr(auth, '_auth') and hasattr(auth._auth, 'region'):
        return _BASE_URLS.get(auth._auth.region, _BASE_URLS["us"])
    return _BASE_URLS["us"]


def _int_value(value: Any) -> int:
    """将 Coros 的数字/字符串字段安全转为整数。"""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _calories_kcal(value: Any) -> int:
    """Convert Coros milli-kilocalories to the canonical kcal unit."""
    try:
        return round(float(value or 0) / 1000.0)
    except (TypeError, ValueError):
        return 0


def _parse_activity_item(item: dict[str, Any]) -> ActivityData:
    """将 Coros 原始活动映射为统一模型，运动时长排除暂停时间。"""
    total_time = _int_value(item.get("totalTime"))
    workout_time = _int_value(item.get("workoutTime"))
    if workout_time > 0 and (total_time <= 0 or workout_time <= total_time):
        active_time = workout_time
    else:
        active_time = total_time

    timestamp = _int_value(item.get("startTime"))
    start_time = (
        datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if timestamp > 100000 else str(timestamp)
    )
    sport_type = str(item.get("sportType", 100))
    sport_map = {
        "100": "running", "101": "running_indoor", "200": "cycling",
        "300": "swimming", "500": "strength",
    }

    return ActivityData(
        activity_id=str(item.get("labelId", "")),
        activity_name=item.get("name") or item.get("remark") or "训练",
        activity_type=sport_map.get(sport_type, sport_type),
        start_time=start_time,
        duration_seconds=active_time,
        distance_meters=float(item.get("distance") or item.get("totalDistance") or 0),
        avg_heart_rate=item.get("avgHr"),
        max_heart_rate=item.get("maxHr"),
        training_load=float(item.get("trainingLoad") or 0),
        calories=_calories_kcal(item.get("calorie")),
        elevation_gain=_optional_float_value(
            item, ("ascent", "totalAscent", "elevationGain"),
        ),
        extra={
            "provider": "coros",
            "sport_type": sport_type,
            "total_time_seconds": total_time,
            "workout_time_seconds": workout_time,
            "paused_seconds": max(total_time - active_time, 0),
        },
    )


def _optional_float_value(
    item: dict[str, Any], keys: tuple[str, ...],
) -> float | None:
    """保留 Provider 缺失值；真实零值仍返回 0.0。"""
    for key in keys:
        if key not in item or item[key] is None:
            continue
        try:
            return float(item[key])
        except (TypeError, ValueError):
            return None
    return None


def _emit_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    payload: dict[str, Any],
) -> None:
    """进度回调失败不得掩盖 Coros 主同步结果。"""
    if callback is None:
        return
    try:
        callback(payload)
    except Exception as exc:
        logger.warning(
            "Coros 同步进度上报失败: error_type=%s", type(exc).__name__,
        )


def _fetch_activity_items(
    auth: CorosAuth,
    start: date,
    end: date,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """直接读取 Coros 活动列表原始字段，保留 coros-mcp 丢弃的 workoutTime。"""
    import httpx

    items: list[dict[str, Any]] = []
    page = 1
    size = 100
    with httpx.Client(timeout=30) as client:
        while True:
            def load_page() -> dict:
                response = client.get(
                    f"{_base_for_auth(auth)}/activity/query",
                    params={
                        "startDay": start.strftime("%Y%m%d"),
                        "endDay": end.strftime("%Y%m%d"),
                        "pageNumber": page,
                        "size": size,
                    },
                    headers=auth.get_headers(),
                )
                response.raise_for_status()
                body = response.json()
                if body.get("result") != "0000":
                    raise RuntimeError(
                        f"{body.get('message', 'unknown error')} "
                        f"(result={body.get('result', 'unknown')})"
                    )
                return body

            body = auth.run_with_training_relogin(load_page)
            data = body.get("data") or {}
            page_items = data.get("dataList", data.get("list", [])) or []
            items.extend(page_items)
            total = _int_value(data.get("totalCount") or data.get("count"))
            finished = (
                not page_items
                or len(page_items) < size
                or (total and len(items) >= total)
            )
            if page_items and (total or finished):
                _emit_progress(progress_callback, {
                    "current": len(items),
                    "total": total or len(items),
                    "date": None,
                    "metric": "activities",
                    "outcome": "completed",
                })
            if finished:
                break
            page += 1
    return items


def _run(coro):
    """同步包装器。"""
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as ex:
            return ex.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


class CorosAuth(AuthProvider):
    """Coros 认证，并按 neurun 用户目录隔离持久化 token。"""

    _TOKEN_FILENAME = "coros-auth.json"

    def __init__(
        self,
        token_dir: str | None = None,
        *,
        credential_key: str = "",
        remember_credentials: bool | None = None,
    ):
        self._auth: Any = None
        self._sleep_auth_error: str | None = None
        self._auto_relogin_warning: str | None = None
        self._sleep_auto_refresh_warning: str | None = None
        self._remember_credentials = remember_credentials
        self._token_path = (
            Path(token_dir).expanduser() / self._TOKEN_FILENAME
            if token_dir else None
        )
        self.credential_store = CorosReloginCredentialStore(
            token_dir, credential_key,
        )
        self.mobile_credential_store = CorosMobileCredentialStore(
            token_dir, credential_key,
        )
        self._restore()

    def _restore(self) -> bool:
        """从用户专属 token 文件恢复认证，不读取 coros-mcp 的全局凭证。"""
        if self._token_path is None or not self._token_path.exists():
            return False
        try:
            from coros_mcp.models import StoredAuth
            raw = read_private_text(self._token_path)
            if hasattr(StoredAuth, "model_validate_json"):
                self._auth = StoredAuth.model_validate_json(raw)
            else:  # pragma: no cover - pydantic v1 compatibility
                self._auth = StoredAuth.parse_raw(raw)
            legacy_mobile_payload = getattr(
                self._auth, "mobile_login_payload", None,
            )
            if legacy_mobile_payload:
                try:
                    self.mobile_credential_store.save(
                        legacy_mobile_payload, str(self._auth.region),
                    )
                except CorosCredentialError as exc:
                    self._sleep_auto_refresh_warning = str(exc)
                self._auth.mobile_login_payload = None
                self._save()
            return True
        except LocalPersistenceError:
            raise
        except Exception as exc:
            self._auth = None
            logger.warning("Coros token 恢复失败，请重新绑定账号: %s", exc)
            return False

    def _save(self) -> None:
        """以 0700 目录、0600 文件权限保存当前用户的 Coros token。"""
        if self._token_path is None or self._auth is None:
            return
        ensure_private_dir(self._token_path.parent)
        serializable = self._auth
        if hasattr(self._auth, "model_copy"):
            serializable = self._auth.model_copy(update={
                "mobile_login_payload": None,
            })
        elif hasattr(self._auth, "copy"):  # pragma: no cover - pydantic v1
            serializable = self._auth.copy(update={"mobile_login_payload": None})
        if hasattr(serializable, "model_dump_json"):
            raw = serializable.model_dump_json()
        else:  # pragma: no cover - pydantic v1 compatibility
            raw = serializable.json()
        atomic_write_private(self._token_path, raw)

    def migrate_legacy_token(self) -> bool:
        """将 coros-mcp 旧版全局 token 迁移到当前用户目录。"""
        if self.is_authenticated():
            return True
        try:
            from coros_mcp.coros_api import get_stored_auth
            legacy_auth = get_stored_auth()
            if (legacy_auth is None or not legacy_auth.access_token
                    or not legacy_auth.user_id):
                return False
            self._auth = legacy_auth
            self._save()
            logger.info("Coros: 已将旧版全局 token 迁移到用户目录")
            return True
        except LocalPersistenceError:
            raise
        except Exception as exc:
            self._auth = None
            logger.warning("Coros 旧版 token 迁移失败: %s", exc)
            return False

    def login_training(
        self,
        account: str,
        password: str,
        region: str,
        *,
        auto_refresh: bool = True,
    ) -> bool:
        """只认证 Training Hub；不触发 Coros Mobile 登录。"""
        try:
            import coros_mcp.models  # noqa: F401
        except ModuleNotFoundError as exc:
            if exc.name == "coros_mcp" or "coros_mcp" in str(exc):
                raise CorosDependencyError(
                    "Coros 运行依赖未安装；请重新执行 pip install -e . 并重启服务，"
                    "Docker 部署请重新构建镜像"
                ) from exc
            raise
        account = account.strip()
        region = region.strip().lower()
        if not account or not password or region not in _BASE_URLS:
            return False
        logger.info(
            "Coros: Training Hub 登录 (region=%s, account=%s...)",
            region, account[:3],
        )
        try:
            previous_mobile_token = getattr(
                self._auth, "mobile_access_token", None,
            )
            self._auth = self._login_training_password(
                account, password, region,
            )
            self._auth.mobile_access_token = previous_mobile_token
            self._save()
            self._auto_relogin_warning = None
            if auto_refresh:
                try:
                    self.credential_store.save(account, password, region)
                except CorosCredentialError as exc:
                    self._auto_relogin_warning = str(exc)
                    logger.warning(
                        "Coros 自动续期未启用: error_type=%s",
                        type(exc).__name__,
                    )
            else:
                self.credential_store.delete()
            logger.info(
                "Coros: Training Hub 登录成功 (user_id=%s)",
                self._auth.user_id,
            )
            return True
        except LocalPersistenceError:
            raise
        except Exception as e:
            logger.error("Coros 登录失败: %s", e)
            return False

    def _login_training_password(
        self, account: str, password: str, region: str,
    ):
        """直接登录 Training Hub，避免上游 `_save_auth` 写全局 Token。"""
        import httpx
        from coros_mcp.coros_api import USER_AGENT
        from coros_mcp.models import StoredAuth

        response = httpx.post(
            f"{_BASE_URLS[region]}/account/login",
            json={
                "account": account,
                "accountType": 2,
                "pwd": hashlib.md5(
                    password.encode("utf-8"), usedforsecurity=False,
                ).hexdigest(),
            },
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if str(body.get("result") or "") != "0000":
            raise CorosAuthenticationError(
                f"Coros Training Hub 登录失败 (result={body.get('result') or 'unknown'})"
            )
        data = body.get("data") or {}
        if not data.get("accessToken") or not data.get("userId"):
            raise ValueError("Coros Training Hub 登录响应缺少必要字段")
        return StoredAuth(
            access_token=str(data["accessToken"]),
            user_id=str(data["userId"]),
            region=region,
            timestamp=int(time.time() * 1000),
        )

    def login_sleep(
        self,
        email: str,
        password: str,
        *,
        auto_refresh: bool = True,
    ) -> bool:
        """只认证 Coros App Mobile；保留现有 Training Hub Token。"""
        if self._auth is None or "@" not in email or not password:
            self._sleep_auth_error = "Coros App 睡眠认证需要登录邮箱和密码"
            return False
        try:
            from coros_mcp.coros_api import _mobile_login
        except ModuleNotFoundError as exc:
            if exc.name == "coros_mcp" or "coros_mcp" in str(exc):
                raise CorosDependencyError(
                    "Coros 运行依赖未安装；请重新安装依赖并重启服务"
                ) from exc
            raise
        region = str(getattr(self._auth, "region", "eu"))
        self._sleep_auth_error = None
        self._sleep_auto_refresh_warning = None
        try:
            mobile_token, mobile_payload = _run(
                _mobile_login(email.strip(), password, region)
            )
            self._auth.mobile_access_token = mobile_token
            self._auth.mobile_login_payload = None
            if auto_refresh:
                try:
                    self.mobile_credential_store.save(mobile_payload, region)
                except CorosCredentialError as exc:
                    self._sleep_auto_refresh_warning = str(exc)
            else:
                self.mobile_credential_store.delete()
            self._save()
            return True
        except LocalPersistenceError:
            raise
        except Exception as exc:
            self._sleep_auth_error = _format_mobile_auth_error(exc)
            logger.warning("Coros: %s", self._sleep_auth_error)
            return False

    def login(self, email: str, password: str) -> bool:
        """兼容旧调用：只绑定 Training Hub。"""
        region = "cn" if (email.isdigit() and len(email) >= 10) else "eu"
        return self.login_training(
            email, password, region,
            auto_refresh=self._remember_credentials is True,
        )

    def is_authenticated(self) -> bool:
        return self._auth is not None

    @property
    def auto_relogin_enabled(self) -> bool:
        return self.credential_store.enabled

    @property
    def auto_relogin_warning(self) -> str | None:
        return self._auto_relogin_warning

    @property
    def sleep_auto_refresh_enabled(self) -> bool:
        return self.mobile_credential_store.enabled

    @property
    def sleep_auto_refresh_warning(self) -> str | None:
        return self._sleep_auto_refresh_warning

    def has_sleep_access(self) -> bool:
        """当前用户是否已持久化 Coros Mobile 睡眠凭据。"""
        return bool(
            self._auth is not None
            and (
                getattr(self._auth, "mobile_access_token", None)
                or self.mobile_credential_store.enabled
            )
        )

    @property
    def sleep_auth_error(self) -> str | None:
        """本次登录的脱敏 Mobile 授权错误；不持久化账号、密码或 token。"""
        return self._sleep_auth_error

    def get_user_id(self) -> int:
        return int(self._auth.user_id) if self._auth else 0

    def get_headers(self) -> dict[str, str]:
        if self._auth is None:
            return {}
        import json as _json
        return {
            "accessToken": self._auth.access_token,
            "yfheader": _json.dumps({"userId": self._auth.user_id}),
        }

    def _login_with_replay(self, payload: dict[str, object]):
        """使用解密后的密码等价重放对象换取新的 Training Hub Token。"""
        import httpx
        from coros_mcp.coros_api import USER_AGENT
        from coros_mcp.models import StoredAuth

        region = str(payload["region"])
        login_payload = {
            "account": str(payload["account"]),
            "accountType": int(payload["accountType"]),
            "pwd": str(payload["pwd"]),
        }
        response = httpx.post(
            f"{_BASE_URLS.get(region, _BASE_URLS['us'])}/account/login",
            json=login_payload,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise CorosReloginRejected("Coros access token is invalid；自动重登凭据被拒绝")
        response.raise_for_status()
        body = response.json()
        result = str(body.get("result") or "")
        if result in {"1001", "1019"}:
            raise CorosReloginRejected("Coros access token is invalid；自动重登凭据被拒绝")
        if result != "0000":
            raise RuntimeError(f"Coros 自动重登暂时失败 (result={result or 'unknown'})")
        data = body.get("data") or {}
        access_token = str(data.get("accessToken") or "")
        user_id = str(data.get("userId") or "")
        if not access_token or not user_id:
            raise ValueError("Coros 自动重登响应缺少必要字段")
        previous_user_id = str(getattr(self._auth, "user_id", ""))
        if previous_user_id and user_id != previous_user_id:
            raise CorosReloginRejected("Coros access token is invalid；自动重登账号不一致")
        return StoredAuth(
            access_token=access_token,
            user_id=user_id,
            region=region,
            timestamp=int(time.time() * 1000),
            mobile_access_token=getattr(self._auth, "mobile_access_token", None),
            mobile_login_payload=getattr(self._auth, "mobile_login_payload", None),
        )

    def _reload_if_token_changed(self, failed_token: str) -> bool:
        if self._token_path is None or not self._token_path.exists():
            return False
        previous = self._auth
        if self._restore():
            current = str(getattr(self._auth, "access_token", ""))
            if current and current != failed_token:
                return True
        if self._auth is None:
            self._auth = previous
        return False

    def _refresh_after_invalid(self, failed_token: str) -> None:
        with _relogin_lock(self._token_path):
            current = str(getattr(self._auth, "access_token", ""))
            if current and current != failed_token:
                return
            if self._reload_if_token_changed(failed_token):
                return
            try:
                payload = self.credential_store.load()
            except CorosCredentialError as exc:
                raise CorosAuthenticationError(
                    "Coros access token is invalid，且没有可用的自动续期凭据"
                ) from exc
            try:
                refreshed = self._login_with_replay(payload)
            except CorosReloginRejected:
                self.credential_store.delete()
                raise
            self._auth = refreshed
            self._save()
            logger.info("Coros: Training Hub Token 已自动更新")

    def run_with_training_relogin(self, operation: Callable[[], Any]) -> Any:
        """执行 Training Hub 请求，明确失效时自动重登并最多重试一次。"""
        failed_token = str(getattr(self._auth, "access_token", ""))
        try:
            return operation()
        except Exception as exc:
            if not _is_training_auth_error(exc):
                raise
        self._refresh_after_invalid(failed_token)
        try:
            return operation()
        except Exception as exc:
            if not _is_training_auth_error(exc):
                raise
            self.credential_store.delete()
            raise CorosAuthenticationError(
                "Coros access token 自动重登后仍然失效，请重新授权"
            ) from exc

    def _refresh_mobile_with_replay(self) -> None:
        """在 neurun 用户域内重放 Mobile 登录，不写 coros-mcp 全局凭据。"""
        import httpx
        from coros_mcp.coros_api import (
            MOBILE_BASE_URLS, MOBILE_LOGIN_ENDPOINT,
        )

        stored = self.mobile_credential_store.load()
        region = str(stored["region"])
        response = httpx.post(
            f"{MOBILE_BASE_URLS[region]}{MOBILE_LOGIN_ENDPOINT}",
            json=stored["login_payload"],
            headers={
                "content-type": "application/json",
                "accept-encoding": "gzip",
                "user-agent": "okhttp/4.12.0",
                "request-time": str(int(time.time() * 1000)),
            },
            timeout=30,
        )
        if response.status_code in {401, 403}:
            raise CorosReloginRejected("Coros Mobile 自动鉴权凭据被拒绝")
        response.raise_for_status()
        body = response.json()
        result = str(body.get("result") or "")
        if result in {"1001", "1019"}:
            raise CorosReloginRejected("Coros Mobile 自动鉴权凭据被拒绝")
        if result != "0000":
            raise RuntimeError(
                f"Coros Mobile 自动鉴权暂时失败 (result={result or 'unknown'})"
            )
        token = str((body.get("data") or {}).get("accessToken") or "")
        if not token:
            raise ValueError("Coros Mobile 自动鉴权响应缺少 accessToken")
        self._auth.mobile_access_token = token
        self._auth.mobile_login_payload = None
        self._save()

    def run_with_sleep_relogin(self, operation: Callable[[], Any]) -> Any:
        """执行 Mobile 请求，失效时在用户域内自动鉴权并重试一次。"""
        failed_token = str(getattr(self._auth, "mobile_access_token", ""))
        try:
            result = operation()
            self._save()
            return result
        except Exception as exc:
            if not _is_mobile_auth_error(exc):
                raise

        lock_path = (
            self._token_path.with_name("coros-mobile-auth")
            if self._token_path else None
        )
        with _relogin_lock(lock_path):
            if self._token_path is not None and self._token_path.exists():
                self._restore()
            current = str(getattr(self._auth, "mobile_access_token", ""))
            if current == failed_token:
                try:
                    self._refresh_mobile_with_replay()
                except CorosReloginRejected:
                    self.mobile_credential_store.delete()
                    self._auth.mobile_access_token = None
                    self._save()
                    raise
        try:
            result = operation()
            self._save()
            return result
        except Exception as exc:
            if not _is_mobile_auth_error(exc):
                raise
            self.mobile_credential_store.delete()
            self._auth.mobile_access_token = None
            self._save()
            raise CorosAuthenticationError(
                "Coros Mobile 自动鉴权后仍然失效，请重新认证睡眠数据"
            ) from exc


class CorosActivity(ActivityProvider):
    def __init__(self, auth: CorosAuth):
        self._auth = auth

    def fetch_activities(
        self,
        start: date,
        end: date,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[ActivityData]:
        if not self._auth.is_authenticated():
            return []
        try:
            raw = _fetch_activity_items(
                self._auth, start, end,
                progress_callback=progress_callback,
            )
        except Exception as e:
            logger.error("Coros 获取活动失败: %s", e)
            raise RuntimeError(f"Coros 获取活动失败，请重新绑定账号或稍后重试: {e}") from e
        result = [_parse_activity_item(item) for item in raw]
        logger.info("Coros: %d 条活动 (%s ~ %s)", len(result), start, end)
        return result

    def fetch_activity_detail(
        self, activity_id: str, sport_type: int = 0,
    ) -> dict[str, Any]:
        """拉取 Coros 活动详情，保留 graphList/frequencyList 等全部字段。

        coros-mcp 的 fetch_activity_detail 会主动丢弃 graphList / frequencyList /
        gpsLightDuration 高频时序数组（见 coros_api.py 的 strip 逻辑）；这里自建请求
        保留原始响应，供 Hz 级分段配速分析与结构确认。sport_type 为活动类型数字
        （跑步 100/102/103、骑行 200/2 等），由调用方从活动项 extra 传入。
        """
        import httpx

        auth = self._auth
        if auth is None or not auth.is_authenticated():
            return {}
        # Coros detail/query 端点不接受 application/json，须用默认 form 编码；
        # accessToken / yfheader 头来自统一凭据。
        headers = {
            **auth.get_headers(),
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
        }
        url = _base_for_auth(auth) + "/activity/detail/query"
        form = {
            "labelId": str(activity_id),
            "userId": str(auth.get_user_id()),
            "sportType": str(sport_type or 0),
        }
        try:
            resp = httpx.post(url, data=form, headers=headers, timeout=30)
            body = resp.json()
        except Exception as exc:
            logger.warning(
                "Coros 活动详情拉取失败 activity_id=%s error_type=%s",
                activity_id, type(exc).__name__,
            )
            return {}
        if str(body.get("result")) != "0000" or "data" not in body:
            logger.warning(
                "Coros 活动详情响应异常 activity_id=%s result=%s message=%s",
                activity_id, body.get("result"), str(body.get("message"))[:80],
            )
            return {}
        data = body.get("data") or {}
        if not isinstance(data, dict):
            return {}
        # 记录高频时序字段结构，便于确认 Hz 级配速/心率数据的格式。
        for key in ("graphList", "frequencyList", "gpsLightDuration"):
            value = data.get(key)
            if value is not None:
                logger.info(
                    "Coros detail 高频字段: %s type=%s len=%s",
                    key, type(value).__name__,
                    len(value) if hasattr(value, "__len__") else "?",
                )
        return data


class CorosHealth(HealthProvider):
    def __init__(self, auth: CorosAuth):
        self._auth = auth
        self._analyse_cache: dict | None = None
        self._hrv_cache: list | None = None
        self._sleep_cache: dict[str, Any] = {}
        self._sleep_loaded_dates: set[str] = set()
        # Note: cache persists for process lifetime — fine for CLI (short-lived),
        # but MCP server may need restart to pick up new Coros data.

    def _get_analyse_data(self) -> dict:
        """获取 analyse/query 数据（缓存）。"""
        if self._analyse_cache is not None:
            return self._analyse_cache
        import httpx
        try:
            def load_analyse() -> dict:
                response = httpx.get(
                    f"{_base_for_auth(self._auth)}/analyse/query",
                    headers=self._auth.get_headers(),
                    timeout=15,
                )
                response.raise_for_status()
                body = response.json()
                if body.get("result") != "0000":
                    raise RuntimeError(
                        f"{body.get('message', 'unknown error')} "
                        f"(result={body.get('result', 'unknown')})"
                    )
                return body.get("data") or {}

            self._analyse_cache = self._auth.run_with_training_relogin(
                load_analyse,
            )
        except Exception as exc:
            if _is_training_auth_error(exc) or isinstance(
                exc, CorosAuthenticationError,
            ):
                raise
            self._analyse_cache = {}
        return self._analyse_cache or {}

    def _get_hrv_data(self) -> list:
        """获取 HRV 数据（缓存）。"""
        if self._hrv_cache is not None:
            return self._hrv_cache
        try:
            from coros_mcp.coros_api import fetch_hrv
            self._hrv_cache = self._auth.run_with_training_relogin(
                lambda: _run(fetch_hrv(self._auth._auth)),
            )
        except Exception as exc:
            if _is_training_auth_error(exc) or isinstance(
                exc, CorosAuthenticationError,
            ):
                raise
            self._hrv_cache = []
        return self._hrv_cache or []

    def _load_sleep_range(self, start: date, end: date) -> None:
        """批量读取 Mobile API 睡眠数据；失败时保留其他健康指标。"""
        start_day = start.strftime("%Y%m%d")
        end_day = end.strftime("%Y%m%d")
        dates: list[str] = []
        current = start
        while current <= end:
            dates.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
        if all(day in self._sleep_loaded_dates for day in dates):
            return
        self._sleep_loaded_dates.update(dates)

        if not self._auth.has_sleep_access():
            logger.info("Coros: 当前凭据未启用睡眠权限，请重新绑定账号")
            return

        try:
            from coros_mcp.coros_api import fetch_sleep
            records = self._auth.run_with_sleep_relogin(
                lambda: _run(fetch_sleep(
                    self._auth._auth, start_day, end_day,
                )),
            )
        except LocalPersistenceError:
            raise
        except Exception as exc:
            logger.warning("Coros 获取睡眠失败，继续同步活动和其他健康指标: %s", exc)
            return

        for record in records or []:
            record_date = str(getattr(record, "date", ""))
            if record_date:
                self._sleep_cache[record_date] = record

    def fetch_daily_health(self, target_date: date) -> DailyHealth | None:
        self._load_sleep_range(target_date, target_date)
        # Build from analyse data (RHR, load, distance, duration)
        analyse = self._get_analyse_data()
        day_list = analyse.get("dayList", []) or []
        t7_list = analyse.get("t7dayList", []) or []

        rhr = None
        distance = 0.0
        duration = 0.0
        training_load = 0.0
        stress_level = None  # Coros: tiredRate

        ts_compact = target_date.strftime("%Y%m%d")
        lthr = None
        ltsp = None
        for item in day_list:
            if str(item.get("happenDay", "")) == ts_compact:
                rhr = item.get("rhr")
                distance = float(item.get("distance", 0) or 0)
                duration = float(item.get("duration", 0) or 0)
                training_load = float(item.get("trainingLoad", 0) or 0)
                stress_level = item.get("tiredRate")  # 0-100 fatigue index
                # 平台自算乳酸阈值（/analyse/query）：lthr=阈值心率 bpm，ltsp=阈值配速 s/km
                lthr = _int_value(item.get("lthr")) if item.get("lthr") is not None else None
                ltsp = _int_value(item.get("ltsp")) if item.get("ltsp") is not None else None
                break

        # Check t7dayList for RHR if not found
        if rhr is None:
            for item in t7_list:
                if str(item.get("happenDay", "")) == ts_compact:
                    rhr = item.get("rhr")
                    break

        # HRV from dashboard
        hrv_val = None
        hrv_status = "balanced"
        for r in self._get_hrv_data():
            if str(getattr(r, 'date', '')) == ts_compact:
                hrv_val = getattr(r, 'avg_sleep_hrv', None)
                break

        sleep_record = self._sleep_cache.get(ts_compact)
        sleep_minutes = _int_value(
            getattr(sleep_record, "total_duration_minutes", None)
        )
        phases = getattr(sleep_record, "phases", None)
        deep_minutes = _int_value(getattr(phases, "deep_minutes", None))
        rem_minutes = _int_value(getattr(phases, "rem_minutes", None))
        deep_pct = (
            round(deep_minutes / sleep_minutes * 100, 2)
            if sleep_minutes > 0 else 0
        )
        rem_pct = (
            round(rem_minutes / sleep_minutes * 100, 2)
            if sleep_minutes > 0 else 0
        )

        # If we have at least sleep, HRV, RHR, or distance, return data.
        if sleep_minutes > 0 or rhr is not None or hrv_val is not None or distance > 0:
            return DailyHealth(
                metric_date=target_date,
                sleep_duration_hours=round(sleep_minutes / 60, 2),
                deep_sleep_hours=round(deep_minutes / 60, 2),
                rem_sleep_hours=round(rem_minutes / 60, 2),
                deep_sleep_pct=deep_pct,
                rem_sleep_pct=rem_pct,
                resting_heart_rate=rhr,
                hrv_last_night_avg=hrv_val,
                hrv_weekly_avg=hrv_val,
                hrv_status=hrv_status,
                total_distance_meters=distance,
                total_steps=0,
                avg_stress_level=stress_level,
                extra={
                    "provider": "coros",
                    "sleep_quality_score": getattr(
                        sleep_record, "quality_score", None
                    ),
                    "awake_minutes": _int_value(
                        getattr(phases, "awake_minutes", None)
                    ),
                    "nap_minutes": _int_value(
                        getattr(phases, "nap_minutes", None)
                    ),
                    "daily_duration_seconds": duration,
                    "daily_training_load": training_load,
                    "lthr": lthr,
                    "ltsp": ltsp,
                },
            )

        return None

    def fetch_health_range(
        self,
        start: date,
        end: date,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[DailyHealth]:
        self._load_sleep_range(start, end)
        result = []
        d = start
        total = (end - start).days + 1
        current = 0
        while d <= end:
            current += 1
            try:
                h = self.fetch_daily_health(d)
            except Exception:
                _emit_progress(progress_callback, {
                    "current": current,
                    "total": total,
                    "date": d.isoformat(),
                    "metric": "daily_health",
                    "outcome": "failed",
                })
                raise
            if h:
                result.append(h)
            _emit_progress(progress_callback, {
                "current": current,
                "total": total,
                "date": d.isoformat(),
                "metric": "daily_health",
                "outcome": "completed" if h else "skipped",
            })
            d += timedelta(days=1)
        return result


class CorosProvider(DataProvider):
    def __init__(self, config):
        self._config = config
        self.auth = CorosAuth(
            getattr(config, "token_dir", None),
            credential_key=getattr(config, "coros_credential_key", ""),
            remember_credentials=getattr(config, "coros_auto_relogin", None),
        )
        self._activities = CorosActivity(self.auth)
        self._health = CorosHealth(self.auth)

    def authenticate(self) -> bool:
        # Web 后续请求不保存密码，使用绑定时写入的用户隔离 token。
        if self.auth.is_authenticated() and not self._config.password:
            return True
        if not self._config.email or not self._config.password:
            logger.error("Coros 认证信息不可用，请重新绑定账号")
            return False
        return self.auth.login(self._config.email, self._config.password)

    @property
    def sleep_available(self) -> bool:
        """当前 Coros 绑定是否具备 Mobile 睡眠读取能力。"""
        return self.auth.has_sleep_access()

    @property
    def sleep_auth_error(self) -> str | None:
        """本次 Coros Mobile 授权失败的可操作原因。"""
        return self.auth.sleep_auth_error

    @property
    def sleep_auto_refresh_enabled(self) -> bool:
        return self.auth.sleep_auto_refresh_enabled

    @property
    def sleep_auto_refresh_warning(self) -> str | None:
        return self.auth.sleep_auto_refresh_warning

    @property
    def auto_relogin_enabled(self) -> bool:
        return self.auth.auto_relogin_enabled

    @property
    def auto_relogin_warning(self) -> str | None:
        return self.auth.auto_relogin_warning

    @property
    def user_id(self) -> int:
        return self.auth.get_user_id()

    @property
    def activities(self) -> ActivityProvider:
        return self._activities

    @property
    def health(self) -> HealthProvider:
        return self._health
