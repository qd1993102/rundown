"""配置模块 — 统一管理所有配置项，从环境变量读取并校验。

使用 python-dotenv 支持 .env 文件，优先级：
系统环境变量 > .env 文件 > 默认值
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """配置错误。"""


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

    # ── 必填（RUNDOWN_ACCOUNT 为主，兼容旧名 RUNDOWN_EMAIL / GARMIN_EMAIL）──
    email: str = field(
        default_factory=lambda: (os.getenv("RUNDOWN_ACCOUNT") or
                                 os.getenv("RUNDOWN_EMAIL") or
                                 os.getenv("GARMIN_EMAIL", ""))
    )
    password: str = field(
        default_factory=lambda: os.getenv("RUNDOWN_PASSWORD") or os.getenv("GARMIN_PASSWORD", "")
    )

    # ── 数据源 ────────────────────────────────
    provider_type: str = field(
        default_factory=lambda: os.getenv("RUNDOWN_PROVIDER", "garmin")
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

    # ── 存储 ──────────────────────────────────
    db_path: str = field(
        default_factory=lambda: os.getenv(
            "RUNDOWN_DB_PATH") or os.getenv("GARMIN_DB_PATH",
            str(Path.home() / ".rundown" / "data.db"),
        )
    )
    sync_days: int = field(
        default_factory=lambda: int(os.getenv("RUNDOWN_SYNC_DAYS") or os.getenv("GARMIN_SYNC_DAYS", "30"))
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("RUNDOWN_LOG_LEVEL") or os.getenv("GARMIN_LOG_LEVEL", "INFO")
    )

    # ── 内部路径 ──────────────────────────────
    memory_dir: str = field(
        default_factory=lambda: os.getenv("RUNDOWN_MEMORY_DIR", "./memory")
    )

    # ── 工作目录（可选，设后所有相对路径基于此解析）──
    rundown_home: str = field(
        default_factory=lambda: os.getenv("RUNDOWN_HOME", "")
    )

    def validate(self) -> None:
        """校验必填配置项，缺失则抛出 ConfigError。"""
        missing: list[str] = []
        if not self.email:
            missing.append("RUNDOWN_ACCOUNT")
        if not self.password:
            missing.append("RUNDOWN_PASSWORD")

        if missing:
            raise ConfigError(
                f"缺少必填环境变量: {', '.join(missing)}\n"
                f"请复制 .env.example 为 .env 并填入真实值\n"
                f"(也支持旧名 RUNDOWN_EMAIL / GARMIN_EMAIL)"
            )

    def log_config(self) -> None:
        """打印配置信息（敏感信息脱敏）。"""
        logger.info("配置加载完成:")
        logger.info("  Provider:        %s", self.provider_type)
        logger.info("  Email:           %s", _mask_email(self.email))
        logger.info("  DB:              %s", self.db_path)
        logger.info("  Sync days:       %s", self.sync_days)
        logger.info("  Log level:       %s", self.log_level)
        if self.provider_type == "garmin":
            logger.info("  Domain:          %s", self.domain)
            logger.info("  Token dir:       %s", self.token_dir)


def get_config() -> Config:
    """创建并校验配置的单次入口。

    每次调用都重新加载 .env，确保读取当前工作目录的配置。
    优先级: 系统环境变量 > 项目 .env > ~/.rundown/.env > 默认值

    若设置 RUNDOWN_HOME 环境变量（须为实际环境变量，不可写在 .env 中）：
    - 从 RUNDOWN_HOME/.env 加载项目配置
    - 所有相对路径（db_path、memory_dir）基于 RUNDOWN_HOME 解析
    """
    # RUNDOWN_HOME 必须从实际环境变量读取（非 .env），避免鸡生蛋问题
    rundown_home = os.getenv("RUNDOWN_HOME", "")
    if rundown_home:
        os.environ["RUNDOWN_HOME"] = rundown_home  # 确保后续 Config() 也能读到

    # 1. 加载 .env：优先当前目录（或 RUNDOWN_HOME），再加载全局
    base_dir = Path(rundown_home) if rundown_home else Path.cwd()
    cwd_env = base_dir / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=True)  # 当前目录 .env 优先
    home_env = Path.home() / ".rundown" / ".env"
    if home_env.exists():
        load_dotenv(home_env, override=False)  # 全局配置只补充缺失项

    config = Config()

    # 2. 若设置了 RUNDOWN_HOME，将相对路径解析为基于 RUNDOWN_HOME 的绝对路径
    if rundown_home:
        home = Path(rundown_home)
        if not Path(config.db_path).is_absolute():
            config.db_path = str(home / config.db_path)
        if not Path(config.memory_dir).is_absolute():
            config.memory_dir = str(home / config.memory_dir)

    config.validate()
    # 配置日志
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config.log_config()
    return config
