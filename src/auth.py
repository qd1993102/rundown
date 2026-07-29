"""认证模块 — 封装 garmy AuthClient，处理登录与 Token 生命周期。

支持：
- 自动检测已有 Token 有效性
- 首次登录 / Token 过期重新登录
- MFA 二次验证（交互式输入 / Web 两步流程）
- 日志中脱敏显示邮箱
"""

from __future__ import annotations

import logging
import sys
import time
from typing import TYPE_CHECKING, Any, Callable

from garmy import AuthClient

if TYPE_CHECKING:
    from garmy import APIClient

from .config import Config
from .resource_lifecycle import close_runtime_resources

logger = logging.getLogger(__name__)

# MFA 中间状态存储（Web 模式共享，5 分钟过期）
_mfa_states: dict[str, dict] = {}


def _discard_mfa_state(session_key: str) -> bool:
    """删除一个 MFA 状态并关闭其认证会话。"""
    state = _mfa_states.pop(session_key, None)
    if state is None:
        return False
    close_runtime_resources(state.get("client"))
    return True


def _mask_email(email: str) -> str:
    """脱敏显示邮箱地址。"""
    if "@" not in email:
        return email[:2] + "***"
    local, domain = email.rsplit("@", 1)
    if len(local) <= 3:
        return f"{local[0]}***@{domain}"
    return f"{local[:3]}***@{domain}"


def _prompt_mfa() -> str:
    """MFA 验证码交互式输入回调（CLI 模式）。"""
    try:
        code = input("请输入运动平台二次验证码 (MFA): ").strip()
        return code
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


def _mfa_not_available() -> str:
    """非交互环境的 MFA 处理器 — 抛出明确错误。"""
    raise RuntimeError(
        "需要 MFA 二次验证，但当前环境不支持交互输入。\n"
        "Web 用户请通过 /api/mfa 提交验证码。\n"
        "CLI 用户请在有终端的设备上运行 neurun sync。"
    )


def get_mfa_state(session_key: str) -> dict | None:
    """获取 MFA 中间状态（Web 模式）。过期自动清除。"""
    state = _mfa_states.get(session_key)
    if state and state.get("expires", 0) > time.time():
        return state
    _discard_mfa_state(session_key)
    return None


def cleanup_expired_mfa_states() -> int:
    """清理过期的 MFA 状态，返回清理数量。"""
    now = time.time()
    expired = [k for k, v in _mfa_states.items() if v.get("expires", 0) <= now]
    for k in expired:
        _discard_mfa_state(k)
    return len(expired)


class AuthManager:
    """运动平台认证管理器。

    支持两种模式：
    - CLI 模式：登录时交互式输入 MFA
    - Web 模式：start_login() 返回状态，complete_mfa() 完成验证
    """

    def __init__(self, config: Config, mfa_handler: Callable[[], str] | None = None):
        self._config = config
        self._mfa_handler = mfa_handler or (
            _mfa_not_available if getattr(config, "non_interactive", False) else _prompt_mfa
        )
        self._client: AuthClient | None = None

    @property
    def client(self) -> AuthClient:
        """获取已认证的 AuthClient（懒初始化，CLI 模式）。"""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def create_api_client(self) -> APIClient:
        """创建与当前 Garmin 认证区域一致的数据 APIClient。"""
        from garmy import APIClient

        return APIClient(
            auth_client=self.client,
            domain=self._config.domain,
        )

    # ── CLI 模式（保持向后兼容）──────────────────

    def _create_client(self) -> AuthClient:
        """创建并认证 AuthClient（CLI 一键模式）。"""
        logger.info(
            "创建 AuthClient (domain=%s, email=%s)",
            self._config.domain,
            _mask_email(self._config.email),
        )

        client = AuthClient(
            domain=self._config.domain,
            token_dir=self._config.token_dir,
        )

        if self._try_existing_token(client):
            logger.info("✅ Token 有效，无需重新登录")
            return client

        logger.info("🔐 执行登录...")
        try:
            result = client.login(
                email=self._config.email,
                password=self._config.password,
                prompt_mfa=self._mfa_handler,
                return_on_mfa=True,
            )
            if isinstance(result, tuple) and result[0] == "needs_mfa":
                logger.info("🔐 需要 MFA 二次验证")
                mfa_code = self._mfa_handler()
                client.resume_login(mfa_code, result[1])
            logger.info("✅ 登录成功")
        except Exception as exc:
            logger.error("❌ 登录失败: %s", exc)
            raise

        return client

    # ── Web 模式（两步 MFA）─────────────────────

    def start_login(self, email: str, password: str,
                    session_key: str, mfa_timeout: int = 300) -> str:
        """启动登录流程（Web 模式），不阻塞等待 MFA。

        返回 "ok"（无需 MFA）或 "needs_mfa"（需 MFA）。
        如需 MFA，状态保留在 _mfa_states[session_key] 中。
        """
        logger.info("Web 登录启动: %s", _mask_email(email))
        cleanup_expired_mfa_states()
        _discard_mfa_state(session_key)

        client = AuthClient(
            domain=self._config.domain,
            token_dir=self._config.token_dir,
        )

        # 尝试已有 Token
        if self._try_existing_token(client):
            logger.info("✅ Token 有效，无需重新登录")
            self._client = client
            return "ok"

        try:
            result = client.login(
                email=email,
                password=password,
                prompt_mfa=_mfa_not_available,
                return_on_mfa=True,
            )

            if isinstance(result, tuple) and result[0] == "needs_mfa":
                # 保存 MFA 中间状态
                _mfa_states[session_key] = {
                    "client": client,
                    "mfa_state": result[1],
                    "expires": time.time() + mfa_timeout,
                }
                logger.info("🔐 MFA 已触发，等待 Web 用户提交验证码 (session=%s)", session_key)
                return "needs_mfa"

            # 无需 MFA，直接登录成功
            self._client = client
            logger.info("✅ 登录成功（无需 MFA）")
            return "ok"

        except Exception as exc:
            close_runtime_resources(client)
            logger.error("❌ 登录失败: %s", exc)
            raise

    def complete_mfa(self, session_key: str, mfa_code: str) -> AuthClient:
        """提交 MFA 验证码完成登录（Web 模式）。

        返回已认证的 AuthClient。
        会话过期或无效时抛出 ValueError。
        """
        state = get_mfa_state(session_key)
        if state is None:
            raise ValueError("MFA 会话已过期或不存在，请重新发起绑定")

        client: AuthClient = state["client"]
        mfa_state = state["mfa_state"]
        del _mfa_states[session_key]

        try:
            client.resume_login(mfa_code, mfa_state)
            self._client = client
            logger.info("✅ MFA 验证通过，登录成功 (session=%s)", session_key)
        except Exception as exc:
            close_runtime_resources(client)
            logger.error("❌ MFA 验证失败: %s", exc)
            raise

        return client

    def close(self) -> None:
        """关闭当前认证客户端的 HTTP 会话；Token 文件保持不变。"""
        close_runtime_resources(self._client)
        self._client = None

    # ── 通用方法 ────────────────────────────────

    @staticmethod
    def _try_existing_token(client: AuthClient) -> bool:
        """尝试用已有 Token 认证，返回是否有效。"""
        try:
            return client.is_authenticated
        except Exception:
            return False

    def logout(self) -> None:
        """清除 Token。"""
        if self._client:
            try:
                self._client.logout()
                logger.info("已清除 Token")
            except Exception as exc:
                logger.warning("清除 Token 时出错: %s", exc)

    def get_headers(self) -> dict:
        """获取认证请求头（供手动 API 调用）。"""
        return self.client.get_auth_headers()
