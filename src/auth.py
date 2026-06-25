"""认证模块 — 封装 garmy AuthClient，处理登录与 Token 生命周期。

支持：
- 自动检测已有 Token 有效性
- 首次登录 / Token 过期重新登录
- MFA 二次验证（交互式输入，支持两阶段 MFA 流程）
- 日志中脱敏显示邮箱
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from garmy import AuthClient

from .config import Config

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """脱敏显示邮箱地址。"""
    if "@" not in email:
        return email[:2] + "***"
    local, domain = email.rsplit("@", 1)
    if len(local) <= 3:
        return f"{local[0]}***@{domain}"
    return f"{local[:3]}***@{domain}"


def _prompt_mfa() -> str:
    """MFA 验证码交互式输入回调。"""
    try:
        code = input("请输入 Garmin 二次验证码 (MFA): ").strip()
        return code
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


class AuthManager:
    """Garmin 认证管理器。

    封装 AuthClient 的登录和 Token 管理，
    对外提供已认证的 AuthClient 实例。
    """

    def __init__(self, config: Config, mfa_handler: Callable[[], str] | None = None):
        self._config = config
        self._mfa_handler = mfa_handler or _prompt_mfa
        self._client: AuthClient | None = None

    @property
    def client(self) -> AuthClient:
        """获取已认证的 AuthClient（懒初始化）。"""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> AuthClient:
        """创建并认证 AuthClient。"""
        logger.info(
            "创建 AuthClient (domain=%s, email=%s)",
            self._config.domain,
            _mask_email(self._config.email),
        )

        client = AuthClient(
            domain=self._config.domain,
            token_dir=self._config.token_dir,
        )

        # 尝试用已有 Token（is_authenticated 是 property）
        if self._try_existing_token(client):
            logger.info("✅ Token 有效，无需重新登录")
            return client

        # 执行登录 (garmy 2.0 API: prompt_mfa + return_on_mfa)
        logger.info("🔐 执行登录...")
        try:
            result = client.login(
                email=self._config.email,
                password=self._config.password,
                prompt_mfa=self._mfa_handler,
                return_on_mfa=True,  # 两阶段 MFA: 先返回 needs_mfa 状态
            )
            # 检查是否需要 MFA
            if isinstance(result, tuple) and result[0] == "needs_mfa":
                logger.info("🔐 需要 MFA 二次验证")
                mfa_code = self._mfa_handler()
                client.resume_login(mfa_code, result[1])
            logger.info("✅ 登录成功")
        except Exception as exc:
            logger.error("❌ 登录失败: %s", exc)
            raise

        return client

    @staticmethod
    def _try_existing_token(client: AuthClient) -> bool:
        """尝试用已有 Token 认证，返回是否有效。"""
        try:
            # garmy 2.0: is_authenticated 是 property
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
