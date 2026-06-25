"""活动详情模块 — 从 Garmin API 获取并存储每项活动的详细数据。

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


def ensure_tables(storage) -> None:
    """确保 activity_details 和 activity_splits 表存在。"""
    from sqlalchemy import text
    session = storage.db.get_session()
    try:
        session.execute(text(CREATE_ACTIVITY_DETAILS))
        session.execute(text(CREATE_ACTIVITY_SPLITS))
        session.commit()
        logger.info("activity_details / activity_splits 表已就绪")
    except Exception as exc:
        logger.warning("创建详情表: %s", exc)
    finally:
        session.close()


# ── Fetch ────────────────────────────────────


def fetch_activity_detail(api_client: Any, activity_id: str) -> dict[str, Any] | None:
    """从 Garmin API 获取单条活动的完整详情。

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


def store_activity_detail(
    storage,
    user_id: int,
    activity_id: str,
    detail: dict[str, Any],
) -> None:
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

        # 存储分段数据
        splits = detail.get("splitSummaries", [])
        for i, s in enumerate(splits):
            dist = s.get("distance", 0) or 0
            dur = s.get("duration", 0) or 0
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
                "stype": s.get("splitType", "unknown"),
                "dist": dist,
                "dur": dur,
                "pace": round(pace, 1) if pace else None,
                "ahr": s.get("averageHR"),
                "mhr": s.get("maxHR"),
                "cad": s.get("averageRunCadence"),
                "pwr": s.get("averagePower"),
                "npwr": s.get("normalizedPower"),
                "gct": s.get("groundContactTime"),
                "sl": s.get("strideLength"),
                "vo": s.get("verticalOscillation"),
                "eg": s.get("elevationGain", 0) or 0,
                "el": s.get("elevationLoss", 0) or 0,
            })

        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("存储活动详情 %s 失败: %s", activity_id, exc)
    finally:
        session.close()


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

    # 获取日期范围内所有 activity IDs
    session = storage.db.get_session()
    rows = session.execute(text("""
        SELECT activity_id FROM activities
        WHERE activity_date >= :start AND activity_date <= :end
        ORDER BY activity_date
    """), {"start": str(start), "end": str(end)}).fetchall()
    session.close()

    activity_ids = [r[0] for r in rows]

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

        stats["fetched"] += 1
        store_activity_detail(storage, user_id, str(aid), detail)
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
    row = session.execute(text("""
        SELECT detail_json FROM activity_details WHERE activity_id = :aid
    """), {"aid": str(activity_id)}).fetchone()
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
    rows = session.execute(text("""
        SELECT split_index, split_type, distance_m, duration_sec,
               pace_per_km, avg_hr, max_hr, avg_cadence,
               avg_power, ground_contact_ms, stride_length_cm,
               vertical_osc_mm, elevation_gain, elevation_loss
        FROM activity_splits
        WHERE activity_id = :aid
        ORDER BY split_index
    """), {"aid": str(activity_id)}).fetchall()
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
    detail = get_activity_detail(db, activity_id)

    if not splits:
        return ""

    lines = [f"\n### {activity_name}"]

    # 从 summary 提取关键指标
    if detail:
        summary = detail.get("summaryDTO", {})
        dist = (summary.get("distance", 0) or 0) / 1000
        dur = (summary.get("duration", 0) or 0) / 60
        elev = summary.get("elevationGain", 0) or 0
        hr = summary.get("averageHR", 0)
        cad = summary.get("averageRunCadence", 0)
        pwr = summary.get("averagePower", 0)
        npwr = summary.get("normalizedPower", 0)
        gct = summary.get("groundContactTime", 0)
        sl = summary.get("strideLength", 0)
        vo = summary.get("verticalOscillation", 0)
        te = summary.get("trainingEffectLabel", "")
        vo2max = summary.get("vO2MaxValue")

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
