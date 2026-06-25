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

# 自动加载 .env 文件（如果存在）
load_dotenv()

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

    # ── 必填（RUNDOWN_ 为主，GARMIN_ 兼容旧配置） ──
    email: str = field(
        default_factory=lambda: os.getenv("RUNDOWN_EMAIL") or os.getenv("GARMIN_EMAIL", "")
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
            "./data/rundown_data.db",
        )
    )
    sync_days: int = field(
        default_factory=lambda: int(os.getenv("RUNDOWN_SYNC_DAYS") or os.getenv("GARMIN_SYNC_DAYS", "30"))
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("RUNDOWN_LOG_LEVEL") or os.getenv("GARMIN_LOG_LEVEL", "INFO")
    )

    # ── 内部路径 ──────────────────────────────
    memory_dir: str = "./memory"

    def validate(self) -> None:
        """校验必填配置项，缺失则抛出 ConfigError。"""
        missing: list[str] = []
        if not self.email:
            missing.append("RUNDOWN_EMAIL")
        if not self.password:
            missing.append("RUNDOWN_PASSWORD")

        if missing:
            raise ConfigError(
                f"缺少必填环境变量: {', '.join(missing)}\n"
                f"请复制 .env.example 为 .env 并填入真实值\n"
                f"(也支持旧名 GARMIN_EMAIL / GARMIN_PASSWORD)"
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
    """创建并校验配置的单次入口。"""
    config = Config()
    config.validate()
    # 配置日志
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config.log_config()
    return config
