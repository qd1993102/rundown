"""CLI 入口模块 — 命令行参数解析与流程编排。

neurun — Your AI running coach.

Commands:
    neurun daily       自动同步数据并生成每日综合报告（HTML + PNG + 终端）
    neurun sync        纯数据同步（批量拉取，不含报告生成）
    neurun activities  查询活动列表
    neurun health      查询健康指标
    neurun memory      记忆管理（list/show/summarize/goal/plan/...）
    neurun status      查看同步状态
    neurun mcp         启动 MCP Server
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .config import get_config, ConfigError
from .auth import AuthManager
from .fetcher import Fetcher
from .storage import Storage
from .memory import MemoryStore, MemoryType, MemoryStatus, get_daily_activities
from .render import render_daily_html
from .image import render_daily_image
from .local_files import (
    LocalPersistenceError,
    atomic_write_private,
    ensure_private_dir,
    restrict_private_file,
)
from .resource_lifecycle import close_runtime_resources
from .training_service_factory import build_capacity_athlete_context
from .report_readiness import (
    DailyReportReadinessError,
    DailyReportReadinessService,
    enforce_report_readiness,
)

logger = logging.getLogger(__name__)
console = Console()
SyncProgressCallback = Callable[..., None]


class ProviderAuthenticationError(RuntimeError):
    """当前用户的数据源 Token 无法恢复或验证。"""

    def __init__(self, provider_type: str):
        self.provider_type = provider_type
        display_name = {
            "garmin": "Garmin",
            "coros": "Coros",
            "huawei": "Huawei",
        }.get(provider_type, provider_type)
        super().__init__(f"{display_name} 认证失败，请重新绑定账号")


class ProviderIdentityError(RuntimeError):
    """认证通过，但平台暂时没有返回可用的正整数用户 ID。"""

    def __init__(self, provider_type: str):
        self.provider_type = provider_type
        super().__init__("数据源暂时无法确认用户身份，请稍后重试")


def _report_sync_progress(
    callback: SyncProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    label: str,
    items: dict[str, Any] | None = None,
) -> None:
    """上报真实阶段；进度持久化异常不得掩盖主同步结果。"""
    if callback is None:
        return
    try:
        if items is None:
            callback(stage, current, total, label)
        else:
            callback(stage, current, total, label, items)
    except Exception as exc:
        logger.warning("同步进度上报失败: error_type=%s", type(exc).__name__)


def _throttled_item_progress_callback(
    callback: SyncProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    label: str,
    *,
    min_interval: float = 1.0,
) -> Callable[[dict[str, Any]], None]:
    """按首次、时间间隔和最终项节流 Provider 的逐项进度。"""
    last_emit_at = float("-inf")

    def report(items: dict[str, Any]) -> None:
        nonlocal last_emit_at
        now = time.monotonic()
        item_current = max(0, int(items.get("current") or 0))
        item_total = max(0, int(items.get("total") or 0))
        is_final = item_total > 0 and item_current >= item_total
        if now - last_emit_at < min_interval and not is_final:
            return
        _report_sync_progress(
            callback, stage, current, total, label, items,
        )
        last_emit_at = now

    return report


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _setup(config=None):
    """初始化所有模块。

    Returns: (config, provider, storage, memory_store, user_id)
    - provider: DataProvider 实例（GarminProvider 或 CorosProvider）
    """
    if config is None:
        config = get_config()

    from .providers import get_provider

    provider = get_provider(config)
    try:
        authenticated = provider.authenticate()
    except Exception:
        close_runtime_resources(provider)
        raise
    if not authenticated:
        close_runtime_resources(provider)
        raise ProviderAuthenticationError(config.provider_type)

    try:
        user_id = int(provider.user_id)
    except Exception:
        close_runtime_resources(provider)
        raise
    if user_id <= 0:
        close_runtime_resources(provider)
        raise ProviderIdentityError(config.provider_type)

    storage = Storage(config)

    # Memory store（Garmin 需要 api_client_getter 补全距离）
    def _make_api_client():
        if config.provider_type == "garmin":
            return provider.auth.create_api_client()
        return None

    memory_store = MemoryStore(
        config.memory_dir,
        db_getter=lambda: storage.db,
        api_client_getter=_make_api_client,
    )

    return config, provider, storage, memory_store, user_id


def _get_user_id(auth: AuthManager) -> int:
    """获取当前用户的运动平台 user_id (int)。

    通过 APIClient.profile 获取真实 user_id。
    """
    try:
        api = auth.create_api_client()
        profile = api.profile
        if isinstance(profile, dict):
            uid = profile.get("id")
            if uid:
                user_id = int(uid)
                logger.info("user_id: %d", user_id)
                return user_id
    except Exception as exc:
        logger.warning("获取 user_id 失败: %s", exc)
    raise RuntimeError("无法获取运动平台 user_id，请检查账号配置")


def _get_ai_insight(
    fm: dict[str, Any], target_date: date,
) -> dict[str, Any] | None:
    """调用配置的 AI 服务获取教练洞察。"""
    try:
        from .coach import get_coach_insight
        return get_coach_insight(fm, target_date)
    except Exception as exc:
        logger.warning("AI 洞察生成失败: %s", exc)
        return None


def _parse_date(date_str: str) -> date:
    """解析日期字符串 (YYYY-MM-DD)。"""
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"无效日期格式: {date_str}，应为 YYYY-MM-DD")


# ═══════════════════════════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════════════════════════

def cmd_sync(args: argparse.Namespace) -> None:
    """sync 命令：纯数据同步（不含记忆生成，使用 neurun daily 生成报告）。"""
    config, provider, storage, memory_store, user_id = _setup()

    console.print(Panel.fit(
        f"[bold blue]🔄 neurun Sync ({config.provider_type})[/]\n同步运动数据",
        border_style="blue",
    ))

    # 确定日期范围
    if args.from_date and args.to_date:
        start = _parse_date(args.from_date)
        end = _parse_date(args.to_date)
    elif args.full:
        start = date.today() - timedelta(days=365 * 3)
        end = date.today()
    else:
        days = args.days or config.sync_days
        start = date.today() - timedelta(days=days)
        end = date.today()

    console.print(f"📅 同步范围: {start} ~ {end}")

    # 清理同步记录（--force 时全量重置数据）避免 garmy SyncManager 跳过重试
    force = getattr(args, 'force', False)
    if force:
        console.print("[yellow]⚠️  强制模式：清除区间内全部本地数据后重新拉取[/]")
    storage.reset_pending_metrics(user_id, start, end, force=force)

    # 执行同步
    storage.mark_sync_calendar_range(user_id, start, end, "pending")
    try:
        if config.provider_type == "garmin":
            # Garmin: 使用 garmy SyncManager（健康数据）+ 直同步活动
            result = storage.sync_range(user_id, start, end, args.metrics)
            console.print(f"[green]✅ 同步完成[/]")
            if result:
                for k, v in result.items():
                    console.print(f"  {k}: {v}")
            # garmy ActivitiesIterator 有状态 bug：按日期升序处理时游标不回退，
            # 导致后续日期的活动被跳过。这里用我们自己的 fetch 直写 DB 作为补充。
            _sync_garmin_activities(provider, storage, user_id, start, end)
        elif config.provider_type in ("coros", "huawei"):
            # Coros/Huawei: Provider 标准化后直接写入 SQLite
            _sync_provider(provider, storage, user_id, start, end, config.provider_type)
    except Exception as exc:
        storage.mark_sync_calendar_range(
            user_id, start, end, "failed", error_message=str(exc),
        )
        console.print(f"[red]❌ 同步失败: {exc}[/]")
        return
    else:
        storage.mark_sync_calendar_range(user_id, start, end, "completed")

    console.print("[dim]💡 运行 [bold]neurun daily[/bold] 生成日报[/]")


def cmd_auth(args: argparse.Namespace) -> None:
    """完成当前 Provider 的本地认证。"""
    config = get_config()
    from .providers import get_provider
    provider = get_provider(config)
    try:
        if provider.authenticate():
            console.print(f"[green]✅ {config.provider_type} 本地认证成功，令牌已安全保存[/]")
        else:
            console.print(f"[red]❌ {config.provider_type} 认证失败[/]")
    except Exception as exc:
        console.print(f"[red]❌ 认证失败: {exc}[/]")


def _ensure_activity_columns(storage: Storage) -> None:
    """补齐 neurun 在 garmy activities 表上扩展的字段。"""
    from sqlalchemy import text

    for column_sql in (
        "ALTER TABLE activities ADD COLUMN distance_meters FLOAT",
        "ALTER TABLE activities ADD COLUMN activity_type VARCHAR",
        "ALTER TABLE activities ADD COLUMN max_heart_rate INTEGER",
        "ALTER TABLE activities ADD COLUMN calories INTEGER",
        "ALTER TABLE activities ADD COLUMN elevation_gain FLOAT",
        "ALTER TABLE activities ADD COLUMN provider_name VARCHAR",
    ):
        session = storage.db.get_session()
        try:
            session.execute(text(column_sql))
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()


def _ensure_health_threshold_columns(storage: Storage) -> None:
    """补齐 daily_health_metrics 表的平台乳酸阈值列（lthr/ltsp）。"""
    from sqlalchemy import text

    for column_sql in (
        "ALTER TABLE daily_health_metrics ADD COLUMN lthr INTEGER",
        "ALTER TABLE daily_health_metrics ADD COLUMN ltsp INTEGER",
    ):
        session = storage.db.get_session()
        try:
            session.execute(text(column_sql))
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()


def _sync_garmin_activities(provider, storage, user_id: int, start: date, end: date) -> None:
    """Garmin 活动直同步：绕过 garmy ActivitiesIterator 的状态 bug。

    garmy 的 ActivitiesIterator 是单向迭代器（从新到旧），sync_range 按日期升序
    处理时（23→24→25→26），处理完 23 后游标已跳过 24-26，导致这些日期的活动全部丢失。
    这里直接从 Garmin API 拉取活动并写入 DB。
    """
    from sqlalchemy import text

    _ensure_activity_columns(storage)

    console.print("[dim]📥 补全 Garmin 活动数据...[/]")
    activities = provider.activities.fetch_activities(start, end)
    session = storage.db.get_session()
    stored_act = 0
    updated_act = 0
    for a in activities:
        row = session.execute(
            text("""
                SELECT distance_meters, activity_type, elevation_gain,
                       max_heart_rate, calories, provider_name,
                       avg_heart_rate, training_load
                FROM activities WHERE activity_id = :aid
            """),
            {"aid": a.activity_id}
        ).fetchone()
        adate = a.start_time[:10] if a.start_time and len(str(a.start_time)) >= 10 else str(start)[:10]
        if not row:
            # 新活动：INSERT
            session.execute(text("""
                INSERT INTO activities (user_id, activity_id, activity_date,
                    activity_name, duration_seconds, avg_heart_rate,
                    training_load, start_time, distance_meters, activity_type,
                    max_heart_rate, calories, elevation_gain, provider_name,
                    created_at)
                VALUES (:uid, :aid, :ad, :an, :dur, :hr, :tl, :st, :dist,
                    :atype, :mhr, :cal, :elev, :provider, datetime('now'))
            """), {
                "uid": user_id, "aid": a.activity_id,
                "ad": adate,
                "an": a.activity_name, "dur": a.duration_seconds,
                "hr": a.avg_heart_rate, "tl": a.training_load,
                "st": a.start_time, "dist": a.distance_meters,
                "atype": a.activity_type,
                "mhr": a.max_heart_rate, "cal": a.calories,
                "elev": a.elevation_gain, "provider": "garmin",
            })
            stored_act += 1
        elif (
            (not row[0] and a.distance_meters)
            or row[1] != a.activity_type
            or (a.elevation_gain is not None and row[2] != a.elevation_gain)
            or (a.max_heart_rate is not None and row[3] != a.max_heart_rate)
            or (a.calories is not None and row[4] != a.calories)
            or row[5] != "garmin"
            or (a.avg_heart_rate is not None and row[6] != a.avg_heart_rate)
            or (a.training_load > 0 and row[7] != a.training_load)
        ):
            # 已有记录：补全标准活动事实，不覆盖未知值。
            session.execute(text("""
                UPDATE activities
                SET distance_meters = CASE
                        WHEN :dist > 0 THEN :dist ELSE distance_meters
                    END,
                    activity_type = :atype,
                    avg_heart_rate = COALESCE(:hr, avg_heart_rate),
                    training_load = CASE
                        WHEN :tl > 0 THEN :tl ELSE training_load
                    END,
                    max_heart_rate = COALESCE(:mhr, max_heart_rate),
                    calories = COALESCE(:cal, calories),
                    elevation_gain = COALESCE(:elev, elevation_gain),
                    provider_name = :provider
                WHERE activity_id = :aid
            """), {
                "dist": a.distance_meters,
                "atype": a.activity_type,
                "hr": a.avg_heart_rate, "tl": a.training_load,
                "mhr": a.max_heart_rate,
                "cal": a.calories,
                "elev": a.elevation_gain,
                "provider": "garmin",
                "aid": a.activity_id,
            })
            updated_act += 1
    session.commit()
    session.close()
    _sync_activity_details(provider, storage, user_id, activities)
    if stored_act > 0 or updated_act > 0:
        console.print(f"  ✅ Garmin 活动: {stored_act} 条新增, {updated_act} 条字段补全 (共 {len(activities)} 条)")
    else:
        console.print(f"  📦 Garmin 活动: 已是最新 (共 {len(activities)} 条)")


def _sync_provider(provider, storage, user_id: int, start: date, end: date,
                   provider_name: str,
                   progress_callback: SyncProgressCallback | None = None) -> None:
    """将非 Garmin Provider 的标准化数据写入 SQLite。"""
    from sqlalchemy import text

    _ensure_activity_columns(storage)

    _report_sync_progress(
        progress_callback, "syncing_activities", 2, 4, "正在同步运动记录",
    )
    console.print("[dim]📥 拉取活动数据...[/]")
    if provider_name == "coros" and progress_callback is not None:
        activity_progress = _throttled_item_progress_callback(
            progress_callback, "syncing_activities", 2, 4,
            "正在同步运动记录",
        )
        activities = provider.activities.fetch_activities(
            start, end, progress_callback=activity_progress,
        )
    else:
        activities = provider.activities.fetch_activities(start, end)
    session = storage.db.get_session()
    stored_act = 0
    updated_act = 0
    for a in activities:
        existing = session.execute(
            text("""
                SELECT duration_seconds, activity_type, distance_meters,
                       elevation_gain, max_heart_rate, calories, provider_name,
                       avg_heart_rate, training_load
                FROM activities WHERE activity_id = :aid
            """),
            {"aid": a.activity_id}
        ).fetchone()
        if not existing:
            # Extract date from formatted start_time string (e.g. "2026-06-25 08:30:00")
            try:
                activity_date = a.start_time[:10] if a.start_time and len(str(a.start_time)) >= 10 else str(start)[:10]
            except Exception:
                activity_date = str(start)[:10]

            session.execute(text("""
                INSERT INTO activities (user_id, activity_id, activity_date,
                    activity_name, duration_seconds, avg_heart_rate,
                    training_load, start_time, distance_meters, activity_type,
                    max_heart_rate, calories, elevation_gain, provider_name,
                    created_at)
                VALUES (:uid, :aid, :ad, :an, :dur, :hr, :tl, :st, :dist,
                    :atype, :mhr, :cal, :elev, :provider, datetime('now'))
            """), {
                "uid": user_id, "aid": a.activity_id,
                "ad": activity_date,
                "an": a.activity_name, "dur": a.duration_seconds,
                "hr": a.avg_heart_rate, "tl": a.training_load,
                "st": a.start_time, "dist": a.distance_meters,
                "atype": a.activity_type,
                "mhr": a.max_heart_rate, "cal": a.calories,
                "elev": a.elevation_gain, "provider": provider_name,
            })
            stored_act += 1
        elif (
            existing[0] != a.duration_seconds
            or existing[1] != a.activity_type
            or (a.distance_meters > 0 and existing[2] != a.distance_meters)
            or (a.elevation_gain is not None and existing[3] != a.elevation_gain)
            or (a.max_heart_rate is not None and existing[4] != a.max_heart_rate)
            or (a.calories is not None and existing[5] != a.calories)
            or existing[6] != provider_name
            or (a.avg_heart_rate is not None and existing[7] != a.avg_heart_rate)
            or (a.training_load > 0 and existing[8] != a.training_load)
        ):
            session.execute(text("""
                UPDATE activities
                SET duration_seconds = :dur,
                    activity_type = :atype,
                    avg_heart_rate = COALESCE(:hr, avg_heart_rate),
                    training_load = CASE
                        WHEN :tl > 0 THEN :tl ELSE training_load
                    END,
                    distance_meters = CASE
                        WHEN :dist > 0 THEN :dist ELSE distance_meters
                    END,
                    max_heart_rate = COALESCE(:mhr, max_heart_rate),
                    calories = COALESCE(:cal, calories),
                    elevation_gain = COALESCE(:elev, elevation_gain),
                    provider_name = :provider
                WHERE activity_id = :aid
            """), {
                "dur": a.duration_seconds,
                "atype": a.activity_type,
                "hr": a.avg_heart_rate, "tl": a.training_load,
                "dist": a.distance_meters,
                "mhr": a.max_heart_rate,
                "cal": a.calories,
                "elev": a.elevation_gain,
                "provider": provider_name,
                "aid": a.activity_id,
            })
            updated_act += 1
    session.commit()
    session.close()
    _sync_activity_details(provider, storage, user_id, activities)
    console.print(
        f"  ✅ 活动: {stored_act} 条新增, {updated_act} 条字段更新 "
        f"(共 {len(activities)} 条)"
    )
    _report_sync_progress(
        progress_callback, "syncing_metrics", 3, 4, "正在同步健康指标",
    )
    console.print("[dim]📥 拉取健康数据...[/]")
    _ensure_health_threshold_columns(storage)
    stored_health = 0
    updated_health = 0
    if provider_name == "coros" and progress_callback is not None:
        health_progress = _throttled_item_progress_callback(
            progress_callback, "syncing_metrics", 3, 4,
            "正在同步健康指标",
        )
        health_records = provider.health.fetch_health_range(
            start, end, progress_callback=health_progress,
        )
    else:
        health_records = provider.health.fetch_health_range(start, end)
    for health in health_records:
        d = health.metric_date
        if d < start or d > end:
            continue
        if health and any((
            health.sleep_duration_hours > 0,
            health.resting_heart_rate is not None,
            health.hrv_last_night_avg is not None,
            health.total_steps > 0,
            health.total_distance_meters > 0,
            health.total_calories > 0,
            health.active_calories > 0,
        )):
            session = storage.db.get_session()
            existing = session.execute(
                text("SELECT 1 FROM daily_health_metrics WHERE user_id = :uid AND metric_date = :md"),
                {"uid": user_id, "md": str(d)}
            ).fetchone()
            if not existing:
                session.execute(text("""
                    INSERT INTO daily_health_metrics
                        (user_id, metric_date, sleep_duration_hours,
                         deep_sleep_hours, rem_sleep_hours, deep_sleep_percentage,
                         rem_sleep_percentage, resting_heart_rate,
                         hrv_weekly_avg, hrv_last_night_avg, hrv_status,
                         avg_stress_level, body_battery_high, body_battery_low,
                         total_steps, total_distance_meters, total_calories,
                         active_calories, training_readiness_score,
                         training_readiness_level, lthr, ltsp, created_at, updated_at)
                    VALUES (:uid, :md, :sl, :ds, :rs, :dp, :rp, :rhr,
                            :hw, :hn, :hs, :as, :bh, :bl,
                            :ts, :td, :tc, :ac, :trs, :trl, :lthr, :ltsp,
                            datetime('now'), datetime('now'))
                """), {
                    "uid": user_id, "md": str(d),
                    "sl": health.sleep_duration_hours, "ds": health.deep_sleep_hours,
                    "rs": health.rem_sleep_hours, "dp": health.deep_sleep_pct,
                    "rp": health.rem_sleep_pct, "rhr": health.resting_heart_rate,
                    "hw": health.hrv_weekly_avg, "hn": health.hrv_last_night_avg,
                    "hs": health.hrv_status, "as": health.avg_stress_level,
                    "bh": health.body_battery_high, "bl": health.body_battery_low,
                    "ts": health.total_steps, "td": health.total_distance_meters,
                    "tc": health.total_calories, "ac": health.active_calories,
                    "trs": health.training_readiness_score,
                    "trl": health.training_readiness_level,
                    "lthr": health.extra.get("lthr") if isinstance(health.extra, dict) else None,
                    "ltsp": health.extra.get("ltsp") if isinstance(health.extra, dict) else None,
                })
                stored_health += 1
            else:
                # 普通重同步只用本次实际取得的值补齐或更新字段；Mobile
                # 睡眠暂时不可用时不得用默认 0/null 清空已有健康数据。
                session.execute(text("""
                    UPDATE daily_health_metrics
                    SET sleep_duration_hours = CASE WHEN :sl > 0 THEN :sl ELSE sleep_duration_hours END,
                        deep_sleep_hours = CASE WHEN :sl > 0 THEN :ds ELSE deep_sleep_hours END,
                        rem_sleep_hours = CASE WHEN :sl > 0 THEN :rs ELSE rem_sleep_hours END,
                        deep_sleep_percentage = CASE WHEN :sl > 0 THEN :dp ELSE deep_sleep_percentage END,
                        rem_sleep_percentage = CASE WHEN :sl > 0 THEN :rp ELSE rem_sleep_percentage END,
                        resting_heart_rate = COALESCE(:rhr, resting_heart_rate),
                        hrv_weekly_avg = COALESCE(:hw, hrv_weekly_avg),
                        hrv_last_night_avg = COALESCE(:hn, hrv_last_night_avg),
                        hrv_status = CASE WHEN :hw IS NOT NULL OR :hn IS NOT NULL THEN :hs ELSE hrv_status END,
                        avg_stress_level = COALESCE(:as, avg_stress_level),
                        body_battery_high = COALESCE(:bh, body_battery_high),
                        body_battery_low = COALESCE(:bl, body_battery_low),
                        total_steps = CASE WHEN :ts > 0 THEN :ts ELSE total_steps END,
                        total_distance_meters = CASE WHEN :td > 0 THEN :td ELSE total_distance_meters END,
                        total_calories = CASE WHEN :tc > 0 THEN :tc ELSE total_calories END,
                        active_calories = CASE WHEN :ac > 0 THEN :ac ELSE active_calories END,
                        training_readiness_score = COALESCE(:trs, training_readiness_score),
                        training_readiness_level = CASE WHEN :trl != '' THEN :trl ELSE training_readiness_level END,
                        lthr = COALESCE(:lthr, lthr),
                        ltsp = COALESCE(:ltsp, ltsp),
                        updated_at = datetime('now')
                    WHERE user_id = :uid AND metric_date = :md
                """), {
                    "uid": user_id, "md": str(d),
                    "sl": health.sleep_duration_hours, "ds": health.deep_sleep_hours,
                    "rs": health.rem_sleep_hours, "dp": health.deep_sleep_pct,
                    "rp": health.rem_sleep_pct, "rhr": health.resting_heart_rate,
                    "hw": health.hrv_weekly_avg, "hn": health.hrv_last_night_avg,
                    "hs": health.hrv_status, "as": health.avg_stress_level,
                    "bh": health.body_battery_high, "bl": health.body_battery_low,
                    "ts": health.total_steps, "td": health.total_distance_meters,
                    "tc": health.total_calories, "ac": health.active_calories,
                    "trs": health.training_readiness_score,
                    "trl": health.training_readiness_level,
                    "lthr": health.extra.get("lthr") if isinstance(health.extra, dict) else None,
                    "ltsp": health.extra.get("ltsp") if isinstance(health.extra, dict) else None,
                })
                updated_health += 1
            session.commit()
            session.close()
    console.print(f"  ✅ 健康: {stored_health} 天新增, {updated_health} 天更新")

    console.print(f"[green]✅ {provider_name} 同步完成[/]")


def _sync_activity_details(
    provider, storage, user_id: int, activities: list[Any],
) -> dict[str, int]:
    """尽力补齐 Provider 活动详情；单条失败不影响汇总同步。"""
    from .activity import (
        ensure_tables, get_activity_detail, get_activity_splits,
        store_activity_detail,
    )

    fetch_detail = getattr(provider.activities, "fetch_activity_detail", None)
    if not callable(fetch_detail):
        return {"total": len(activities), "stored": 0, "failed": 0}
    fetch_splits = getattr(provider.activities, "fetch_activity_splits", None)

    ensure_tables(storage)
    stored = 0
    failed = 0
    for activity in activities:
        try:
            activity_id = str(activity.activity_id)
            existing_detail = get_activity_detail(storage, activity_id)
            existing_splits = get_activity_splits(storage, activity_id)
            is_running = "run" in str(activity.activity_type).lower() or "跑" in str(
                getattr(activity, "activity_name", "") or ""
            )
            # 旧结构（session-summary 无 segment_sequence）的跑步活动需要重拉一次，
            # 补齐 Garmin /splits 官方分段（lapDTOs），否则永远停留在 ×2 坏数据。
            has_summary = _has_summary_facts(storage, activity_id)
            needs_provider_normalization = _summary_needs_provider_normalization(
                storage, activity_id, existing_detail,
            )
            needs_relap = (
                is_running and existing_detail and has_summary
                and not _summary_has_sequence(storage, activity_id)
            )
            # 有 detail 但缺 activity_summary_facts（旧同步或摘要重建失败）：用已有 detail
            # 重建分段摘要（intensity 配速带 / pace_profile 分位数 / structure），
            # 不强制重拉 detail——否则【今天对计划意味着什么】永远拿不到分段配速分析。
            needs_summary = bool(existing_detail) and (
                not has_summary or needs_provider_normalization
            )
            if (
                existing_detail
                and (existing_splits or not is_running)
                and not needs_relap
                and not needs_summary
            ):
                continue
            # Coros 需要活动类型数字（sportType）才能取详情；其他 Provider 忽略。
            sport_type = 0
            extra = getattr(activity, "extra", None) or {}
            if isinstance(extra, dict) and extra.get("provider") == "coros":
                sport_type = int(extra.get("sport_type") or 0)
            if needs_summary and not needs_relap:
                # 仅缺摘要：直接用已有 detail 重建，不重新拉取、不附加 lapDTOs。
                detail = existing_detail
            else:
                try:
                    detail = fetch_detail(activity_id, sport_type=sport_type)
                except TypeError:
                    detail = fetch_detail(activity_id)
            if not isinstance(detail, dict) or not detail:
                failed += 1
                continue
            # 跑步活动：附加官方分段（可信 lapDTOs），供分段分析与结构识别
            if is_running and callable(fetch_splits):
                try:
                    laps = fetch_splits(activity_id)
                except Exception:
                    laps = []
                if laps:
                    detail["lapDTOs"] = laps
                elif existing_detail and existing_detail.get("lapDTOs"):
                    # 重拉失败：保留已有 lapDTOs，避免覆盖已修复的分段数据
                    detail["lapDTOs"] = existing_detail["lapDTOs"]
                elif needs_relap:
                    # needs_relap 就是为了补齐 lapDTOs：本次拉取失败则不覆盖，
                    # 保持现状等下次同步再试，绝不把已修复/已存在数据降级回旧 schema。
                    failed += 1
                    continue
            if store_activity_detail(
                storage, user_id, activity_id, detail,
            ):
                stored += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "活动详情同步失败 activity_id=%s error_type=%s",
                getattr(activity, "activity_id", ""), type(exc).__name__,
            )
    return {"total": len(activities), "stored": stored, "failed": failed}


def _summary_has_sequence(storage, activity_id: str) -> bool:
    """当前代码产物判定：summary 含 segment_sequence 且段含 duration_s（新 schema 标志）。

    旧表（sequence 无 duration_s / split_type 缺 intensityType）返回 False，
    触发一次重拉修复；重拉后不再重复。
    """
    try:
        from .activity import get_activity_summary_facts

        facts = get_activity_summary_facts(storage, activity_id)
    except Exception:
        return False
    sequence = (facts or {}).get("segment_sequence") or []
    return bool(sequence and "duration_s" in (sequence[0] or {}))


def _summary_needs_provider_normalization(
    storage, activity_id: str, detail: dict[str, Any] | None,
) -> bool:
    """Detect replayable Coros details created before unit/Hz normalization."""
    if not isinstance(detail, dict) or not (
        detail.get("frequencyList") or detail.get("lapList")
    ):
        return False
    try:
        from .activity import get_activity_summary_facts

        facts = get_activity_summary_facts(storage, activity_id) or {}
    except Exception:
        return True
    volume = facts.get("volume") or {}
    gate = facts.get("quantity_gate") or {}
    return bool(
        facts.get("granularity") != "L2"
        or not volume.get("duration_s")
        or not volume.get("distance_m")
        or gate.get("quantity_reliable") is False
    )


def _has_summary_facts(storage, activity_id: str) -> bool:
    """活动是否已有 activity_summary_facts（分段摘要）记录。"""
    try:
        from .activity import get_activity_summary_facts

        return get_activity_summary_facts(storage, activity_id) is not None
    except Exception:
        return False


def _do_data_sync(config, target: date | None = None,
                  full_sync: bool = False, force_sync: bool = False,
                  sync_days: int | None = None, quiet: bool = False,
                  progress_callback: SyncProgressCallback | None = None):
    """核心数据同步逻辑；只写入 SQLite，不生成日报。"""
    _report_sync_progress(
        progress_callback, "authenticating", 1, 4, "正在验证数据源",
    )
    _, provider, storage, memory_store, user_id = _setup(config=config)
    try:
        return _do_initialized_data_sync(
            config=config,
            provider=provider,
            storage=storage,
            memory_store=memory_store,
            user_id=user_id,
            target=target,
            full_sync=full_sync,
            force_sync=force_sync,
            sync_days=sync_days,
            quiet=quiet,
            progress_callback=progress_callback,
        )
    except Exception:
        close_runtime_resources(provider, storage)
        raise


def _do_initialized_data_sync(
    config,
    provider,
    storage,
    memory_store,
    user_id: int,
    target: date | None = None,
    full_sync: bool = False,
    force_sync: bool = False,
    sync_days: int | None = None,
    quiet: bool = False,
    progress_callback: SyncProgressCallback | None = None,
):
    """使用已初始化资源执行数据同步；成功后资源所有权交还调用方。"""

    target = target or date.today()

    def _log(msg: str) -> None:
        if not quiet:
            console.print(msg)

    if full_sync:
        from_day = target - timedelta(days=365 * 3)
        to_day = target
    elif sync_days is not None:
        from_day = target - timedelta(days=sync_days)
        to_day = target
    else:
        missing_dates = []
        for i in range(3):
            d = target - timedelta(days=i)
            if not storage.has_local_data(user_id, d):
                missing_dates.append(d)
        if missing_dates:
            from_day = missing_dates[-1]
            to_day = target
        else:
            from_day = None
            to_day = None

    if from_day and to_day:
        _log(f"[dim]🔄 数据同步: {from_day} ~ {to_day}[/]")
        if force_sync:
            storage.reset_pending_metrics(user_id, from_day, to_day, force=True)
        elif config.provider_type == "garmin":
            storage.reset_pending_metrics(user_id, from_day, to_day)

        storage.mark_sync_calendar_range(
            user_id, from_day, to_day, "pending",
        )
        try:
            if config.provider_type == "garmin":
                # Web 多用户模式：每用户隔离 token_dir，需注入已认证 APIClient
                if hasattr(provider, 'auth') and hasattr(provider.auth, '_client'):
                    storage.set_api_client(provider.auth.create_api_client())
                _report_sync_progress(
                    progress_callback, "syncing_metrics", 2, 4,
                    "正在同步健康指标",
                )

                def report_metric_items(items: dict[str, Any]) -> None:
                    _report_sync_progress(
                        progress_callback, "syncing_metrics", 2, 4,
                        "正在同步健康指标", items,
                    )

                storage.sync_range(
                    user_id, from_day, to_day,
                    progress_callback=report_metric_items,
                )
                _report_sync_progress(
                    progress_callback, "syncing_activities", 3, 4,
                    "正在同步运动记录",
                )
                _sync_garmin_activities(provider, storage, user_id, from_day, to_day)
            elif config.provider_type in ("coros", "huawei"):
                _sync_provider(provider, storage, user_id, from_day, to_day,
                               config.provider_type, progress_callback)
        except Exception as exc:
            storage.mark_sync_calendar_range(
                user_id, from_day, to_day, "failed", error_message=str(exc),
            )
            raise
        else:
            storage.mark_sync_calendar_range(
                user_id, from_day, to_day, "completed",
            )
    else:
        _log("[dim]📦 本地数据完整，跳过同步[/]")

    return provider, storage, memory_store, user_id


def _local_report_context(config):
    """创建仅访问本地 SQLite 的日报上下文。"""
    storage = Storage(config)
    _ensure_activity_columns(storage)
    user_id = storage.get_local_user_id()
    if user_id is None:
        raise RuntimeError("本地没有已同步数据，请先同步后再生成日报")
    memory_store = MemoryStore(
        config.memory_dir,
        db_getter=lambda: storage.db,
    )
    return None, storage, memory_store, user_id


def _do_daily_sync(config, target: date | None = None,
                   skip_sync: bool = False, full_sync: bool = False,
                   force_sync: bool = False, sync_days: int | None = None,
                   quiet: bool = False, report_mode: str = "complete"):
    """按需同步后生成日报；``skip_sync`` 时严格只读取本地 SQLite。"""
    target = target or date.today()

    if skip_sync:
        provider, storage, memory_store, user_id = _local_report_context(config)
    else:
        local_context = None
        effective_sync_days = sync_days
        if not full_sync and not force_sync and sync_days is None:
            try:
                local_context = _local_report_context(config)
                _local_provider, local_storage, _local_memory, local_user_id = local_context
                local_readiness = DailyReportReadinessService(
                    local_storage, provider_type=config.provider_type,
                ).check(local_user_id, target)
                enforce_report_readiness(local_readiness, report_mode)
            except DailyReportReadinessError as exc:
                if local_context is not None:
                    close_runtime_resources(local_context[0], local_context[1])
                local_context = None
                actions = set(exc.readiness.suggested_actions)
                if "sync_28d" in actions:
                    effective_sync_days = 27
                elif "sync_7d" in actions:
                    effective_sync_days = 6
                else:
                    effective_sync_days = 0
            except RuntimeError:
                if local_context is not None:
                    close_runtime_resources(local_context[0], local_context[1])
                local_context = None
                effective_sync_days = 27 if report_mode == "complete" else 0

        if local_context is not None:
            provider, storage, memory_store, user_id = local_context
        else:
            provider, storage, memory_store, user_id = _do_data_sync(
                config=config,
                target=target,
                full_sync=full_sync,
                force_sync=force_sync,
                sync_days=effective_sync_days,
                quiet=quiet,
            )

    def _log(msg: str) -> None:
        if not quiet:
            console.print(msg)

    try:
        readiness = DailyReportReadinessService(
            storage, provider_type=config.provider_type,
        ).check(user_id, target)
        enforce_report_readiness(readiness, report_mode)
        readiness_snapshot = readiness.to_dict()

        _log("[yellow]📰 生成日报...[/]")
        try:
            athlete_context = build_capacity_athlete_context(
                config, target, omitted_sections=readiness.omitted_sections,
            )
        except Exception:
            athlete_context = {
                "status": "unavailable",
                "reason": "capacity_load_error",
                "source": "training_domain_capacity_profile",
            }
        mem = memory_store.generate_daily_report(
            user_id,
            target,
            ai_insight=None,
            readiness=readiness_snapshot,
            athlete_context=athlete_context,
            persist=False,
        )
        if mem is None:
            raise RuntimeError(f"无法生成 {target} 的日报")

        # 受限版同样生成在线洞察：缺失维度在 Front Matter 中标记 unavailable 并随
        # omitted_sections 传入 Skill，模型契约要求保留未知、只解释可用事实
        # （运动概要/当日训练/负荷等）；不因辅助维度缺失整块跳过 AI，避免
        # “今天对计划意味着什么”在用户有跑步时没有任何跑步分析。
        ai_result = _get_ai_insight(mem.front_matter, target)
        if ai_result:
            mem.front_matter["ai_insight"] = ai_result
        mem = memory_store.finalize_daily_report(mem)
    except Exception:
        close_runtime_resources(provider, storage)
        raise

    return mem, provider, storage, memory_store, user_id


def cmd_daily(args: argparse.Namespace) -> None:
    """daily 命令：自动同步 → 生成 md → HTML → PNG → AI 洞察。"""
    config, provider, storage, memory_store, user_id = _setup()

    target = _parse_date(args.date) if args.date else date.today()
    theme = getattr(args, 'theme', 'sport')
    skip_sync = getattr(args, 'skip_sync', False)
    full_sync = getattr(args, 'full', False)
    force_sync = getattr(args, 'force', False)
    sync_days = getattr(args, 'sync_days', None)
    report_mode = getattr(args, 'report_mode', 'complete')

    try:
        mem, provider, storage, memory_store, user_id = _do_daily_sync(
            config=config, target=target, skip_sync=skip_sync,
            full_sync=full_sync, force_sync=force_sync,
            sync_days=sync_days, quiet=False, report_mode=report_mode,
        )
    except DailyReportReadinessError as exc:
        if getattr(args, "format", "md") == "json":
            import json as _json
            console.print_json(_json.dumps(exc.to_dict(), ensure_ascii=False))
        else:
            console.print(f"[red]{exc}[/]")
            for action in exc.readiness.suggested_actions:
                console.print(f"  [dim]建议操作: {action}[/]")
        raise SystemExit(2) from exc
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        return

    ai_result = mem.front_matter.get('ai_insight', {})
    console.print(f"  ✅ md: {mem.path}")
    if mem.front_matter.get("data_readiness") == "limited":
        console.print("  ⚠️  数据受限：已省略不具备依据的结论，未调用 AI")
    elif ai_result:
        console.print(f"  🤖 AI 洞察: {ai_result.get('model', '已配置模型')}")
    else:
        console.print(f"  🤖 AI 洞察: 规则引擎 fallback")

    # ── JSON-only 模式（跳过 HTML/PNG/终端）──
    if args.format == "json":
        import json as _json
        console.print_json(_json.dumps(mem.front_matter, ensure_ascii=False, indent=2, default=str))
        return

    # ── 渲染 HTML + PNG ──
    output_dir = Path("output")
    ensure_private_dir(output_dir)

    html_path = str(output_dir / f"{target}.html")
    render_daily_html(mem, html_path)
    restrict_private_file(html_path)
    console.print(f"  🌐 HTML: {html_path}")

    png_path = str(output_dir / f"{target}.png")
    try:
        render_daily_image(mem, output_path=png_path, theme=theme)
        restrict_private_file(png_path)
        console.print(f"  🖼️  PNG: {png_path}")
    except Exception as exc:
        console.print(f"  [yellow]⚠️  PNG 生成失败: {exc}[/]")

    # ── 终端摘要 ──
    fm = mem.front_matter
    ya = get_daily_activities(fm)
    sleep = fm.get("last_night_sleep", {})
    morning = fm.get("this_morning", {})
    load = fm.get("training_load", {})
    recovery = fm.get("recovery", {})
    rec = fm.get("recommendation", {})

    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekday_names[target.weekday()]

    console.print()
    console.rule(f"[bold blue]📰 每日训练报告 — {target} {wd}[/]")

    # 当日训练
    if ya.get("activity_state") == "unknown":
        console.print(
            "\n[bold]🏃 当日训练[/]: [yellow]运动数据未同步，训练/休息状态未知[/]"
        )
    elif ya.get("is_rest_day"):
        console.print(f"\n[bold]🏃 当日训练[/]: [dim]休息日（无正式记录）[/]")
        console.print(f"   [dim]全天活动: {ya.get('daily_steps', 0)} 步 | "
                      f"{ya.get('daily_distance_km', 0)} km | "
                      f"活动消耗 {ya.get('daily_active_cal', 0)} cal[/]")
    else:
        console.print(f"\n[bold]🏃 当日训练[/]: {ya.get('day_type', '?')} | "
                      f"{ya.get('total_duration_min', 0)}min | "
                      f"{ya.get('total_distance_km', 0):.1f}km | "
                      f"负荷 {ya.get('total_training_load', 0)}")
        for s in ya.get('sessions', []):
            d_km = s.get('distance_km') or 0
            console.print(f"   [dim]{s['type']}: {s['name']} | {s['duration_min']}min"
                          f"{' | ' + str(d_km) + 'km' if d_km else ''}"
                          f" | HR {s.get('avg_hr', '?')}"
                          f" | load {s.get('training_load', 0)}[/]")

    # 睡眠 + 状态 + 负荷
    quality_emoji = {"excellent": "🟢", "good": "🟢", "fair": "🟡", "poor": "🔴"}
    qe = quality_emoji.get(sleep.get("quality", ""), "")
    sleep_h = sleep.get('total_hours') or 0
    sleep_str = f"{sleep_h}h" if sleep_h > 0 else "—"
    console.print(f"[bold]😴 睡眠[/]: {sleep_str} | "
                  f"评分 {sleep.get('sleep_score', '—')} {qe}")

    status_table = Table(show_header=False, box=None, padding=(0, 2))
    status_table.add_column(style="dim"); status_table.add_column()
    status_table.add_row("静息心率", f"{morning.get('resting_hr', '—')} bpm")
    status_table.add_row("HRV", f"{morning.get('hrv_ms', '—')} ms")
    bb = morning.get('body_battery_morning')
    if bb is not None: status_table.add_row("身体电量", str(bb))
    tr = morning.get('training_readiness_score')
    if tr is not None: status_table.add_row("训练准备", str(tr))
    console.print(status_table)

    acwr_emoji = {"optimal": "🟢", "overreaching": "🟠", "high_risk": "🔴"}
    ae = acwr_emoji.get(load.get("acwr_status", ""), "")
    console.print(f"[bold]📈 负荷[/]: ACWR {load.get('acwr', '—')} {ae} | "
                  f"恢复 {recovery.get('overall_score', '—')}/100 ({recovery.get('level', '—')})")

    # 建议 + AI 洞察
    ai = fm.get("ai_insight", {})
    if ai:
        console.print(f"\n[bold green]🤖 AI 洞察[/] [bold white]{ai.get('conclusion', '')}[/]")
        for obs in ai.get("observations", [])[:3]:
            console.print(f"  [dim]• {obs}[/]")

    console.rule()


def cmd_activities(args: argparse.Namespace) -> None:
    """activities 命令：查询活动列表。"""
    config, provider, storage, memory_store, user_id = _setup()

    days = args.recent or 30
    end = date.today()
    start = end - timedelta(days=days)
    raw = provider.activities.fetch_activities(start, end)

    if args.type:
        raw = [a for a in raw if a.activity_type == args.type]

    activities = [
        {
            "start_time_local": a.start_time,
            "activity_type_name": a.activity_type,
            "activity_name": a.activity_name,
            "duration": a.duration_seconds,
            "distance": a.distance_meters,
            "average_hr": a.avg_heart_rate,
            "activity_training_load": a.training_load,
        }
        for a in raw
    ]

    if args.export:
        storage.export_csv(activities, args.export)
        console.print(f"[green]✅ 已导出到 {args.export}[/]")
        return

    table = Table(title=f"🏃 最近 {days} 天活动")
    table.add_column("日期", style="dim")
    table.add_column("类型")
    table.add_column("名称")
    table.add_column("时长", justify="right")
    table.add_column("距离", justify="right")
    table.add_column("心率", justify="right")
    table.add_column("负荷", justify="right")

    for a in activities[:50]:
        dur = (a.get("duration", 0) or 0) / 60
        dist = (a.get("distance", 0) or 0) / 1000
        table.add_row(
            str(a.get("start_time_local", ""))[:10],
            a.get("activity_type_name", ""),
            str(a.get("activity_name", ""))[:20],
            f"{dur:.0f}min",
            f"{dist:.1f}km" if dist else "",
            str(a.get("average_hr", "")),
            str(a.get("activity_training_load", "")),
        )

    console.print(table)
    console.print(f"[dim]共 {len(activities)} 条记录[/]")


def cmd_health(args: argparse.Namespace) -> None:
    """health 命令：查询健康指标。"""
    config, provider, storage, memory_store, user_id = _setup()

    if args.metric:
        metrics = [args.metric]
    else:
        metrics = ["sleep", "hrv", "heart_rate", "stress", "body_battery"]

    table = Table(title=f"💊 健康指标 (最近 {args.days or 7} 天)")
    table.add_column("日期", style="dim")
    for m in metrics:
        table.add_column(m, justify="right")

    today = date.today()
    for i in range(args.days or 7):
        target = today - timedelta(days=i)
        data = storage.get_health_metrics(user_id, target)
        row = [str(target)]
        for m in metrics:
            if data is None:
                row.append("—")
            elif m == "sleep":
                dur = data.get("sleep_duration_hours", 0) or 0
                row.append(f"{dur:.1f}h")
            elif m == "hrv":
                row.append(str(data.get("hrv_last_night_avg", "—")))
            elif m == "heart_rate":
                row.append(str(data.get("resting_heart_rate", "—")))
            elif m == "stress":
                row.append(str(data.get("avg_stress_level", "—")))
            elif m == "body_battery":
                row.append(str(data.get("body_battery_high", "—")))
            else:
                row.append("✓")
        table.add_row(*row)

    console.print(table)


def cmd_memory(args: argparse.Namespace) -> None:
    """memory 命令：记忆管理。"""
    config, provider, storage, memory_store, user_id = _setup()

    sub = args.memory_subcommand

    if sub == "list":
        mem_type = MemoryType(args.type) if args.type else None
        mem_status = MemoryStatus(args.status) if args.status else None
        tags = args.tag.split(",") if args.tag else None

        if args.search:
            memories = memory_store.search(args.search)
        elif mem_type:
            memories = memory_store.list_by_type(mem_type, status=mem_status, tags=tags)
        else:
            memories = memory_store.query(tags=tags)

        table = Table(title="🧠 记忆列表")
        table.add_column("ID", style="dim")
        table.add_column("类型")
        table.add_column("日期")
        table.add_column("标签")

        for m in memories[:50]:
            table.add_row(
                m.id,
                m.type.value,
                str(m.created_date or ""),
                ", ".join(m.tags[:5]),
            )

        console.print(table)
        console.print(f"[dim]共 {len(memories)} 条记忆[/]")

    elif sub == "show":
        mem = memory_store.get(args.memory_id)
        if mem is None:
            console.print(f"[red]未找到记忆: {args.memory_id}[/]")
            return
        console.print(Panel(mem.body, title=f"🧠 {mem.id} ({mem.type.value})"))
        console.print("[dim]Front Matter:[/]")
        console.print_json(
            __import__("json").dumps(mem.front_matter, ensure_ascii=False, indent=2, default=str)
        )

    elif sub == "summarize":
        period = args.period or "weekly"
        if args.date:
            target = _parse_date(args.date)
        else:
            target = date.today()

        console.print(f"[bold]生成 {period} 摘要...[/]")
        summary = memory_store.generate_weekly_summary(user_id, target)
        console.print(f"[green]✅ 摘要已生成: {summary.id}[/]")

        recovery = memory_store.generate_recovery_summary(user_id, target)
        console.print(f"[green]✅ 恢复摘要已生成: {recovery.id}[/]")

        for cat in ["summaries", "recovery"]:
            memory_store.rebuild_index(f"auto/{cat}")

    elif sub == "check":
        console.print("[bold]🔍 执行完整性检查...[/]")
        result = memory_store.integrity_check()

        console.print(f"\n检查文件: {result['checked']}")
        console.print(f"  [green]通过: {result['pass_count']}[/]")
        console.print(f"  [yellow]警告: {result['warn_count']}[/]")
        console.print(f"  [red]错误: {result['error_count']}[/]")

        if result["issues"]:
            console.print("\n[bold]详情:[/]")
            for issue in result["issues"]:
                style = "red" if issue["level"] == "error" else "yellow"
                console.print(f"  [{style}][{issue['level']}][/] {issue['file']}: {issue['message']}")

    elif sub == "index":
        cats = ["daily", "summaries", "recovery", "execution"]
        for cat in cats:
            memory_store.rebuild_index(f"auto/{cat}")
        console.print("[green]✅ 所有索引已重建[/]")

    else:
        console.print(f"[red]未知 memory 子命令: {sub}[/]")


def cmd_status(args: argparse.Namespace) -> None:
    """status 命令：查看同步状态。"""
    config, provider, storage, memory_store, user_id = _setup()

    status_list = storage.get_all_sync_status(user_id)

    if not status_list:
        console.print("[yellow]暂无同步记录，请先运行 neurun sync[/]")
        return

    table = Table(title="📡 同步状态")
    table.add_column("日期")
    table.add_column("指标")
    table.add_column("状态")

    for s in status_list[-30:]:
        table.add_row(
            str(s.get("date", "")),
            str(s.get("metric", "")),
            str(s.get("status", "")),
        )

    console.print(table)


def cmd_init(args: argparse.Namespace) -> None:
    """init 命令：引导式创建配置文件。"""
    console.rule("[bold green]🚀 neurun Init[/]")
    console.print("首次使用？让我帮你创建配置文件。\n")

    # 1. 选择 Provider
    provider = _ask("运动平台 (garmin/coros)", "garmin")
    if provider not in ("garmin", "coros"):
        console.print("[red]无效的平台，请输入 garmin 或 coros[/]")
        return

    # 2. 账号
    account = password = ""
    hint = "邮箱或手机号" if provider == "coros" else "Garmin Connect 邮箱"
    account = _ask(f"账号 ({hint})")
    if not account:
        console.print("[red]账号不能为空[/]")
        return
    password = _ask("密码")
    if not password:
        console.print("[red]密码不能为空[/]")
        return

    # 4. 存储位置
    console.print("\n[dim]数据存储位置（回车使用默认）[/]")
    location = _ask("数据库路径", "./data/rundown_data.db")
    sync_days = _ask("默认同步天数", "30")

    env_content = f"""# neurun 配置
NEURUN_PROVIDER={provider}
NEURUN_DB_PATH={location}
NEURUN_SYNC_DAYS={sync_days}
NEURUN_LOG_LEVEL=INFO
"""
    env_content += f"NEURUN_ACCOUNT={account}\nNEURUN_PASSWORD={password}\n"
    if provider == "garmin":
        domain = _ask("Garmin 区域 (garmin.com/garmin.cn)", "garmin.com")
        env_content += f"GARMIN_DOMAIN={domain}\n"

    # 写入
    env_path = Path(".env")
    if env_path.exists():
        overwrite = _ask(f"{env_path} 已存在，覆盖？(y/n)", "n")
        if overwrite.lower() != "y":
            console.print("[yellow]已取消[/]")
            return

    atomic_write_private(env_path, env_content, private_parent=False)
    console.print(f"\n[green]✅ 配置已写入: {env_path}[/]")
    # 询问全局配置
    make_global = _ask("同时写入全局配置 ~/.neurun/.env？(y/n)", "y")
    if make_global.lower() == "y":
        global_dir = Path.home() / ".neurun"
        ensure_private_dir(global_dir)
        atomic_write_private(global_dir / ".env", env_content)
        console.print(f"[green]✅ 全局配置已写入: {global_dir / '.env'}[/]")

    # 询问首次同步
    do_sync = _ask("\n是否立即同步数据？(y/n)", "y")
    if do_sync.lower() == "y":
        console.print("\n[bold]开始首次同步...[/]")
        # 构造一个简单的 args namespace
        class SyncArgs:
            from_date = None
            to_date = None
            full = False
            days = int(sync_days) if sync_days.isdigit() else 30
            metrics = None
        cmd_sync(SyncArgs())


def cmd_setup(args: argparse.Namespace) -> None:
    """setup 命令：交互式录入个人资料、最佳成绩和目标。"""
    config, provider, storage, memory_store, user_id = _setup()

    from datetime import datetime
    from src.memory import build_memory_file

    console.rule("[bold green]⚙️  neurun Setup[/]")
    console.print("输入你的基本信息（回车跳过可留空）\n")

    # ── 1. 基本信息 ──
    console.print("[bold]1/4 基本信息[/]")
    height = _ask("身高 (cm)", "175")
    weight = _ask("体重 (kg)", "70")
    age = _ask("年龄", "30")
    gender = _ask("性别 (male/female)", "male")
    location = _ask("训练地点", "")

    # ── 2. 个人最佳成绩 ──
    console.print("\n[bold]2/4 个人最佳成绩[/]")
    pb_5k = _ask("5K 最佳 (格式 MM:SS 或留空)", "")
    pb_10k = _ask("10K 最佳", "")
    pb_hm = _ask("半马最佳", "")
    pb_marathon = _ask("全马最佳", "")
    vo2max = _ask("VO2max 估算值", "")

    # ── 3. 当前目标 ──
    console.print("\n[bold]3/4 训练目标[/]")
    has_goal = _ask("是否设置目标？(y/n)", "y")
    if has_goal.lower() == "y":
        goal_name = _ask("目标名称", "5K 突破")
        goal_distance = _ask("目标距离 (5k/10k/hm/marathon)", "5k")
        goal_intent = _ask("目标意图 (completion/performance)", "performance")
        goal_time = _ask("目标成绩", "")
        if goal_intent not in {"completion", "performance"}:
            goal_intent = "performance" if goal_time else "completion"
        goal_date_str = _ask("目标日期 (YYYY-MM-DD)", str(date.today().replace(year=date.today().year + 1)))
    else:
        goal_name = goal_distance = goal_intent = goal_time = goal_date_str = ""

    # ── 4. 训练偏好 ──
    console.print("\n[bold]4/4 训练偏好[/]")
    pref_time = _ask("偏好训练时间 (morning/afternoon/evening)", "evening")
    pref_workouts = _ask("偏好训练类型 (逗号分隔: 间歇,节奏,长距离,轻松跑)", "间歇,节奏,长距离")
    pref_terrain = _ask("偏好地形 (田径场/公路/混合)", "公路")
    injury_history = _ask("伤病史 (简要描述，无则留空)", "")
    training_philosophy = _ask("训练理念 (一句话)", "数据驱动，极化训练，重视恢复")

    # ── 写入文件 ──
    import yaml

    # Profile
    profile_fm = {
        "type": "fitness_profile",
        "profile_type": "assessment",
        "updated": datetime.now().isoformat(timespec="seconds"),
        "personal_info": {
            "height_cm": int(height) if height.isdigit() else None,
            "weight_kg": int(weight) if weight.isdigit() else None,
            "age": int(age) if age.isdigit() else None,
            "gender": gender,
            "location": location,
        },
        "personal_bests": {},
        "tags": ["fitness-profile", str(date.today().year)],
    }
    if pb_5k:
        profile_fm["personal_bests"]["5k"] = {"time": pb_5k}
    if pb_10k:
        profile_fm["personal_bests"]["10k"] = {"time": pb_10k}
    if pb_hm:
        profile_fm["personal_bests"]["half_marathon"] = {"time": pb_hm}
    if pb_marathon:
        profile_fm["personal_bests"]["marathon"] = {"time": pb_marathon}
    if vo2max:
        profile_fm["personal_bests"]["vo2max_estimate"] = float(vo2max)

    profile_body = f"""# 竞技档案

## 基本信息
- 身高: {height} cm
- 体重: {weight} kg
- 年龄: {age}
- 性别: {gender}
- 地点: {location or '未设置'}

## 个人最佳
"""
    for dist, data in profile_fm["personal_bests"].items():
        if isinstance(data, dict) and "time" in data:
            profile_body += f"- **{dist}**: {data['time']}\n"

    profile_path = Path(config.memory_dir) / "profile" / "fitness-assessment.md"
    atomic_write_private(profile_path, build_memory_file(profile_fm, profile_body))
    console.print(f"  ✅ 竞技档案: {profile_path}")

    # Goal
    if has_goal.lower() == "y" and goal_name:
        goal_id = f"goal-{date.today().year}-{goal_distance}"
        goal_fm = {
            "type": "goal",
            "id": goal_id,
            "goal_type": "time_based",
            "goal_intent": goal_intent or ("performance" if goal_time else "completion"),
            "category": "running",
            "status": "active",
            "priority": "high",
            "created": str(date.today()),
            "target_date": goal_date_str,
            "review_cycle": "weekly",
            "metrics": {f"target_{goal_distance}": goal_time},
            "tags": [goal_distance, str(date.today().year), "active"],
        }
        goal_body = f"""# {goal_name}

## 目标
- 距离: {goal_distance}
- 目标意图: {goal_intent or ('performance' if goal_time else 'completion')}
- 目标成绩: {goal_time}
- 截止日期: {goal_date_str}

## 进度
创建于 {date.today()}，定期更新。
"""
        goal_path = Path(config.memory_dir) / "goals" / "active" / f"{goal_id}.md"
        atomic_write_private(goal_path, build_memory_file(goal_fm, goal_body))
        console.print(f"  ✅ 训练目标: {goal_path}")

    # Preferences
    pref_fm = {
        "type": "coaching_preference",
        "updated": datetime.now().isoformat(timespec="seconds"),
        "training_preferences": {
            "preferred_workouts": [w.strip() for w in pref_workouts.split(",")],
            "preferred_time": pref_time,
            "preferred_terrain": [t.strip() for t in pref_terrain.split(",")],
        },
        "injury_history": [],
        "training_philosophy": training_philosophy,
        "tags": ["preferences", "coaching-style"],
    }
    if injury_history:
        pref_fm["injury_history"].append({
            "description": injury_history,
            "date": str(date.today()),
        })

    pref_body = f"""# 训练偏好

## 训练习惯
- 偏好时间: {pref_time}
- 偏好类型: {pref_workouts}
- 偏好地形: {pref_terrain}

## 训练哲学
{training_philosophy}

## 伤病史
{injury_history or '无'}
"""
    pref_path = Path(config.memory_dir) / "coaching" / "preferences.md"
    atomic_write_private(pref_path, build_memory_file(pref_fm, pref_body))
    console.print(f"  ✅ 训练偏好: {pref_path}")

    console.print("\n[green]✅ Setup 完成！运行 neurun daily 即可自动同步数据并查看日报。[/]")


def _ask(prompt: str, default: str = "") -> str:
    """交互式提问，支持默认值。"""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    else:
        return input(f"  {prompt}: ").strip()


def cmd_mcp(args: argparse.Namespace) -> None:
    """mcp 命令：启动 MCP Server（默认 stdio，支持 SSE/HTTP 传输）。"""
    config, provider, storage, memory_store, user_id = _setup()

    from .mcp_server import create_server

    transport = os.getenv("MCP_TRANSPORT", getattr(args, 'transport', None) or "stdio")
    host = os.getenv("MCP_HOST", getattr(args, 'host', None) or "127.0.0.1")
    port = int(os.getenv("MCP_PORT", str(getattr(args, 'port', None) or 8000)))

    enable_admin_tools = _admin_mcp_tools_enabled(transport, host)
    server = create_server(
        config, provider, storage, memory_store, user_id,
        enable_admin_tools=enable_admin_tools,
    )

    if transport == "stdio":
        console.print("[bold blue]🔌 启动 neurun MCP Server...[/]")
        console.print("[green]✅ MCP Server 已启动 (stdio mode)[/]")
        console.print("[dim]等待 OpenClaw / Claude Desktop 连接...[/]")
        server.run(transport="stdio")
    else:
        console.print(f"[bold blue]🔌 启动 neurun MCP Server ({transport} mode)...[/]")
        console.print(f"[green]✅ 监听 http://{host}:{port}[/]")
        server.run(transport=transport, host=host, port=port)


def _admin_mcp_tools_enabled(transport: str, host: str) -> bool:
    """仅允许显式开启的本地 MCP 注册邀请码管理工具。"""
    requested = os.getenv("NEURUN_ENABLE_ADMIN_TOOLS", "").lower() in ("1", "true", "yes")
    if not requested:
        return False
    serve_mode = os.getenv("NEURUN_SERVE_MODE", "").lower() in ("1", "true", "yes")
    if serve_mode:
        raise ConfigError("公网 serve 模式禁止启用邀请码管理员 MCP tools")
    if transport == "stdio":
        return True
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigError("邀请码管理员 MCP tools 只能监听 localhost 或使用 stdio")
    return True


def _invite_output(
    args: argparse.Namespace,
    payload: Any,
    *,
    file_path: Path | None = None,
) -> None:
    """输出邀请码管理员命令结果。

    file_path 仅在 create 时传入：table 模式直接打印，json 模式写入 stderr，
    保持 stdout JSON 结构稳定（供 AI/MCP 消费）。
    """
    if args.output == "json":
        console.print_json(json.dumps(payload, ensure_ascii=False, default=str))
        if file_path is not None:
            print(f"已写入文件: {file_path}", file=sys.stderr)
        return

    records = payload if isinstance(payload, list) else [payload]
    table = Table(title="neurun Invitations")
    table.add_column("ID")
    table.add_column("Code")
    table.add_column("Status")
    table.add_column("Created")
    table.add_column("Used by")
    for item in records:
        status = "available" if item.get("available") else (
            "revoked" if not item.get("enabled") else "used"
        )
        table.add_row(
            str(item.get("id", "")),
            str(item.get("code", "")),
            status,
            str(item.get("created_at", "")),
            str(item.get("used_by") or "—"),
        )
    console.print(table)
    if file_path is not None:
        console.print(f"已写入文件: {file_path}")


def cmd_invite(args: argparse.Namespace) -> None:
    """invite 命令：在服务器本地生成、查看和停用邀请码。"""
    from .invitations import InvitationError, InvitationStore

    try:
        if not args.invite_subcommand:
            raise InvitationError("缺少 invite 子命令：create、list、show 或 revoke")
        config = get_config(validate_credentials=False)
        store = InvitationStore(config.invite_codes_path)
        if args.invite_subcommand == "create":
            records = [item.to_admin_dict(reveal=True) for item in store.create(args.count)]
            _invite_output(args, records, file_path=store.path)
        elif args.invite_subcommand == "list":
            _invite_output(args, [item.to_admin_dict() for item in store.list_all()])
        elif args.invite_subcommand == "show":
            _invite_output(args, store.get(args.invitation_id).to_admin_dict(reveal=args.reveal))
        elif args.invite_subcommand == "revoke":
            _invite_output(args, store.revoke(args.invitation_id).to_admin_dict())
    except InvitationError as exc:
        if getattr(args, "output", "table") == "json":
            print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_serve() -> None:
    """SAE / VPS 入口：启动 Web 应用服务（含 MCP Server SSE）。

    环境变量驱动：
    - NEURUN_DATA_DIR: 数据根目录 (默认 ./data)
    - NEURUN_SERVE_MODE=true: 跳过 Garmin 凭证校验
    - MCP_HOST / MCP_PORT: 监听地址
    """
    import logging as std_logging

    std_logging.basicConfig(
        level=std_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 标记 Web 服务模式（跳过全局 Garmin 凭证校验）
    os.environ["NEURUN_SERVE_MODE"] = "true"

    config = get_config()

    # 初始化用户管理器
    from .users import UserManager
    user_manager = UserManager(config.data_dir)

    # 容器启动时恢复所有用户的 SQLite 备份
    _restore_all_users(user_manager)

    # 创建带 Web 路由的 MCP Server
    from .web import create_web_server
    server = create_web_server(config, user_manager)

    transport = os.getenv("MCP_TRANSPORT", "sse")
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8080"))

    logger.info("🚀 neurun Web 应用启动中... transport=%s host=%s port=%s", transport, host, port)
    logger.info("📂 数据目录: %s", config.data_dir)

    server.run(transport=transport, host=host, port=port)


def _restore_all_users(user_manager) -> None:
    """容器启动时从备份恢复所有用户的 SQLite。"""
    from .storage import Storage
    from .config import Config

    users = user_manager.list_all()
    logger.info("扫描到 %d 个已注册用户", len(users))

    dummy_config = Config()
    for user in users:
        try:
            backup_path = user_manager.get_backup_path(user.api_key)
            user_db_path = user_manager.get_db_path(user.api_key)

            # 临时 Storage 实例用于恢复
            class _TempCfg:
                db_path = user_db_path
            storage = Storage(_TempCfg())  # type: ignore[arg-type]
            if storage.restore_from(backup_path):
                logger.info("用户 %s: 已从备份恢复数据库", user.api_key)
        except Exception as exc:
            logger.warning("用户 %s: 恢复失败 (%s)", user.api_key, exc)


# ═══════════════════════════════════════════════════════════════
# Argument Parser
# ═══════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="neurun",
        description="neurun — Your AI running coach",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="command", help="可用命令")

    # ── init ──────────────────────────────────
    sub.add_parser("init", help="引导式创建配置文件 (.env)")

    # ── auth ──────────────────────────────────
    sub.add_parser("auth", help="在本地浏览器完成数据源认证")

    # ── sync ──────────────────────────────────
    p_sync = sub.add_parser("sync", help="纯数据同步（不含记忆生成）")
    p_sync.add_argument("--days", type=int, help="同步最近 N 天")
    p_sync.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    p_sync.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    p_sync.add_argument("--metrics", nargs="*", help="指定指标（逗号分隔）")
    p_sync.add_argument("--full", action="store_true", help="全量同步")
    p_sync.add_argument("--force", action="store_true", help="强制覆盖：清除已有数据后重新全量拉取")

    # ── daily ─────────────────────────────────
    p_daily = sub.add_parser("daily", help="自动同步数据并生成日报（md + HTML + PNG + AI 洞察）")
    p_daily.add_argument("--date", help="报告日期 YYYY-MM-DD (默认今天)")
    p_daily.add_argument("--format", choices=["md", "json"], default="md",
                         help="md(终端+文件) | json(仅 JSON 输出)")
    p_daily.add_argument("--theme", choices=["fresh", "sport", "dark"], default="sport",
                         help="HTML/PNG 主题 (默认 sport)")
    p_daily.add_argument("--sync-days", type=int, default=None,
                         help="同步最近 N 天数据后生成报告 (默认自动检测)")
    p_daily.add_argument("--skip-sync", action="store_true",
                         help="跳过自动同步，仅基于本地已有数据生成报告")
    p_daily.add_argument("-m", "--report-mode", choices=["complete", "limited"],
                         default="complete",
                         help="报告门禁模式：完整报告或显式受限版（默认 complete）")
    p_daily.add_argument("--full", action="store_true",
                         help="全量同步（3年）后生成报告")
    p_daily.add_argument("--force", action="store_true",
                         help="强制覆盖已有数据后重新同步")

    # ── activities ────────────────────────────
    p_act = sub.add_parser("activities", help="查询活动列表")
    p_act.add_argument("--recent", type=int, help="最近 N 条")
    p_act.add_argument("--type", help="按运动类型筛选")
    p_act.add_argument("--export", help="导出 CSV 文件路径")

    # ── health ────────────────────────────────
    p_health = sub.add_parser("health", help="查询健康指标")
    p_health.add_argument("--metric", help="指定指标 key")
    p_health.add_argument("--days", type=int, help="最近 N 天")
    p_health.add_argument("--export", help="导出 CSV 文件路径")

    # ── memory ────────────────────────────────
    p_mem = sub.add_parser("memory", help="记忆管理")
    p_mem_sub = p_mem.add_subparsers(dest="memory_subcommand", help="子命令")

    p_list = p_mem_sub.add_parser("list", help="列出记忆")
    p_list.add_argument("--type", help="按类型筛选")
    p_list.add_argument("--status", help="按状态筛选")
    p_list.add_argument("--tag", help="按标签筛选")
    p_list.add_argument("--search", help="关键词搜索")

    p_show = p_mem_sub.add_parser("show", help="查看单条记忆")
    p_show.add_argument("memory_id", help="记忆 ID")

    p_sum = p_mem_sub.add_parser("summarize", help="手动触发摘要生成")
    p_sum.add_argument("--period", choices=["weekly", "monthly"], default="weekly")
    p_sum.add_argument("--date", help="目标日期")

    p_mem_sub.add_parser("check", help="完整性校验")
    p_mem_sub.add_parser("index", help="重建所有索引文件")

    # ── status ────────────────────────────────
    sub.add_parser("status", help="查看同步状态")

    # ── setup ─────────────────────────────────
    sub.add_parser("setup", help="交互式录入个人资料和目标")

    # ── mcp ───────────────────────────────────
    p_mcp = sub.add_parser("mcp", help="启动 MCP Server（默认 stdio，支持 SSE/HTTP）")
    p_mcp.add_argument("--transport", choices=["stdio", "sse", "http", "streamable-http"],
                       help="传输模式 (环境变量: MCP_TRANSPORT)")
    p_mcp.add_argument("--host", help="监听地址 (环境变量: MCP_HOST)")
    p_mcp.add_argument("--port", type=int, help="监听端口 (环境变量: MCP_PORT)")

    # ── invite（服务器本地管理员命令）──────────
    p_invite = sub.add_parser("invite", help="管理 Web 注册邀请码")
    p_invite_sub = p_invite.add_subparsers(dest="invite_subcommand", help="子命令")

    p_invite_create = p_invite_sub.add_parser("create", help="生成 6 位随机一次性邀请码")
    p_invite_create.add_argument("-n", "--count", type=int, default=1, help="生成数量（1-100）")
    p_invite_create.add_argument("-o", "--output", choices=["table", "json"], default="table")

    p_invite_list = p_invite_sub.add_parser("list", help="列出邀请码（默认掩码）")
    p_invite_list.add_argument("-o", "--output", choices=["table", "json"], default="table")

    p_invite_show = p_invite_sub.add_parser("show", help="查看单个邀请码")
    p_invite_show.add_argument("invitation_id", help="邀请码 ID")
    p_invite_show.add_argument("--reveal", action="store_true", help="显示完整邀请码（仅本地 CLI）")
    p_invite_show.add_argument("-o", "--output", choices=["table", "json"], default="table")

    p_invite_revoke = p_invite_sub.add_parser("revoke", help="停用邀请码")
    p_invite_revoke.add_argument("invitation_id", help="邀请码 ID")
    p_invite_revoke.add_argument("-o", "--output", choices=["table", "json"], default="table")

    return parser


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════


COMMAND_HANDLERS = {
    "init": cmd_init,
    "auth": cmd_auth,
    "sync": cmd_sync,
    "daily": cmd_daily,
    "activities": cmd_activities,
    "health": cmd_health,
    "memory": cmd_memory,
    "setup": cmd_setup,
    "status": cmd_status,
    "mcp": cmd_mcp,
    "invite": cmd_invite,
}


def main(argv: list[str] | None = None) -> None:
    """neurun CLI 入口。

    Args:
        argv: 命令行参数列表，None 表示使用 sys.argv。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    handler = COMMAND_HANDLERS.get(args.command)
    if handler is None:
        console.print(f"[red]未知命令: {args.command}[/]")
        parser.print_help()
        return

    try:
        handler(args)
    except ConfigError as exc:
        console.print(f"[red]❌ 配置错误: {exc}[/]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消[/]")
        sys.exit(0)
    except Exception as exc:
        logger.exception("命令执行失败")
        console.print(f"[red]❌ 错误: {exc}[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
