"""活动详情模块 — 从运动平台 API 获取并存储每项活动的详细数据。

包括：分段配速、步频、功率、触地时间、步幅、垂直振幅、爬升等。
数据存储在 activity_details 表和 activity_splits 表中。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ── DB Schema ────────────────────────────────

CREATE_ACTIVITY_DETAILS = """
CREATE TABLE IF NOT EXISTS activity_details (
    activity_id    VARCHAR PRIMARY KEY,
    user_id        INTEGER NOT NULL,
    detail_json    TEXT NOT NULL,        -- 完整 API 响应 JSON
    fetched_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id)
)
"""

CREATE_ACTIVITY_SPLITS = """
CREATE TABLE IF NOT EXISTS activity_splits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id    VARCHAR NOT NULL,
    split_index    INTEGER NOT NULL,
    split_type     VARCHAR,             -- RWD_STAND / INTERVAL_ACTIVE / RWD_RUN / RWD_WALK
    distance_m     FLOAT,
    duration_sec   FLOAT,
    pace_per_km    FLOAT,               -- sec/km
    avg_hr         INTEGER,
    max_hr         INTEGER,
    avg_cadence    FLOAT,
    avg_power      FLOAT,
    normalized_power FLOAT,
    ground_contact_ms FLOAT,
    stride_length_cm FLOAT,
    vertical_osc_mm FLOAT,
    elevation_gain FLOAT,
    elevation_loss FLOAT,
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id)
)
"""

CREATE_ACTIVITY_SUMMARY_FACTS = """
CREATE TABLE IF NOT EXISTS activity_summary_facts (
    activity_id VARCHAR PRIMARY KEY,
    user_id INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    detail_hash VARCHAR NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (activity_id) REFERENCES activities(activity_id)
)
"""


def ensure_tables(storage) -> None:
    """确保 activity_details 和 activity_splits 表存在。"""
    from sqlalchemy import text
    session = storage.db.get_session()
    try:
        session.execute(text(CREATE_ACTIVITY_DETAILS))
        session.execute(text(CREATE_ACTIVITY_SPLITS))
        session.execute(text(CREATE_ACTIVITY_SUMMARY_FACTS))
        # 存量表迁移：统一收拢后不再持久化 schema 版本号，重算以 detail_hash 为准。
        # 迁移失败（如旧 SQLite 不支持 DROP COLUMN 或事务异常）时记录日志，
        # store_activity_detail 会按列兼容兜底，不能静默吞掉。
        try:
            session.execute(text(
                "ALTER TABLE activity_summary_facts DROP COLUMN summary_version"
            ))
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.info("activity_summary_facts 版本列迁移跳过（已在或不可删）: %s", exc)
        session.commit()
        logger.info("activity_details / activity_splits 表已就绪")
    except Exception as exc:
        logger.warning("创建详情表: %s", exc)
    finally:
        session.close()


# ── Fetch ────────────────────────────────────


def fetch_activity_detail(api_client: Any, activity_id: str) -> dict[str, Any] | None:
    """从运动平台 API 获取单条活动的完整详情。

    Returns:
        API 返回的完整 dict，失败返回 None。
    """
    try:
        detail = api_client.connectapi(
            f"/activity-service/activity/{activity_id}"
        )
        return detail
    except Exception as exc:
        logger.warning("获取活动详情 %s 失败: %s", activity_id, exc)
        return None


# ── Store ────────────────────────────────────


def fetch_activity_splits(api_client: Any, activity_id: str) -> list[dict[str, Any]]:
    """拉取 Garmin 官方分段（/splits → lapDTOs）。

    lapDTOs 是可信分段（距离/时长合计与 summary 一致），
    替换 detail.splitSummaries 的异常数据（×2）；仅跑步活动调用。
    """
    try:
        resp = api_client.connectapi(
            f"/activity-service/activity/{activity_id}/splits"
        )
    except Exception:
        return []
    laps = (resp or {}).get("lapDTOs") if isinstance(resp, dict) else None
    return laps if isinstance(laps, list) else []


def store_activity_detail(
    storage,
    user_id: int,
    activity_id: str,
    detail: dict[str, Any],
) -> bool:
    """存储活动详情到 DB。"""
    from sqlalchemy import text
    session = storage.db.get_session()
    try:
        # 存储完整 JSON
        session.execute(text("""
            INSERT OR REPLACE INTO activity_details (activity_id, user_id, detail_json)
            VALUES (:aid, :uid, :json)
        """), {
            "aid": str(activity_id),
            "uid": user_id,
            "json": json.dumps(detail, ensure_ascii=False),
        })

        from .providers.normalization import normalize_activity_detail

        normalized_detail = normalize_activity_detail(detail)

        # 存储分段数据
        session.execute(text(
            "DELETE FROM activity_splits WHERE activity_id = :aid"
        ), {"aid": str(activity_id)})
        splits = _extract_splits(normalized_detail)
        for i, s in enumerate(splits):
            dist = _value(s, "distance", "distance_m", "distanceMeters") or 0
            dur = _value(s, "duration", "duration_sec", "durationSeconds") or 0
            pace = (dur / (dist / 1000)) if dist > 0 else None

            session.execute(text("""
                INSERT OR REPLACE INTO activity_splits
                    (activity_id, split_index, split_type, distance_m, duration_sec,
                     pace_per_km, avg_hr, max_hr, avg_cadence, avg_power,
                     normalized_power, ground_contact_ms, stride_length_cm,
                     vertical_osc_mm, elevation_gain, elevation_loss)
                VALUES (:aid, :idx, :stype, :dist, :dur,
                        :pace, :ahr, :mhr, :cad, :pwr,
                        :npwr, :gct, :sl, :vo, :eg, :el)
            """), {
                "aid": str(activity_id),
                "idx": i,
                "stype": _value(
                    s, "intensityType", "splitType", "split_type",
                    "type", "lapType", "stepType",
                ) or "unknown",
                "dist": dist,
                "dur": dur,
                "pace": round(pace, 1) if pace else None,
                "ahr": _value(s, "averageHR", "avgHr", "avg_hr"),
                "mhr": _value(s, "maxHR", "maxHr", "max_hr"),
                "cad": _value(s, "averageRunCadence", "avgCadence", "avg_cadence"),
                "pwr": _value(s, "averagePower", "avgPower", "avg_power"),
                "npwr": _value(s, "normalizedPower", "normalized_power"),
                "gct": _value(s, "groundContactTime", "ground_contact_ms"),
                "sl": _value(s, "strideLength", "stride_length_cm"),
                "vo": _value(s, "verticalOscillation", "vertical_osc_mm"),
                "eg": _value(s, "elevationGain", "ascent", "elevation_gain") or 0,
                "el": _value(s, "elevationLoss", "descent", "elevation_loss") or 0,
            })

        # Summary extraction is deliberately best-effort: a provider field drift
        # must not prevent retaining the original detail or its usable splits.
        try:
            from .summary_extraction import build_session_summary

            facts = build_session_summary(normalized_detail)
            summary_json = json.dumps(facts.to_dict(), ensure_ascii=False, sort_keys=True)
            # 兼容存量表：若 summary_version 列未被迁移删除，则填空串占位，
            # 避免 NOT NULL 约束导致摘要重建失败（旧表迁移失败时）。
            has_version_col = any(
                row[1] == "summary_version"  # PRAGMA table_info: cid, name, type, ...
                for row in session.execute(
                    text("PRAGMA table_info(activity_summary_facts)")
                ).fetchall()
            )
            if has_version_col:
                session.execute(text("""
                    INSERT OR REPLACE INTO activity_summary_facts
                        (activity_id, user_id, summary_json, summary_version, detail_hash)
                    VALUES (:aid, :uid, :summary, '', :detail_hash)
                """), {
                    "aid": str(activity_id), "uid": user_id,
                    "summary": summary_json,
                    "detail_hash": facts.data_quality["source_hash"],
                })
            else:
                session.execute(text("""
                    INSERT OR REPLACE INTO activity_summary_facts
                        (activity_id, user_id, summary_json, detail_hash)
                    VALUES (:aid, :uid, :summary, :detail_hash)
                """), {
                    "aid": str(activity_id), "uid": user_id,
                    "summary": summary_json,
                    "detail_hash": facts.data_quality["source_hash"],
                })
        except Exception as exc:
            logger.warning("活动摘要提炼失败 activity_id=%s error_type=%s", activity_id, type(exc).__name__)

        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        logger.warning("存储活动详情 %s 失败: %s", activity_id, exc)
        return False
    finally:
        session.close()


def _is_running(activity: dict[str, Any]) -> bool:
    """判断活动是否跑步（activity_type 或名称含 run/跑/越野）。"""
    text = " ".join((
        str(activity.get("activity_type") or ""),
        str(activity.get("activity_name") or ""),
    )).lower()
    return any(token in text for token in ("run", "跑", "越野", "trail"))


def _value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _flatten_lap_list(laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Coros lapList 展开为有顺序的分段序列（lapItemList 子项优先）。"""
    flat: list[dict[str, Any]] = []
    for lap in laps:
        items = lap.get("lapItemList") or []
        if isinstance(items, list) and items and all(
            isinstance(item, dict) for item in items
        ):
            flat.extend(items)
        else:
            flat.append(lap)
    return flat


def _extract_splits(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容 Provider 常见详情结构提取有顺序的 laps/splits。"""
    candidates = (
        "lapDTOs", "splitSummaries", "splits", "laps", "intervals", "segments",
        "lapList",
    )
    queue: list[dict[str, Any]] = [detail]
    visited: set[int] = set()
    while queue:
        current = queue.pop(0)
        marker = id(current)
        if marker in visited:
            continue
        visited.add(marker)
        for key in candidates:
            value = current.get(key)
            if value and isinstance(value, list) and all(isinstance(item, dict) for item in value):
                if key == "lapList":
                    return _flatten_lap_list(value)
                return value
        for value in current.values():
            if isinstance(value, dict):
                queue.append(value)
    return []


# ── Sync ─────────────────────────────────────


def sync_all_activity_details(
    storage,
    api_client: Any,
    user_id: int,
    days: int = 30,
) -> dict[str, int]:
    """同步指定天数内所有活动的详情数据。

    Returns:
        {fetched, stored, skipped} 统计。
    """
    ensure_tables(storage)

    from datetime import date as dt_date, timedelta
    from sqlalchemy import text

    start = dt_date.today() - timedelta(days=days)
    end = dt_date.today()

    # 获取日期范围内所有 activity IDs（附带类型，跑步才拉取官方分段）
    session = storage.db.get_session()
    rows = session.execute(text("""
        SELECT activity_id, activity_type, activity_name FROM activities
        WHERE activity_date >= :start AND activity_date <= :end
        ORDER BY activity_date
    """), {"start": str(start), "end": str(end)}).fetchall()
    session.close()

    activity_ids = [r[0] for r in rows]
    running_ids = {
        str(r[0]) for r in rows
        if _is_running({"activity_type": r[1], "activity_name": r[2]})
    }

    stats = {"total": len(activity_ids), "fetched": 0, "stored": 0, "skipped": 0}

    for aid in activity_ids:
        # 检查是否已有详情
        session = storage.db.get_session()
        exists = session.execute(text(
            "SELECT 1 FROM activity_details WHERE activity_id = :aid"
        ), {"aid": str(aid)}).fetchone()
        session.close()

        if exists:
            stats["skipped"] += 1
            continue

        detail = fetch_activity_detail(api_client, str(aid))
        if detail is None:
            continue

        # 跑步活动：拉取 Garmin 官方分段（lapDTOs 可信），注入 detail 供
        # activity_splits 与 session-summary 使用；其他运动暂不拉取。
        if str(aid) in running_ids:
            laps = fetch_activity_splits(api_client, str(aid))
            if laps:
                detail["lapDTOs"] = laps

        stats["fetched"] += 1
        if store_activity_detail(storage, user_id, str(aid), detail):
            stats["stored"] += 1

    logger.info("活动详情同步完成: %s", stats)
    return stats


# ── Query ────────────────────────────────────


def _get_session(db):
    """兼容 Storage 和 HealthDB 获取 session。"""
    if hasattr(db, 'db'):
        return db.db.get_session()
    return db.get_session()


def get_activity_detail(
    db, activity_id: str,
) -> dict[str, Any] | None:
    """从 DB 读取活动详情。"""
    from sqlalchemy import text

    session = _get_session(db)
    try:
        row = session.execute(text("""
            SELECT detail_json FROM activity_details WHERE activity_id = :aid
        """), {"aid": str(activity_id)}).fetchone()
    finally:
        session.close()

    if row is None:
        return None
    return json.loads(row[0])


def get_activity_splits(
    db, activity_id: str,
) -> list[dict[str, Any]]:
    """从 DB 读取活动分段数据。"""
    from sqlalchemy import text

    session = _get_session(db)
    try:
        rows = session.execute(text("""
            SELECT split_index, split_type, distance_m, duration_sec,
                   pace_per_km, avg_hr, max_hr, avg_cadence,
                   avg_power, ground_contact_ms, stride_length_cm,
                   vertical_osc_mm, elevation_gain, elevation_loss
            FROM activity_splits
            WHERE activity_id = :aid
            ORDER BY split_index
        """), {"aid": str(activity_id)}).fetchall()
    finally:
        session.close()

    return [
        {
            "index": r[0], "type": r[1], "distance_m": r[2],
            "duration_sec": r[3], "pace_per_km": r[4],
            "avg_hr": r[5], "max_hr": r[6], "avg_cadence": r[7],
            "avg_power": r[8], "ground_contact_ms": r[9],
            "stride_length_cm": r[10], "vertical_osc_mm": r[11],
            "elevation_gain": r[12], "elevation_loss": r[13],
        }
        for r in rows
    ]


def get_activity_summary_facts(
    db, activity_id: str,
) -> dict[str, Any] | None:
    """Return the persisted aggregate facts, never the raw detail payload."""
    from sqlalchemy import text

    session = _get_session(db)
    try:
        row = session.execute(text("""
            SELECT summary_json FROM activity_summary_facts WHERE activity_id = :aid
        """), {"aid": str(activity_id)}).fetchone()
    finally:
        session.close()
    return json.loads(row[0]) if row else None


def build_session_analysis_text(
    db, activity_id: str, activity_name: str,
) -> str:
    """为一条活动生成详细分析文本（供日报和 AI 使用）。

    Args:
        db: HealthDB 实例或 Storage 实例。
    """
    # 兼容 Storage 和 HealthDB
    if hasattr(db, 'db'):
        db = db.db
    splits = get_activity_splits(db, activity_id)
    # Keep report/AI text generation on compact facts; raw detail remains a
    # storage/debug boundary only.
    summary_facts = get_activity_summary_facts(db, activity_id)

    if not splits:
        return ""

    lines = [f"\n### {activity_name}"]

    # 从 summary 提取关键指标
    if summary_facts:
        volume = summary_facts.get("volume") or {}
        structure = summary_facts.get("structure") or {}
        terrain = summary_facts.get("terrain") or {}
        dist = (volume.get("distance_m", 0) or 0) / 1000
        dur = (volume.get("duration_s", 0) or 0) / 60
        elev = terrain.get("ascent_m", 0) or 0
        intensity = summary_facts.get("intensity") or {}
        hr = 0
        cad = structure.get("avg_cadence", 0) or 0
        pwr = 0
        npwr = 0
        gct = structure.get("avg_gct", 0) or 0
        sl = structure.get("avg_stride", 0) or 0
        vo = structure.get("avg_vo", 0) or 0
        te = ""
        vo2max = None

        lines.append(f"距离 {dist:.2f}km | 时长 {dur:.0f}min | 爬升 {elev:.0f}m")
        metrics = [f"HR {hr}", f"Cadence {cad:.0f}"]
        if pwr:
            metrics.append(f"Power {pwr:.0f}W (NP {npwr:.0f}W)")
        if gct:
            metrics.append(f"GCT {gct:.0f}ms")
        if sl:
            metrics.append(f"步幅 {sl:.1f}cm")
        if te:
            metrics.append(f"TE {te}")
        lines.append(" | ".join(metrics))

    # 分段配速
    active_splits = [s for s in splits if s["type"] in
                     ("INTERVAL_ACTIVE", "RWD_RUN")]
    if active_splits:
        lines.append("\n**分段配速**:")
        for s in active_splits:
            dist = (s["distance_m"] or 0) / 1000
            dur = (s["duration_sec"] or 0) / 60
            pace = s["pace_per_km"]
            hr = s["avg_hr"]
            cad = s["avg_cadence"]
            pace_str = f"{int(pace//60)}:{int(pace%60):02d}/km" if pace else "—"
            parts = [f"{dist:.1f}km {pace_str}"]
            if hr:
                parts.append(f"HR{hr:.0f}")
            if cad:
                parts.append(f"cad{cad:.0f}")
            lines.append(f"- " + " | ".join(parts))

    return "\n".join(lines)
