"""AI 教练模块 — 通过 DeepSeek API + Tool Use 生成训练洞察。

Prompt 外置在 prompts/coach.md，可随时编辑调整。
数据收集抽象为 tools，AI 自行决定需要调用哪些工具获取上下文。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .training_analysis import ActivityDayState, natural_week_bounds

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "coach.md"

# ═══════════════════════════════════════════════════════════════
# Prompt
# ═══════════════════════════════════════════════════════════════


def load_coach_prompt() -> str:
    """从 prompts/coach.md 加载系统提示词（用户可随时编辑）。"""
    if _PROMPT_FILE.exists():
        return _PROMPT_FILE.read_text(encoding="utf-8")
    return "你是一位专业的跑步教练 AI，名叫 neurun Coach。"


# ═══════════════════════════════════════════════════════════════
# Tool Definitions (OpenAI function-calling format)
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_athlete_profile",
            "description": "获取运动员档案：身高体重年龄性别、个人最佳成绩(5K/10K/半马/全马/VO2max)、训练偏好(时间/地形/类型/伤病/理念)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_goals",
            "description": "获取所有活跃训练目标：目标距离、目标成绩、截止日期、周跑量目标",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_history",
            "description": "直接从 SQLite 原始活动和健康数据获取最近 N 天完整训练史，不依赖是否生成历史日报；包含训练量、恢复趋势和结构化课型",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "查询最近多少天的数据，建议 14-30",
                    }
                },
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_week_progress",
            "description": "按报告日期所在自然周（周一至周日）查询截至该日的训练次数、跑量、负荷和活动数据覆盖；专用于周目标完成度，不是滚动 7 天",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "报告日期，YYYY-MM-DD 格式",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recovery_pattern",
            "description": "分析个体恢复模式：近 N 天所有训练日和休息日的 HRV/RHR/身体电量/恢复评分，高强度训练后需要几天恢复",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "分析天数，建议 14-30",
                    }
                },
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_cycle",
            "description": "获取训练周期分析：距离每个活跃目标的比赛还有多少周、当前处于什么训练阶段(基础期/强化期/高峰期/减量期/比赛周)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_report",
            "description": "获取指定日期的完整日报：训练详情(每节训练的类型/名称/时长/距离/心率/负荷)、睡眠(时长/深睡/REM/评分)、晨起指标(HRV/RHR/电量/训练准备)、负荷(ACWR/状态)、恢复评分",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "日期，YYYY-MM-DD 格式",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_today_data",
            "description": "获取今天/最新日报的核心指标摘要：训练摘要、睡眠、HRV、RHR、电量、恢复评分、ACWR，适合快速了解当前状态",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_plan",
            "description": "获取当前运动大纲：目标比赛、训练阶段、每周结构、周跑量目标、关键课次、配速区间、恢复警戒线",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_training_plan",
            "description": "仅在用户明确确认修改训练方案后保存完整内容；日报分析和一般建议不得调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "完整的大纲内容（Markdown 格式），包含：目标比赛、训练阶段、每周结构、周跑量目标、关键课次、配速区间、恢复警戒线、最近调整说明",
                    }
                },
                "required": ["content"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# Tool Executors
# ═══════════════════════════════════════════════════════════════


def _exec_get_athlete_profile(memory_store: Any) -> str:
    """执行 get_athlete_profile 工具。"""
    parts = []
    try:
        mem = memory_store.get("fitness-assessment")
        if mem:
            fm = mem.front_matter or {}
            info = fm.get("personal_info", {}) or {}
            pbs = fm.get("personal_bests", {}) or {}
            if info:
                items = []
                for k, label in [("height_cm", "身高"), ("weight_kg", "体重"), ("age", "年龄"), ("gender", "性别")]:
                    if info.get(k):
                        items.append(f"{label}: {info[k]}")
                if items:
                    parts.append("身体数据: " + " | ".join(items))
            if pbs:
                pb_items = []
                for dist in ["5k", "10k", "half_marathon", "marathon"]:
                    v = pbs.get(dist, {})
                    if isinstance(v, dict) and v.get("time"):
                        pb_items.append(f"{dist}: {v['time']}")
                if pbs.get("vo2max_estimate"):
                    pb_items.append(f"VO2max: {pbs['vo2max_estimate']}")
                if pb_items:
                    parts.append("最佳成绩: " + " | ".join(pb_items))
    except Exception:
        pass
    try:
        pref = memory_store.get("preferences")
        if pref and pref.body:
            parts.append("训练偏好:\n" + pref.body.strip()[:600])
    except Exception:
        pass
    return "\n\n".join(parts) if parts else "暂无运动员档案"


def _exec_get_training_goals(memory_store: Any) -> str:
    """执行 get_training_goals 工具。"""
    try:
        goals = memory_store.list_by_type("goal", status="active")
        if not goals:
            return "暂无活跃训练目标"
        lines = []
        for g in goals:
            fm = g.front_matter or {}
            m = fm.get("metrics", {})
            dist = next((k.replace("target_", "") for k in m if k.startswith("target_")), "?")
            lines.append(
                f"- {fm.get('title') or g.id}: {dist} "
                f"目标{m.get('target_'+dist, '—')} "
                f"截止{fm.get('target_date', '—')} "
                f"周跑量{m.get('weekly_mileage_km', '—')}km "
                f"状态{fm.get('status', 'active')}"
            )
        return "活跃目标:\n" + "\n".join(lines)
    except Exception:
        return "无法获取目标"


def _get_history_entries(memory_store: Any, days: int) -> list[dict[str, Any]]:
    """优先读取 SQLite 完整历史，兼容尚未接入数据库的旧 MemoryStore。"""
    if hasattr(memory_store, "get_training_history_entries"):
        try:
            entries = memory_store.get_training_history_entries(days=days)
            if entries:
                return entries
        except Exception as exc:
            logger.warning("读取 SQLite 训练历史失败，回退日报: %s", exc)

    entries = []
    for i in range(days):
        current = date.today() - timedelta(days=i)
        mem = memory_store.get(str(current))
        if not mem:
            continue
        fm = mem.front_matter
        activities = fm.get("yesterday_activities", {})
        sleep = fm.get("last_night_sleep", {})
        morning = fm.get("this_morning", {})
        recovery = fm.get("recovery", {})
        entries.append({
            "date": str(current),
            "activity_state": (
                activities.get("activity_state")
                or (
                    ActivityDayState.CONFIRMED_REST.value
                    if activities.get("is_rest_day")
                    else ActivityDayState.UNKNOWN.value
                )
            ),
            "is_rest": activities.get("is_rest_day", False),
            "duration": activities.get("total_duration_min", 0) or 0,
            "distance": activities.get("total_distance_km", 0) or 0,
            "load": activities.get("total_training_load", 0) or 0,
            "sleep_h": sleep.get("total_hours", 0) or 0,
            "sleep_score": sleep.get("sleep_score"),
            "hrv": morning.get("hrv_ms"),
            "rhr": morning.get("resting_hr"),
            "bb": morning.get("body_battery_morning"),
            "recovery": recovery.get("overall_score"),
            "readiness": morning.get("training_readiness_score"),
        })
    return entries


def _exec_get_training_history(memory_store: Any, days: int) -> str:
    """执行 get_training_history 工具 — N 天训练与恢复汇总。"""
    days = max(1, min(days, 90))
    entries = _get_history_entries(memory_store, days)

    if not entries:
        return "暂无历史数据"

    n = len(entries)
    train_entries = [
        e for e in entries
        if _history_activity_state(e) == ActivityDayState.TRAINING
    ]
    tc = len(train_entries)
    total_km = sum(e["distance"] for e in train_entries)
    total_dur = sum(e["duration"] for e in train_entries)
    total_load = sum(e["load"] for e in train_entries)

    sleep_s = [e["sleep_score"] for e in entries if e["sleep_score"]]
    rec_s = [e["recovery"] for e in entries if e["recovery"]]
    hrv_s = [e["hrv"] for e in entries if e["hrv"]]
    rhr_s = [e["rhr"] for e in entries if e["rhr"]]

    chronological = list(reversed(entries))
    mid = max(n // 2, 1)
    first, second = chronological[:mid], chronological[mid:]
    f_km = sum(e["distance"] for e in first if not e["is_rest"])
    s_km = sum(e["distance"] for e in second if not e["is_rest"])
    km_trend = "上升" if s_km > f_km * 1.1 else ("下降" if s_km < f_km * 0.9 else "持平")
    f_hrv = [e["hrv"] for e in first if e["hrv"]]
    s_hrv = [e["hrv"] for e in second if e["hrv"]]
    fa_hrv = sum(f_hrv) / max(len(f_hrv), 1)
    sa_hrv = sum(s_hrv) / max(len(s_hrv), 1)
    hrv_trend = "上升" if sa_hrv > fa_hrv + 2 else ("下降" if sa_hrv < fa_hrv - 2 else "持平")

    lines = [f"## 近 {n} 天训练与恢复汇总"]
    lines.append(f"- 训练 {tc} 天，总跑量 {total_km:.1f}km，总时长 {total_dur}min，总负荷 {total_load}")
    lines.append(f"- 周均跑量 {total_km/max(n/7,1):.1f}km")
    lines.append(f"- 平均睡眠评分 {sum(sleep_s)/max(len(sleep_s),1):.0f} | 平均恢复评分 {sum(rec_s)/max(len(rec_s),1):.0f}")
    lines.append(f"- 平均 HRV {sum(hrv_s)/max(len(hrv_s),1):.0f}ms | 平均 RHR {sum(rhr_s)/max(len(rhr_s),1):.0f}bpm")
    lines.append(f"- 跑量趋势（前后半段对比）: {km_trend} | HRV 趋势: {hrv_trend}")

    # 近 10 天明细
    recent = entries[:min(10, n)]
    lines.append("\n### 每日明细")
    header = "| 日期 | 训练 | 睡眠/h | HRV | RHR | 电量 | 恢复 |"
    lines.append(header)
    lines.append("|" + "-" * (len(header) - 2) + "|")
    for e in recent:
        type_text = "、".join(e.get("training_types", []))
        state = _history_activity_state(e)
        if state == ActivityDayState.CONFIRMED_REST:
            t = "已确认休息"
        elif state == ActivityDayState.UNKNOWN:
            t = "运动数据未同步"
        else:
            t = (
                f"{type_text + ' ' if type_text else ''}"
                f"{e['duration']}min {e['distance']:.1f}km L{e['load']:.0f}"
            )
        lines.append(
            f"| {e['date']} | {t} | {e['sleep_h']:.1f}/{e['sleep_score'] or '—'} | "
            f"{e['hrv'] or '—'} | {e['rhr'] or '—'} | {e['bb'] or '—'} | {e['recovery'] or '—'} |"
        )

    return "\n".join(lines)


def _history_activity_state(entry: dict[str, Any]) -> ActivityDayState:
    """兼容旧日报条目并返回统一的每日运动状态。"""

    raw = entry.get("activity_state")
    try:
        return ActivityDayState(raw)
    except (TypeError, ValueError):
        if entry.get("is_rest"):
            return ActivityDayState.CONFIRMED_REST
        if any((
            entry.get("duration"), entry.get("distance"), entry.get("load"),
            entry.get("training_types"),
        )):
            return ActivityDayState.TRAINING
        return ActivityDayState.UNKNOWN


def _exec_get_current_week_progress(
    memory_store: Any,
    target_date: date | None = None,
) -> str:
    """按自然周统计截至目标日期的实际训练进度。"""

    target_date = target_date or date.today()
    monday, sunday = natural_week_bounds(target_date)
    elapsed_days = (target_date - monday).days + 1
    entries = memory_store.get_training_history_entries(
        days=elapsed_days, end_date=target_date,
    )
    training = [
        entry for entry in entries
        if _history_activity_state(entry) == ActivityDayState.TRAINING
    ]
    unknown_count = sum(
        _history_activity_state(entry) == ActivityDayState.UNKNOWN
        for entry in entries
    )
    total_km = sum(float(entry.get("distance", 0) or 0) for entry in training)
    total_duration = sum(float(entry.get("duration", 0) or 0) for entry in training)
    total_load = sum(float(entry.get("load", 0) or 0) for entry in training)

    lines = [f"## 本周进度（自然周 {monday} 至 {sunday}）"]
    lines.append(
        f"- 截至 {target_date}：已完成 {len(training)} 次，"
        f"{total_km:.1f}km，{total_duration:.0f}min，总负荷 {total_load:.0f}"
    )
    if unknown_count:
        lines.append(
            f"- {unknown_count} 天运动数据状态未知；不得将其视为休息日或已完成 0 训练"
        )
    return "\n".join(lines)


def _exec_get_recovery_pattern(memory_store: Any, days: int) -> str:
    """执行 get_recovery_pattern 工具 — 个体恢复模式。"""
    days = max(1, min(days, 90))
    train_days, rest_days = [], []
    for raw_entry in reversed(_get_history_entries(memory_store, days)):
        entry = dict(raw_entry)
        state = _history_activity_state(entry)
        if state == ActivityDayState.CONFIRMED_REST:
            rest_days.append(entry)
        elif state == ActivityDayState.TRAINING:
            train_days.append(entry)

    if not train_days:
        return "暂无训练数据用于恢复分析"

    lines = [f"## 个体恢复模式（近 {days} 天，{len(train_days)} 次训练）"]

    # 高强度训练后的恢复
    high_load = [t for t in train_days if t.get("load", 0) >= 100]
    if high_load:
        lines.append(f"\n### 高强度训练后恢复（{len(high_load)} 次 load≥100）")
        for hld in high_load[-5:]:
            hd = date.fromisoformat(hld["date"])
            recovery_days = []
            for offset in [1, 2, 3]:
                nd = hd + timedelta(days=offset)
                rd = next((r for r in rest_days if r["date"] == str(nd)), None)
                if rd:
                    recovery_days.append(
                        f"D+{offset}: HRV={rd['hrv']}ms RHR={rd['rhr']} "
                        f"BB={rd['bb']} 恢复={rd['recovery']}"
                    )
            lines.append(f"- {hld['date']} load={hld.get('load',0)}: " + " | ".join(recovery_days) if recovery_days else "无后续恢复数据")

    # 趋势总结
    if rest_days and train_days:
        recent_rest = rest_days[-min(7, len(rest_days)):]
        avg_hrv = sum(r["hrv"] for r in recent_rest if r["hrv"]) / max(sum(1 for r in recent_rest if r["hrv"]), 1)
        avg_rhr = sum(r["rhr"] for r in recent_rest if r["rhr"]) / max(sum(1 for r in recent_rest if r["rhr"]), 1)
        lines.append(f"\n### 近期恢复基线")
        lines.append(f"- 休息日平均 HRV: {avg_hrv:.0f}ms, RHR: {avg_rhr:.0f}bpm")
        # 训练日 vs 休息日对比
        recent_train = train_days[-min(7, len(train_days)):]
        t_hrv = [t["hrv"] for t in recent_train if t["hrv"]]
        if t_hrv:
            lines.append(f"- 训练次日 HRV 平均: {sum(t_hrv)/len(t_hrv):.0f}ms (vs 休息日 {avg_hrv:.0f}ms)")

    return "\n".join(lines)


def _exec_get_training_cycle(memory_store: Any) -> str:
    """执行 get_training_cycle 工具。"""
    try:
        goals = memory_store.list_by_type("goal", status="active")
        if not goals:
            return "暂无活跃训练目标，无法分析训练周期"
        today = date.today()
        lines = ["## 训练周期分析"]
        for g in goals[:3]:
            fm = g.front_matter or {}
            target_str = fm.get("target_date", "")
            if not target_str:
                continue
            try:
                goal_date = date.fromisoformat(str(target_str)[:10])
            except ValueError:
                continue
            weeks = max(0, (goal_date - today).days / 7)
            if weeks > 12:
                phase = "基础期 — 以有氧耐力积累为主"
            elif weeks > 8:
                phase = "强化期 — 加入节奏跑和专项耐力"
            elif weeks > 4:
                phase = "高峰期 — 比赛配速训练为核心"
            elif weeks > 1:
                phase = "赛前调整/减量期 — 降低跑量保持强度"
            else:
                phase = "比赛周 — 轻松跑+充分恢复"
            title = fm.get("title") or g.id
            m = fm.get("metrics", {})
            dist = next((k.replace("target_", "") for k in m if k.startswith("target_")), "?")
            lines.append(f"- {title}: {dist} 目标{m.get('target_'+dist, '—')}，距比赛 {weeks:.0f} 周 → **{phase}**")
        return "\n".join(lines)
    except Exception:
        return "无法获取训练周期"


def _exec_get_daily_report(memory_store: Any, date_str: str) -> str:
    """执行 get_daily_report 工具。"""
    mem = memory_store.get(date_str)
    if not mem:
        return f"未找到 {date_str} 的日报数据"
    fm = mem.front_matter
    ya = fm.get("yesterday_activities", {})
    sl = fm.get("last_night_sleep", {})
    mo = fm.get("this_morning", {})
    ld = fm.get("training_load", {})
    rc = fm.get("recovery", {})

    lines = [f"## {date_str} 日报"]
    if ya.get("activity_state") == ActivityDayState.UNKNOWN:
        lines.append("训练: 运动数据未同步，无法判断当天是否训练或休息")
    elif ya.get("is_rest_day"):
        lines.append(f"训练: 休息日（步数 {ya.get('daily_steps', 0)}，活动距离 {ya.get('daily_distance_km', 0):.1f}km）")
    else:
        lines.append(f"训练: {ya.get('total_duration_min', 0)}min {ya.get('total_distance_km', 0):.1f}km 负荷{ya.get('total_training_load', 0)}")
        for s in ya.get("sessions", []):
            dkm = f" {s.get('distance_km', 0):.1f}km" if s.get("distance_km") else ""
            lines.append(f"  - {s.get('type')} {s.get('name')}: {s.get('duration_min')}min{dkm} HR{s.get('avg_hr', '?')} load{s.get('training_load', 0)}")
    lines.append(f"睡眠: {sl.get('total_hours', 0)}h 评分{sl.get('sleep_score', '—')} 质量{sl.get('quality', '—')}")
    lines.append(f"晨起: RHR{mo.get('resting_hr', '—')} HRV{mo.get('hrv_ms', '—')}ms({mo.get('hrv_status', '—')}) 电量{mo.get('body_battery_morning', '—')} 准备{mo.get('training_readiness_score', '—')}")
    lines.append(f"负荷: ACWR{ld.get('acwr', '—')}({ld.get('acwr_status', '—')}) 恢复{rc.get('overall_score', '—')}/100({rc.get('level', '—')})")
    anomalies = fm.get("anomalies", {}).get("items", [])
    if anomalies:
        lines.append("异常: " + "；".join(a.get("message", "") for a in anomalies))
    return "\n".join(lines)


def _exec_get_today_data(memory_store: Any) -> str:
    """执行 get_today_data 工具。"""
    today = str(date.today())
    return _exec_get_daily_report(memory_store, today)


def _exec_get_training_plan(memory_store: Any) -> str:
    """执行 get_training_plan 工具 — 读取当前运动大纲。"""
    mem = memory_store.get("active-plan")
    if not mem:
        return "暂无运动大纲。请基于运动员目标、30天数据和恢复模式，调用 save_training_plan 生成一份科学的大纲。"
    lines = ["## 当前运动大纲\n"]
    if mem.front_matter:
        fm = mem.front_matter
        for k, label in [
            ("goal_id", "目标"), ("target_race", "目标比赛"), ("target_time", "目标成绩"),
            ("target_date", "目标日期"), ("weeks_to_race", "剩余周数"),
            ("current_phase", "当前阶段"), ("weekly_mileage_target", "周跑量目标(km)"),
            ("phase_start_date", "阶段开始"), ("phase_end_date", "阶段结束"),
            ("updated", "大纲更新日期"),
        ]:
            if fm.get(k):
                lines.append(f"- {label}: {fm[k]}")
        weekly = fm.get("weekly_structure", {})
        if weekly:
            lines.append("- 每周结构:")
            for day, workout in weekly.items():
                lines.append(f"  - {day}: {workout}")
        key_workouts = fm.get("key_workouts", [])
        if key_workouts:
            lines.append("- 关键课次:")
            for kw in key_workouts:
                if isinstance(kw, dict):
                    lines.append(f"  - {kw.get('type', '')}: {kw.get('description', '')}")
        adjustments = fm.get("adjustments", [])
        if adjustments:
            lines.append("- 近期调整:")
            for adj in adjustments[-3:]:
                lines.append(f"  - {adj}")
    if mem.body:
        lines.append("\n" + mem.body.strip()[:1500])
    return "\n".join(lines)


def _exec_save_training_plan(memory_store: Any, content: str) -> str:
    """执行 save_training_plan 工具 — 保存/更新运动大纲。"""
    from .memory import build_memory_file

    # 解析 content 中的结构化信息
    today_str = str(date.today())
    fm: dict[str, Any] = {
        "type": "training_plan",
        "status": "active",
        "updated": today_str,
    }

    # 从 content 中提取关键字段
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- 目标比赛:") or line.startswith("- 目标:"):
            fm["target_race"] = line.split(":", 1)[1].strip()
        elif line.startswith("- 目标成绩:"):
            fm["target_time"] = line.split(":", 1)[1].strip()
        elif line.startswith("- 目标日期:"):
            fm["target_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("- 剩余周数:"):
            try:
                fm["weeks_to_race"] = int(line.split(":", 1)[1].strip().replace("周", ""))
            except ValueError:
                fm["weeks_to_race"] = line.split(":", 1)[1].strip()
        elif line.startswith("- 当前阶段:"):
            fm["current_phase"] = line.split(":", 1)[1].strip()
        elif line.startswith("- 周跑量目标"):
            try:
                fm["weekly_mileage_target"] = float(line.split(":", 1)[1].strip().replace("km", ""))
            except ValueError:
                pass

    # 保存
    plan_path = None
    if hasattr(memory_store, '_reader') and hasattr(memory_store._reader, 'base_dir'):
        plan_path = Path(memory_store._reader.base_dir) / "plans" / "active-plan.md"
    if plan_path is None:
        return "错误：无法确定大纲存储路径"

    from .local_files import atomic_write_private

    atomic_write_private(plan_path, build_memory_file(fm, content))
    return "大纲已保存"


# Tool dispatcher
TOOL_EXECUTORS = {
    "get_athlete_profile": lambda ms, args: _exec_get_athlete_profile(ms),
    "get_training_goals": lambda ms, args: _exec_get_training_goals(ms),
    "get_training_history": lambda ms, args: _exec_get_training_history(ms, args.get("days", 30)),
    "get_current_week_progress": lambda ms, args: _exec_get_current_week_progress(
        ms, date.fromisoformat(args.get("date", str(date.today()))),
    ),
    "get_recovery_pattern": lambda ms, args: _exec_get_recovery_pattern(ms, args.get("days", 30)),
    "get_training_cycle": lambda ms, args: _exec_get_training_cycle(ms),
    "get_daily_report": lambda ms, args: _exec_get_daily_report(ms, args.get("date", str(date.today()))),
    "get_today_data": lambda ms, args: _exec_get_today_data(ms),
    "get_training_plan": lambda ms, args: _exec_get_training_plan(ms),
    "save_training_plan": lambda ms, args: _exec_save_training_plan(ms, args.get("content", "")),
}


# ═══════════════════════════════════════════════════════════════
# API Call
# ═══════════════════════════════════════════════════════════════


def get_coach_insight(
    fm: dict[str, Any],
    target_date: date | None = None,
    memory_store: Any = None,
) -> dict[str, Any] | None:
    """调用 DeepSeek API（Tool Use 模式）获取 AI 教练洞察。

    AI 自行决定需要调用哪些工具来收集数据，然后生成洞察。

    Args:
        fm: 日报 Front Matter（用于提供基础上下文，AI 也可通过工具获取更多）。
        target_date: 报告日期。
        memory_store: MemoryStore 实例（供工具查询）。

    Returns:
        {conclusion, observations, recommendations, warnings}
    """
    import httpx

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        logger.info("未设置 DEEPSEEK_API_KEY，跳过 AI 洞察")
        return None

    if target_date is None:
        target_date = date.today()

    system_prompt = load_coach_prompt()
    today_str = str(target_date)

    # 初始用户消息 — 给 AI 关键线索
    ya = fm.get("yesterday_activities", {})
    sl = fm.get("last_night_sleep", {})
    mo = fm.get("this_morning", {})
    ld = fm.get("training_load", {})

    if ya.get("activity_state") == ActivityDayState.UNKNOWN:
        train_hint = "运动数据未同步，训练/休息状态未知"
    elif ya.get("is_rest_day"):
        train_hint = "休息日"
    else:
        train_hint = f"{ya.get('total_duration_min', 0)}min {ya.get('total_distance_km', 0):.1f}km"
    user_msg = (
        f"请分析 {today_str} 的训练日报并给出教练洞察。\n\n"
        f"今日线索：{train_hint} | "
        f"睡眠 {sl.get('total_hours', '?')}h/{sl.get('sleep_score', '?')}分 | "
        f"HRV {mo.get('hrv_ms', '?')}ms RHR {mo.get('resting_hr', '?')} | "
        f"ACWR {ld.get('acwr', '?')}({ld.get('acwr_status', '?')})\n\n"
        f"请先用工具获取你需要的上下文数据，再用 JSON 格式输出分析结果。"
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        with httpx.Client(timeout=90.0) as client:
            # Tool-use loop: allow up to 5 rounds of tool calls
            for _round in range(5):
                resp = client.post(
                    DEEPSEEK_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": messages,
                        "tools": TOOLS,
                        "temperature": 0.7,
                        "max_tokens": 2000,
                    },
                )

                if resp.status_code != 200:
                    logger.error("DeepSeek API 返回 %d: %s", resp.status_code, resp.text[:300])
                    return None

                data = resp.json()
                choice = data["choices"][0]
                msg = choice["message"]

                # Check for tool calls
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    # Add assistant message with tool calls
                    messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

                    for tc in tool_calls:
                        func_name = tc["function"]["name"]
                        try:
                            func_args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            func_args = {}

                        executor = TOOL_EXECUTORS.get(func_name)
                        if executor and memory_store:
                            try:
                                result = executor(memory_store, func_args)
                            except Exception as exc:
                                result = f"工具执行错误: {exc}"
                        else:
                            result = f"未知工具: {func_name}"

                        logger.info("🤖 Tool: %s(%s) → %d chars", func_name, func_args, len(result))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                    continue  # Next round

                # No tool calls — final response
                content = msg.get("content", "")
                if not content:
                    return None

                try:
                    result = json.loads(content)
                except json.JSONDecodeError:
                    # Try to extract JSON from markdown block
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                        try:
                            result = json.loads(content)
                        except json.JSONDecodeError:
                            return None
                    else:
                        return None

                logger.info("✅ DeepSeek AI 洞察已生成 (tool-use, %d rounds)", _round + 1)

                return {
                    "observations": result.get("observations", []),
                    "recommendations": result.get("recommendations", []),
                    "warnings": result.get("warnings", []),
                    "conclusion": result.get("conclusion", ""),
                    "plan_execution": result.get("plan_execution", {}),
                    "plan_adjusted": result.get("plan_adjusted", False),
                    "confidence": "ai",
                    "model": DEEPSEEK_MODEL,
                }

            logger.warning("AI 教练达到 tool-use 最大轮次，未返回最终结果")
            return None

    except ImportError:
        logger.warning("httpx 不可用，跳过 AI 洞察")
        return None
    except Exception as exc:
        logger.error("AI 教练调用失败: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════
# Web Chat 流式对话
# ═══════════════════════════════════════════════════════════════


async def chat_stream(
    messages: list[dict[str, str]],
    context: str = "",
    api_key: str | None = None,
    model: str | None = None,
) -> "AsyncIterator[str]":
    """流式 AI 对话 — 用于 Web Chat 页面 SSE 推送。

    Args:
        messages: 对话历史 [{"role": "user"|"assistant", "content": "..."}]
        context: 用户上下文（日报、画像、目标等）
        api_key: DeepSeek API Key，默认从环境变量读取
        model: 模型名，默认 deepseek-chat

    Yields:
        每次 yield 一个 token 字符串。
    """
    import httpx

    key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        yield "错误：未配置 DEEPSEEK_API_KEY，无法使用 AI 教练。"
        return

    model_name = model or DEEPSEEK_MODEL

    system_prompt = (
        "你是专业的跑步教练 AI，名叫 neurun Coach。"
        "你会参考运动员的竞技档案（PB）、训练目标和近期训练数据，"
        "给出个性化、有深度的中文建议。"
        "回答简洁有力，用具体数据说话，不泛泛而谈。"
        "如果用户提到伤病或不适，优先建议安全恢复而非训练。"
    )

    if context:
        system_prompt += f"\n\n## 用户数据与上下文\n{context}"

    api_messages = [{"role": "system", "content": system_prompt}]
    # 保留最近 20 条消息（避免上下文过长）
    api_messages.extend(messages[-20:])

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                DEEPSEEK_BASE_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": api_messages,
                    "stream": True,
                    "temperature": 0.7,
                    "max_tokens": 2000,
                },
            ) as response:
                if response.status_code != 200:
                    yield f"错误：AI 服务返回 {response.status_code}"
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            return
                        try:
                            import json as _json
                            chunk = _json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except Exception:
                            continue

    except Exception as exc:
        logger.error("流式对话失败: %s", exc)
        yield f"错误：{exc}"
