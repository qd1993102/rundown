"""配置模块 — 统一管理所有配置项，从环境变量读取并校验。

使用 python-dotenv 支持 .env 文件，优先级：
系统环境变量 > .env 文件 > 默认值
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .local_files import (
    LocalPersistenceError,
    atomic_write_private,
    read_private_text,
)

logger = logging.getLogger(__name__)

DEFAULT_AI_BASE_URL = "https://api.deepseek.com"
DEFAULT_AI_MODEL = "deepseek-chat"


def huawei_user_key(group_pals_token: str) -> str:
    """从敏感 token 派生不可逆的稳定本地目录键。"""
    if not group_pals_token:
        return "unconfigured"
    return hashlib.sha256(group_pals_token.encode()).hexdigest()[:16]


def _default_huawei_token_dir() -> str:
    key = huawei_user_key(os.getenv("GROUP_PALS_TOKEN", ""))
    return str(Path.home() / ".neurun" / "users" / key / "huawei-tokens")


class ConfigError(Exception):
    """配置错误。"""


@dataclass(frozen=True)
class AIConfig:
    """OpenAI-compatible AI 服务配置。"""

    api_key: str = field(repr=False)
    base_url: str
    model: str

    @property
    def chat_completions_url(self) -> str:
        """返回 Chat Completions 完整端点，同时兼容传入根地址或完整端点。"""
        normalized = self.base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        return f"{normalized}/chat/completions"


def get_ai_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> AIConfig:
    """读取供应商无关的 AI 配置。"""
    resolved_key = api_key or os.getenv("NEURUN_AI_API_KEY", "")
    resolved_base_url = base_url or os.getenv(
        "NEURUN_AI_BASE_URL", DEFAULT_AI_BASE_URL,
    )
    resolved_model = model or os.getenv("NEURUN_AI_MODEL", DEFAULT_AI_MODEL)
    return AIConfig(
        api_key=resolved_key.strip(),
        base_url=resolved_base_url.strip().rstrip("/") or DEFAULT_AI_BASE_URL,
        model=resolved_model.strip() or DEFAULT_AI_MODEL,
    )


def _mask_email(email: str) -> str:
    """脱敏显示邮箱地址。"""
    if "@" not in email:
        return email[:2] + "***"
    local, domain = email.rsplit("@", 1)
    if len(local) <= 3:
        return f"{local[0]}***@{domain}"
    return f"{local[:3]}***@{domain}"


@dataclass
class Config:
    """应用配置，所有值从环境变量读取。"""

    # ── 必填（NEURUN_ACCOUNT 为主，兼容旧名 RUNDOWN_*/GARMIN_*）──
    email: str = field(
        default_factory=lambda: (os.getenv("NEURUN_ACCOUNT") or
                                 os.getenv("RUNDOWN_ACCOUNT") or
                                 os.getenv("NEURUN_EMAIL") or
                                 os.getenv("RUNDOWN_EMAIL") or
                                 os.getenv("GARMIN_EMAIL", ""))
    )
    password: str = field(
        default_factory=lambda: (os.getenv("NEURUN_PASSWORD") or
                                os.getenv("RUNDOWN_PASSWORD") or
                                os.getenv("GARMIN_PASSWORD", ""))
    )

    # ── 数据源 ────────────────────────────────
    provider_type: str = field(
        default_factory=lambda: os.getenv("NEURUN_PROVIDER") or
                               os.getenv("RUNDOWN_PROVIDER", "garmin")
    )
    # Garmin 专用
    domain: str = field(
        default_factory=lambda: os.getenv("GARMIN_DOMAIN", "garmin.com")
    )
    token_dir: str = field(
        default_factory=lambda: os.getenv(
            "GARMIN_TOKEN_DIR",
            os.path.expanduser("~/.garmy"),
        )
    )
    # Huawei 凭证代理（每个用户独立的 CrewPals token）
    group_pals_token: str = field(default_factory=lambda: os.getenv("GROUP_PALS_TOKEN", ""))
    huawei_token_dir: str = field(default_factory=lambda: os.getenv(
        "HUAWEI_TOKEN_DIR", _default_huawei_token_dir()))
    coros_credential_key: str = field(
        default_factory=lambda: os.getenv("NEURUN_COROS_CREDENTIAL_KEY", ""),
        repr=False,
    )

    # ── 存储 ──────────────────────────────────
    db_path: str = field(
        default_factory=lambda: os.getenv("NEURUN_DB_PATH") or
                               os.getenv("RUNDOWN_DB_PATH") or
                               os.getenv("GARMIN_DB_PATH",
            str(Path.home() / ".neurun" / "data.db"),
        )
    )
    sync_days: int = field(
        default_factory=lambda: int(os.getenv("NEURUN_SYNC_DAYS") or
                                   os.getenv("RUNDOWN_SYNC_DAYS") or
                                   os.getenv("GARMIN_SYNC_DAYS", "30"))
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("NEURUN_LOG_LEVEL") or
                               os.getenv("RUNDOWN_LOG_LEVEL") or
                               os.getenv("GARMIN_LOG_LEVEL", "INFO")
    )

    # ── 内部路径 ──────────────────────────────
    memory_dir: str = field(
        default_factory=lambda: os.getenv("NEURUN_MEMORY_DIR") or
                               os.getenv("RUNDOWN_MEMORY_DIR", "./memory")
    )

    # ── 工作目录（可选，设后所有相对路径基于此解析）──
    rundown_home: str = field(
        default_factory=lambda: os.getenv("NEURUN_HOME", "")
    )

    # ── Web 服务 ────────────────────────────────
    data_dir: str = field(
        default_factory=lambda: os.getenv("NEURUN_DATA_DIR") or
                               os.getenv("RUNDOWN_DATA_DIR", "./data")
    )
    invite_codes_file: str = field(
        default_factory=lambda: os.getenv("NEURUN_INVITE_CODES_FILE", "")
    )
    sync_max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("NEURUN_SYNC_MAX_CONCURRENCY", "4"))
    )
    sync_max_pending: int = field(
        default_factory=lambda: int(os.getenv("NEURUN_SYNC_MAX_PENDING", "100"))
    )
    ai_max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("NEURUN_AI_MAX_CONCURRENCY", "32"))
    )
    ai_max_pending: int = field(
        default_factory=lambda: int(os.getenv("NEURUN_AI_MAX_PENDING", "64"))
    )
    ai_wait_timeout_seconds: float = field(
        default_factory=lambda: float(
            os.getenv("NEURUN_AI_WAIT_TIMEOUT_SECONDS", "5"),
        )
    )
    non_interactive: bool = field(
        default_factory=lambda: (
            os.getenv("NEURUN_NON_INTERACTIVE") or
            os.getenv("RUNDOWN_NON_INTERACTIVE", "")
        ).lower() in ("1", "true", "yes")
    )

    def validate(self) -> None:
        """校验必填配置项，缺失则抛出 ConfigError。"""
        missing: list[str] = []
        if self.provider_type == "huawei":
            if not self.group_pals_token:
                missing.append("GROUP_PALS_TOKEN")
        else:
            if not self.email:
                missing.append("NEURUN_ACCOUNT")
            if not self.password:
                missing.append("NEURUN_PASSWORD")

        if missing:
            raise ConfigError(
                f"缺少必填环境变量: {', '.join(missing)}\n"
                f"请复制 .env.example 为 .env 并填入真实值\n"
                f"(也支持旧名 NEURUN_EMAIL / GARMIN_EMAIL)"
            )

    def log_config(self) -> None:
        """打印配置信息（敏感信息脱敏）。"""
        logger.info("配置加载完成:")
        logger.info("  Provider:        %s", self.provider_type)
        if self.provider_type != "huawei":
            logger.info("  Email:           %s", _mask_email(self.email))
        logger.info("  DB:              %s", self.db_path)
        logger.info("  Sync days:       %s", self.sync_days)
        logger.info("  Sync concurrency:%s", self.sync_max_concurrency)
        logger.info("  Sync pending max:%s", self.sync_max_pending)
        logger.info("  AI concurrency:  %s", self.ai_max_concurrency)
        logger.info("  AI pending max:  %s", self.ai_max_pending)
        logger.info("  AI wait timeout: %ss", self.ai_wait_timeout_seconds)
        logger.info("  Log level:       %s", self.log_level)
        if self.provider_type == "garmin":
            logger.info("  Domain:          %s", self.domain)
            logger.info("  Token dir:       %s", self.token_dir)


    def for_user(self, api_key: str) -> "UserConfig":
        """为该用户生成专属配置（路径按 api_key 隔离）。"""
        return UserConfig(api_key=api_key, parent=self)

    @property
    def invite_codes_path(self) -> str:
        """邀请码 JSON 路径。

        优先级：
        1. `NEURUN_INVITE_CODES_FILE` 显式指定；ECS/容器部署推荐设置为
           持久化数据目录的绝对路径（如 `/var/lib/neurun/invite-codes.json`），
           保证 release 切换后生成与读取位置一致。
        2. `<data_dir>/invite-codes.json`；其中 data_dir 为相对路径时固定基于
           项目根目录（`src/` 的上级）解析，不随进程 cwd 漂移，避免 ECS
           release 目录切换导致邀请码生成与读取落在不同文件。
        """
        if self.invite_codes_file:
            return self.invite_codes_file
        data_dir = Path(self.data_dir)
        if not data_dir.is_absolute():
            data_dir = Path(__file__).resolve().parent.parent / data_dir
        return str(data_dir / "invite-codes.json")


@dataclass
class UserConfig:
    """用户专属配置 — 所有路径按 api_key 隔离。

    email / password / _domain 由 web 层从用户注册表中注入，
    供 AuthManager 在 Token 过期时重新登录使用。
    """

    api_key: str
    parent: Config

    # ── 由 Web 层注入的运行时字段 ──
    email: str = ""
    password: str = ""
    _domain: str = ""  # 用户选择的 Garmin 区域，覆盖 parent.domain
    provider: str = ""  # 用户绑定的数据源，覆盖服务级 NEURUN_PROVIDER
    _group_pals_token: str = ""  # Huawei Web 绑定期间的用户级凭证
    coros_auto_relogin: bool | None = None  # 仅绑定请求显式传入；同步时为 None

    @property
    def token_dir(self) -> str:
        return str(Path(self.parent.data_dir) / self.api_key / "tokens")

    @property
    def memory_dir(self) -> str:
        return str(Path(self.parent.data_dir) / self.api_key / "memory")

    @property
    def db_path(self) -> str:
        return str(Path(self.parent.data_dir) / self.api_key / "data.db")

    @property
    def domain(self) -> str:
        return self._domain or self.parent.domain

    @property
    def provider_type(self) -> str:
        return self.provider or self.parent.provider_type

    @property
    def non_interactive(self) -> bool:
        return True  # Web 用户始终非交互

    @property
    def group_pals_token(self) -> str:
        if self._group_pals_token:
            return self._group_pals_token
        credential_path = Path(self.huawei_token_dir) / "group-pals-token"
        if credential_path.exists():
            try:
                token = read_private_text(credential_path).strip()
            except (OSError, LocalPersistenceError) as exc:
                raise ConfigError(
                    f"无法读取 Huawei 用户凭证 {credential_path}: {exc}"
                ) from exc
            if not token:
                raise ConfigError(f"Huawei 用户凭证为空: {credential_path}")
            return token
        return self.parent.group_pals_token

    def set_group_pals_token(self, token: str, *, persist: bool = True) -> None:
        """设置 Huawei 用户级 CrewPals 凭证，并按需私密持久化。"""
        normalized = token.strip()
        if not normalized:
            raise ConfigError("GROUP_PALS_TOKEN 不能为空")
        self._group_pals_token = normalized
        if persist:
            credential_path = Path(self.huawei_token_dir) / "group-pals-token"
            try:
                atomic_write_private(credential_path, normalized + "\n")
            except LocalPersistenceError as exc:
                raise ConfigError(str(exc)) from exc

    @property
    def huawei_token_dir(self) -> str:
        return str(Path(self.parent.data_dir) / self.api_key / "huawei-tokens")

    @property
    def coros_credential_key(self) -> str:
        return self.parent.coros_credential_key


# ECS/systemd 部署（scripts/deploy-ecs.sh）生成的持久化环境文件。
# CLI 管理员命令（invite 等）自动读取它，与 Web 进程
# （systemd EnvironmentFile 同源）保持同一配置，无需手工传 env。
_DEPLOY_ENV_FILE = Path("/etc/neurun/neurun.env")


def get_config(*, validate_credentials: bool = True) -> Config:
    """创建并校验配置的单次入口。

    每次调用都重新加载 .env，确保读取当前工作目录的配置。
    优先级: 系统环境变量 > 项目 .env > 部署环境文件 > ~/.neurun/.env > 默认值

    若设置 NEURUN_HOME 环境变量（须为实际环境变量，不可写在 .env 中）：
    - 从 NEURUN_HOME/.env 加载项目配置
    - 所有相对路径（db_path、memory_dir）基于 NEURUN_HOME 解析
    """
    # NEURUN_HOME 必须从实际环境变量读取（非 .env），避免鸡生蛋问题
    neurun_home = os.getenv("NEURUN_HOME") or os.getenv("RUNDOWN_HOME", "")
    if neurun_home:
        os.environ["NEURUN_HOME"] = neurun_home  # 确保后续 Config() 也能读到

    # 1. 加载 .env：优先当前目录（或 NEURUN_HOME），再加载全局
    base_dir = Path(neurun_home) if neurun_home else Path.cwd()
    cwd_env = base_dir / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=True)  # 当前目录 .env 优先
    # 部署环境文件（ECS systemd 部署）：CLI 与 Web 共享配置源，只补缺失项
    if _DEPLOY_ENV_FILE.exists():
        load_dotenv(_DEPLOY_ENV_FILE, override=False)
    home_env = Path.home() / ".neurun" / ".env"
    if not neurun_home and home_env.exists():
        load_dotenv(home_env, override=False)  # 全局配置只补充缺失项

    config = Config()

    # 2. 若设置了 NEURUN_HOME，将相对路径解析为基于 NEURUN_HOME 的绝对路径
    if neurun_home:
        home = Path(neurun_home)
        if not Path(config.data_dir).is_absolute():
            config.data_dir = str(home / config.data_dir)
        if not Path(config.db_path).is_absolute():
            config.db_path = str(home / config.db_path)
        if not Path(config.memory_dir).is_absolute():
            config.memory_dir = str(home / config.memory_dir)
        if not Path(config.token_dir).is_absolute():
            config.token_dir = str(home / config.token_dir)
        if not Path(config.huawei_token_dir).is_absolute():
            config.huawei_token_dir = str(home / config.huawei_token_dir)
        if config.invite_codes_file and not Path(config.invite_codes_file).is_absolute():
            config.invite_codes_file = str(home / config.invite_codes_file)

    # Web 服务和管理员命令不校验运动平台凭证
    serve_mode = os.getenv("NEURUN_SERVE_MODE") or os.getenv("RUNDOWN_SERVE_MODE", "")
    if validate_credentials and not serve_mode.lower() in ("1", "true", "yes"):
        config.validate()
    # 配置日志
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config.log_config()
    return config
