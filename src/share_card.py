"""分享卡渲染器 — 日报/周复盘分享卡 HTML + PNG 导出。

生成适合分享到微信朋友圈、微信群、小红书等平台的训练卡片。
卡片不包含睡眠、HRV、恢复评分等隐私数据，只展示训练事实。
"""

from __future__ import annotations

import logging
import re as _re_mod
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SHARE_CSS = r"""
*{margin:0;padding:0;box-sizing:border-box}
html{font-size:16px}
body{width:375px;font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','PingFang SC','Microsoft YaHei',sans-serif;line-height:1.5;-webkit-font-smoothing:antialiased;color:var(--text)}

[data-theme="fresh"] {
  --bg:#e8efe9; --card-bg:#fff; --text:#1a2e23; --text-secondary:#5a7d6e; --text-muted:#8aa89a;
  --accent:#2d9d6f; --divider:#dde5df; --tag-bg:#f0f4f0;
  --performance:#22a86e; --warning:#d4a017;
}
[data-theme="sport"] {
  --bg:#f0f0f0; --card-bg:#fff; --text:#171717; --text-secondary:#525252; --text-muted:#a3a3a3;
  --accent:#f15b2a; --divider:#ebebeb; --tag-bg:#f5f5f5;
  --performance:#16a34a; --warning:#eab308;
}
[data-theme="dark"] {
  --bg:#141a14; --card-bg:#1e261e; --text:#e6e8e3; --text-secondary:#9aa89a; --text-muted:#5a6b5a;
  --accent:#f0a030; --divider:#2a342a; --tag-bg:#1a201a;
  --performance:#4ade80; --warning:#fbbf24;
}

.share-card{width:375px;padding:0;background:var(--card-bg)}
.share-card-inner{background:var(--card-bg);border-radius:20px;padding:24px 16px 22px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08)}
.share-brand{display:flex;align-items:center;gap:6px;margin-bottom:18px}
.share-brand-mark{display:grid;place-items:center;width:24px;height:24px;border-radius:7px;background:var(--accent);color:#fff;font-size:13px;font-weight:900;line-height:1}
.share-brand-text{font-size:14px;font-weight:800;letter-spacing:.3px;color:var(--text)}
.share-brand-desc{font-size:10px;color:var(--text-muted);margin-top:1px;letter-spacing:.5px}
.share-date{font-size:12px;color:var(--text-muted);margin-bottom:16px;letter-spacing:.2px}
.share-hero{text-align:center;padding:16px 0 12px;border-bottom:1px solid var(--divider);margin-bottom:12px}
.share-hero-type{display:inline-block;font-size:12px;font-weight:750;padding:3px 10px;border-radius:999px;background:var(--accent);color:#fff;margin-bottom:8px;letter-spacing:.5px}
.share-hero-dist{font-size:42px;font-weight:700;color:var(--text);line-height:1.1;letter-spacing:-1px}
.share-hero-dist .unit{font-size:15px;font-weight:500;color:var(--text-muted);margin-left:2px;letter-spacing:0}
.share-hero-meta{display:flex;justify-content:center;gap:18px;margin-top:8px}
.share-hero-meta-item{font-size:13px;color:var(--text-secondary)}
.share-hero-meta-item .val{font-size:18px;font-weight:700;color:var(--text)}
.share-hero-meta-item .unit{font-size:11px;font-weight:500;color:var(--text-muted);margin-left:1px}
.share-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.share-stats.cols2{grid-template-columns:repeat(2,1fr)}
.share-stat{text-align:center;padding:10px 4px;border-radius:12px;background:var(--tag-bg)}
.share-stat-val{font-size:18px;font-weight:700;color:var(--text);line-height:1.2}
.share-stat-val .unit{font-size:10px;font-weight:500;color:var(--text-muted);margin-left:1px}
.share-stat-lbl{font-size:10px;color:var(--text-muted);margin-top:2px;letter-spacing:.3px}
.share-section{margin-top:12px;padding-top:10px;border-top:1px solid var(--divider)}
.share-section-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-bottom:8px}
.share-intensity-bar{display:flex;height:6px;border-radius:3px;overflow:hidden;margin-bottom:6px;background:var(--tag-bg)}
.share-intensity-seg{height:100%}
.share-intensity-legend{display:flex;flex-wrap:wrap;gap:10px;font-size:10px;color:var(--text-muted)}
.share-intensity-legend span{display:flex;align-items:center;gap:3px}
.share-intensity-swatch{width:7px;height:7px;border-radius:2px;flex-shrink:0}
.share-quality-list{display:grid;gap:8px}
.share-quality-item{padding:10px 12px;border-radius:12px;background:var(--tag-bg)}
.share-quality-day{font-size:10px;font-weight:700;color:var(--text-muted);margin-bottom:3px;letter-spacing:.3px}
.share-quality-type{font-size:14px;font-weight:700;color:var(--text)}
.share-quality-meta{font-size:12px;color:var(--text-secondary);margin-top:2px}
.share-quality-meta .unit{font-size:10px;font-weight:500;color:var(--text-muted)}
.share-quality-effect{font-size:10px;color:var(--text-muted);margin-top:1px}
.share-session-list{display:grid;gap:8px}
.share-session-item{padding:10px 12px;border-radius:12px;background:var(--tag-bg);display:flex;align-items:center;gap:10px}
.share-session-type{font-size:12px;font-weight:700;color:var(--text);min-width:0;flex-shrink:0}
.share-session-data{display:flex;gap:10px;margin-left:auto;flex-shrink:0}
.share-session-data span{font-size:12px;font-weight:600;color:var(--text-secondary);white-space:nowrap}
.share-session-data .unit{font-size:10px;font-weight:500;color:var(--text-muted)}
.share-trend{font-size:13px;color:var(--text-secondary);text-align:center;padding:4px 0}
.share-trend .val{font-weight:700;color:var(--text)}
.share-watermark{text-align:center;margin-top:16px;padding-top:10px;font-size:10px;color:var(--text-muted);letter-spacing:.5px;opacity:.5}
.share-ai{text-align:left;margin-top:12px;padding:10px 12px 10px 14px;border-radius:8px;background:color-mix(in srgb,var(--accent) 5%,transparent);border-left:3px solid var(--accent)}
.share-ai-badge{display:inline-block;font-size:9px;font-weight:700;padding:2px 8px;border-radius:999px;background:var(--accent);color:#fff;margin-bottom:8px;letter-spacing:.5px}
.share-ai-text{font-size:12px;color:var(--text-secondary);line-height:1.6}
.share-ai-cat{font-size:12px;font-weight:700;color:var(--text);margin-bottom:2px}
"""

INTENSITY_COLORS = {
    "easy": "#4ade80", "moderate": "#fbbf24", "tempo": "#fb923c",
    "threshold": "#f87171", "interval": "#e879f9", "repetition": "#c084fc",
    "race": "#f87171", "recovery": "#94a3b8", "warmup": "#a3e635",
    "cooldown": "#94a3b8",
}

QUALITY_TYPE_LABELS: dict[str, str] = {
    "interval": "间歇", "tempo": "节奏", "threshold": "阈值",
    "fartlek": "变速", "repetition": "重复跑",
    "race_pace": "比赛配速", "marathon_pace": "马拉松配速", "mixed": "混合",
}

def _is_running_activity(session: dict[str, Any]) -> bool:
    """Check if a session_analysis entry represents a running activity."""
    name = str(session.get("activity_name") or "").lower()
    display = str(session.get("display_name") or "").lower()
    primary = str(session.get("primary_type") or "").lower()
    # Non-running keywords take priority — Garmin often labels non-running
    # activities with generic "跑步训练" display_name
    if any(token in name for token in ("骑行", "骑车", "bike", "cycling", "swim", "游泳")):
        return False
    if any(token in display for token in ("骑行", "骑车", "bike", "cycling", "swim", "游泳")):
        return False
    if primary in ("easy", "long", "tempo", "interval", "threshold", "fartlek", "recovery", "race", "aerobic"):
        return True
    if any(token in name for token in ("run", "跑", "越野", "trail")):
        return True
    if "跑" in display:
        return True
    return False


PRIMARY_TYPE_LABELS: dict[str, str] = {
    "easy": "轻松跑", "long": "长距离", "tempo": "节奏跑",
    "interval": "间歇跑", "threshold": "阈值跑", "fartlek": "变速跑",
    "recovery": "恢复跑", "race": "比赛", "aerobic": "有氧跑",
    "unknown": "跑步",
}


def _extract_paces_from_ai(fm: dict[str, Any]) -> list[float]:
    """Extract all paces (sec/km) from AI insight text."""
    ai = fm.get("ai_insight") or {}
    if not isinstance(ai, dict):
        return []
    obs = ai.get("observations") or []
    text = " ".join(str(o) for o in obs) + " " + str(ai.get("conclusion") or "")
    paces = []
    for pat in [
        r"平均配速\s*(\d+)['\u2019](\d+)\"",
        r"配速约\s*(\d+)['\u2019](\d+)\"",
        r"配速\s*(\d+)['\u2019](\d+)\"",
        r"平均配速\s*(\d+):(\d+)/km",
        r"配速约\s*(\d+):(\d+)/km",
        r"配速\s*(\d+):(\d+)/km",
    ]:
        for m in _re_mod.finditer(pat, text):
            p = int(m.group(1)) * 60 + int(m.group(2))
            if p not in paces:
                paces.append(p)
    return paces


def _extract_pace_from_ai(fm: dict[str, Any]) -> float | None:
    """Extract the first pace from AI insight."""
    paces = _extract_paces_from_ai(fm)
    return paces[0] if paces else None

def _parse_session_text(text: str) -> dict[str, Any] | None:
    """Parse a session_analyses text string into structured data."""
    if not isinstance(text, str):
        return None
    result: dict[str, Any] = {}
    m = _re_mod.search(r"###\s+(.+)", text)
    if m:
        result["name"] = m.group(1).strip()
    m = _re_mod.search(r"距离\s*([\d.]+)\s*km", text)
    if m:
        result["distance_km"] = float(m.group(1))
    m = _re_mod.search(r"时长\s*(\d+)\s*min", text)
    if m:
        result["duration_min"] = int(m.group(1))
    m = _re_mod.search(r"Cadence\s*(\d+)", text)
    if m:
        result["cadence"] = int(m.group(1))
    m = _re_mod.search(r"-\s*[\d.]+\s*km\s+(\d+):(\d+)/km", text)
    if m:
        mins, secs = int(m.group(1)), int(m.group(2))
        result["pace_sec_per_km"] = mins * 60 + secs
    m = _re_mod.search(r"TE\s+(\w+)", text)
    if m:
        result["te_label"] = m.group(1).strip()
    return result


def _pace_text(pace_sec_per_km) -> str:
    try:
        value = float(pace_sec_per_km)
    except (TypeError, ValueError):
        return "\u2014"
    if not 0 < value <= 3600:
        return "\u2014"
    minutes = int(value // 60)
    seconds = int(round(value % 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}'{seconds:02d}\""


def _duration_hours(seconds) -> str:
    try:
        secs = float(seconds)
    except (TypeError, ValueError):
        return "\u2014"
    if secs <= 0:
        return "\u2014"
    hours = int(secs // 3600)
    minutes = int((secs % 3600) // 60)
    if hours:
        return f"{hours}<span class=\"unit\">h</span>{minutes:02d}"
    return f"{minutes}<span class=\"unit\">min</span>"


def _duration_short(seconds) -> str:
    try:
        secs = float(seconds)
    except (TypeError, ValueError):
        return "\u2014"
    if secs <= 0:
        return "\u2014"
    hours = int(secs // 3600)
    minutes = int((secs % 3600) // 60)
    if hours:
        return f"{hours}h{minutes:02d}"
    return f"{minutes}min"


def _effect_text(effect: dict | None) -> str:
    if not effect:
        return ""
    te = effect.get("aerobic_training_effect")
    ate = effect.get("anaerobic_training_effect")
    parts = []
    if te is not None:
        parts.append(f"有氧 {float(te):.1f}")
    if ate is not None:
        parts.append(f"无氧 {float(ate):.1f}")
    return " \u00b7 ".join(parts) if parts else ""


def _get_intensity_segments(intensity: dict | None) -> list[dict]:
    if not intensity:
        return []
    distribution = intensity.get("distribution") or []
    if distribution:
        return distribution
    hr_zones = intensity.get("heart_rate_zones") or []
    result = []
    zone_color_map = {1: "recovery", 2: "easy", 3: "moderate", 4: "threshold", 5: "interval"}
    for z in hr_zones:
        pct = z.get("percent")
        if pct and float(pct) > 0:
            zone_num = z.get("zone")
            zone_key = zone_color_map.get(zone_num, "easy") if zone_num else "easy"
            result.append({
                "label": f"Z{zone_num if zone_num else ''}",
                "percent": float(pct),
                "color": INTENSITY_COLORS.get(zone_key, "#94a3b8"),
            })
    return result

def _intensity_html(intensity: dict | None) -> str:
    segments = _get_intensity_segments(intensity)
    if not segments:
        return ""
    bar = "".join(
        f'<div class="share-intensity-seg" style="width:{max(seg.get("percent", 0), 0.5):.1f}%;background:{seg.get("color", INTENSITY_COLORS.get(seg.get("label", "").lower(), "#94a3b8"))}"></div>'
        for seg in segments
    )
    legend = "".join(
        f'<span><span class="share-intensity-swatch" style="background:{seg.get("color", "#94a3b8")}"></span>{seg.get("label", "")} {seg.get("percent", 0):.0f}%</span>'
        for seg in segments if seg.get("percent", 0) >= 3
    )
    return f"""<div class="share-section">
  <div class="share-section-title">强度分布</div>
  <div class="share-intensity-bar">{bar}</div>
  <div class="share-intensity-legend">{legend}</div>
</div>"""


def render_daily_share_card(fm: dict[str, Any]) -> str | None:
    """从日报 front_matter 生成日报分享卡 HTML。"""
    target_str = fm.get("date", str(date.today()))
    try:
        target_date = date.fromisoformat(str(target_str)[:10])
    except ValueError:
        target_date = date.today()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekday_names[target_date.weekday()]

    analyses = fm.get("session_analyses") or []
    running_sessions = []
    for a in analyses:
        if not isinstance(a, dict):
            continue
        if not _is_running_activity(a):
            continue
        summary = a.get("session_summary") or {}
        volume = summary.get("volume") or {}
        if volume.get("distance_m"):
            running_sessions.append(a)

    use_fallback = not running_sessions
    if use_fallback:
        for a in analyses:
            if isinstance(a, str):
                parsed = _parse_session_text(a)
                if parsed and parsed.get("distance_km"):
                    running_sessions.append(parsed)
        if running_sessions:
            use_fallback = True
        else:
            ya = fm.get("yesterday_activities") or {}
            ya_sessions = ya.get("sessions") or []
            running_sessions = [
                s for s in ya_sessions
                if isinstance(s, dict) and s.get("type") == "running"
            ]
            use_fallback = bool(running_sessions)

    if not running_sessions:
        return None

    # Keep original order from data (chronological); reverse for newest-first
    if not use_fallback:
        running_sessions = list(reversed(running_sessions))

    # Hero: cumulative totals across all running sessions
    total_distance_km = 0.0
    total_duration_s = 0.0
    total_pace_weighted = 0.0
    total_pace_weight = 0.0
    cadence = None
    granularity = "L0"
    summary = {}
    intensity = {}

    for s in running_sessions:
        if use_fallback:
            sd = float(s.get("distance_km") or 0)
            st = float(s.get("duration_min") or 0) * 60
            sp = s.get("pace_sec_per_km")
            sc = s.get("cadence")
            if not sp and sd > 0 and st > 0:
                sp = round(st / sd)
        else:
            ss = s.get("session_summary") or {}
            sv = ss.get("volume") or {}
            sd = (sv.get("distance_m") or 0) / 1000
            st = sv.get("duration_s") or 0
            spp = ss.get("pace_profile") or {}
            sp = spp.get("avg_pace_sec_per_km")
            if not sp and sd > 0 and st > 0:
                sp = round(st / sd)
            sst = ss.get("structure") or {}
            sc = sst.get("avg_cadence")
            if not granularity or granularity == "L0":
                granularity = ss.get("granularity") or "L0"
            if not summary:
                summary = ss
                intensity = ss.get("intensity") or {}

        total_distance_km += sd
        total_duration_s += st
        if sp and sd > 0:
            total_pace_weighted += sp * sd
            total_pace_weight += sd
        if sc is not None:
            cadence = sc

    distance_km = total_distance_km
    duration_s = total_duration_s
    if total_pace_weight > 0:
        pace = round(total_pace_weighted / total_pace_weight)
    else:
        pace = _extract_pace_from_ai(fm)
    display_name = "跑步"

    pace_parts = f"<span class=\"val\">{_pace_text(pace)}</span><span class=\"unit\">/km</span>" if pace else "\u2014"
    duration_parts = _duration_hours(duration_s)

    stat_cols = 3 if cadence else 2
    stats_html = f"""<div class="share-stats{' cols2' if stat_cols == 2 else ''}">
  <div class="share-stat"><div class="share-stat-val">{duration_parts}</div><div class="share-stat-lbl">时长</div></div>
  <div class="share-stat"><div class="share-stat-val">{_pace_text(pace) if pace else '\u2014'}<span class="unit">/km</span></div><div class="share-stat-lbl">配速</div></div>
  {f'<div class="share-stat"><div class="share-stat-val">{cadence:.0f}<span class="unit">spm</span></div><div class="share-stat-lbl">步频</div></div>' if cadence else ''}
</div>"""

    intensity_html = ""
    if granularity in ("L1", "L2"):
        intensity_html = _intensity_html(intensity)

    # AI insight — 从 observations/warnings/recommendations 构建结构化卡片
    ai_insight = fm.get("ai_insight") or {}
    obs = ai_insight.get("observations") or []
    ai_warnings = ai_insight.get("warnings") or []
    ai_recs = ai_insight.get("recommendations") or []
    ai_conclusion = str(ai_insight.get("conclusion") or "").strip()
    ai_insight_html = ""
    if obs or ai_conclusion or ai_warnings:
        sections = []
        # 分类 observations：按前缀归组
        categories: dict[str, list[str]] = {}
        for o in obs:
            if not isinstance(o, str):
                continue
            o = o.strip()
            if o.startswith("运动概要："):
                categories.setdefault("概要", []).append(o.removeprefix("运动概要："))
            elif o.startswith("强度分布："):
                categories.setdefault("强度", []).append(o.removeprefix("强度分布："))
            elif o.startswith("跑步动力学："):
                categories.setdefault("动力学", []).append(o.removeprefix("跑步动力学："))
            elif o.startswith("跑步分析："):
                categories.setdefault("分析", []).append(o.removeprefix("跑步分析："))
            elif o.startswith("恢复分析："):
                categories.setdefault("恢复", []).append(o.removeprefix("恢复分析："))
            elif o.startswith("近 7 天"):
                categories.setdefault("负荷", []).append(o)
            elif o.startswith("HRV") or o.startswith("ACWR"):
                categories.setdefault("指标", []).append(o)
            elif o.startswith("恢复-表现"):
                categories.setdefault("指标", []).append(o)
            elif o.startswith("伤病风险"):
                categories.setdefault("指标", []).append(o)
            else:
                categories.setdefault("其他", []).append(o)

        # 运动概要
        for line in categories.get("概要", [])[:1]:
            if len(line) < 120:
                sections.append(f'<div class="share-ai-text" style="font-weight:600;color:var(--text)">{line}</div>')

        # 强度 + 动力学
        intensity_lines = categories.get("强度", [])[:1]
        kin_lines = categories.get("动力学", [])[:1]
        for line in intensity_lines + kin_lines:
            if len(line) < 120:
                sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px">• {line}</div>')

        # 跑步分析（有氧漂移/经济性/疲劳等）
        for line in categories.get("分析", [])[:1]:
            if len(line) < 120:
                sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px">• {line}</div>')

        # 恢复/负荷
        for line in categories.get("恢复", [])[:1]:
            if len(line) < 80:
                sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px">• {line}</div>')
        for line in categories.get("负荷", [])[:1]:
            if len(line) < 100:
                sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px">• {line}</div>')

        # 关键指标：HRV/ACWR/一致性/伤病风险
        for line in categories.get("指标", [])[:3]:
            if len(line) < 80:
                sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px">• {line}</div>')

        # 核心结论
        if ai_conclusion and len(ai_conclusion) < 120:
            sections.append(f'<div class="share-ai-text" style="font-style:italic;margin-top:6px;color:var(--accent)">“{ai_conclusion}”</div>')

        # 警告
        for w in ai_warnings[:1]:
            if isinstance(w, str) and len(w) < 80:
                sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px;color:var(--warning)">⚠ {w}</div>')

        # 建议
        for r in ai_recs[:1]:
            if isinstance(r, str) and len(r) < 80:
                sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px">→ {r}</div>')

        if sections:
            ai_insight_html = f'<div class="share-ai"><div class="share-ai-badge">AI 教练</div>{"".join(sections)}</div>'

    # "当日训练" section
    extra_sessions_html = ""
    if running_sessions:
        items = []
        for s in running_sessions:
            if use_fallback:
                s_dist = float(s.get("distance_km") or 0)
                s_dur = _duration_short(float(s.get("duration_min") or 0) * 60)
                s_pace_val = s.get("pace_sec_per_km")
                s_name = s.get("name") or ""
                s_ta = s.get("training_analysis") or {}
                s_type = s_ta.get("primary_type") or "unknown"
                s_dn = s_ta.get("display_name") or PRIMARY_TYPE_LABELS.get(s_type, "跑步")
                s_loc = s_name.removesuffix(" 跑步").removesuffix("跑步")
                s_label = f"{s_loc} {s_dn}" if s_loc else s_dn
            else:
                s_summary = s.get("session_summary") or {}
                s_vol = s_summary.get("volume") or {}
                s_dist = (s_vol.get("distance_m") or 0) / 1000
                s_dur = _duration_short(s_vol.get("duration_s") or 0)
                s_pace = s_summary.get("pace_profile") or {}
                s_pace_val = s_pace.get("avg_pace_sec_per_km")
                s_type = s.get("primary_type") or "unknown"
                s_dn = s.get("display_name") or PRIMARY_TYPE_LABELS.get(s_type, "跑步")
                s_loc = (s.get("activity_name") or "").removesuffix(" 跑步").removesuffix("跑步")
                s_label = f"{s_loc} {s_dn}" if s_loc else s_dn
            if not s_pace_val and s_dist > 0:
                # Compute pace from distance/duration when available
                if use_fallback:
                    s_pace_val = round(float(s.get("duration_min") or 0) * 60 / s_dist)
                else:
                    s_vol = (s.get("session_summary") or {}).get("volume") or {}
                    s_dur = s_vol.get("duration_s") or 0
                    if s_dur:
                        s_pace_val = round(s_dur / (s_dist * 1000))
            s_pace_str = f"{_pace_text(s_pace_val)}<span class=\"unit\">/km</span>" if s_pace_val else "\u2014"
            items.append(f"""<div class="share-session-item">
  <div class="share-session-type">{s_label}</div>
  <div class="share-session-data">
    <span>{s_dist:.1f}<span class="unit">km</span></span>
    <span>{s_dur}</span>
    <span>{s_pace_str}</span>
  </div>
</div>""")
        extra_sessions_html = f"""<div class="share-section">
  <div class="share-section-title">当日训练</div>
  <div class="share-session-list">{''.join(items)}</div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{SHARE_CSS}</style>
</head>
<body data-theme="sport">
<div class="share-card">
  <div class="share-card-inner">
    <div class="share-brand">
      <div class="share-brand-mark">N</div>
      <div>
        <div class="share-brand-text">neurun</div>
        <div class="share-brand-desc">你的 AI 跑步教练</div>
      </div>
    </div>
    <div class="share-date">{target_date} {wd}</div>
    <div class="share-hero">
      <div class="share-hero-type">{display_name}</div>
      <div class="share-hero-dist">{distance_km:.2f}<span class="unit">km</span></div>

    </div>
    {stats_html}
    {intensity_html}
    {extra_sessions_html}
    {ai_insight_html}
    <div class="share-watermark">\u2500\u2500 AI 教练 \u00b7 NeuRun \u2500\u2500</div>
  </div>
</div>
</body>
</html>"""

def render_weekly_share_card(weekly_review: dict[str, Any]) -> str | None:
    """从 review_week() 返回结果生成周复盘分享卡 HTML。"""
    actual = weekly_review.get("actual_summary") or {}
    trend = weekly_review.get("trend_summary") or {}
    quality_sessions = weekly_review.get("quality_sessions") or []

    running_km = float(actual.get("running_distance_km") or 0)
    if running_km <= 0:
        return None

    running_days = int(actual.get("running_days") or 0)
    active_days = int(actual.get("running_days") or 0)
    activity_count = int(actual.get("running_activity_count") or 0)
    duration_min = int(actual.get("running_duration_minutes") or 0)
    duration_h = duration_min / 60
    if duration_h >= 1:
        duration_str = f"{duration_h:.1f}<span class=\"unit\">h</span>"
    else:
        duration_str = f"{duration_min}<span class=\"unit\">min</span>"

    longest = actual.get("longest_running_activity") or {}
    longest_km = float(longest.get("distance_km") or 0)
    running_pace = actual.get("running_pace_sec_per_km")

    week_start = actual.get("week_start") or ""
    week_end = actual.get("week_end") or ""
    week_range = ""
    if week_start and week_end:
        try:
            ws = date.fromisoformat(str(week_start)[:10])
            we = date.fromisoformat(str(week_end)[:10])
            week_range = f"{ws.month}/{ws.day}\u2013{we.month}/{we.day}"
        except ValueError:
            week_range = ""

    review_sec = weekly_review.get("review_sections") or {}
    ai_insight_html = ""
    ai_sections = []

    # 1. 本周概览
    overview = review_sec.get("overview") or {}
    ov_headline = str(overview.get("headline") or "").strip()
    if ov_headline:
        ai_sections.append(f'<div class="share-ai-cat">\U0001f4ca 本周概览</div>')
        ai_sections.append(f'<div class="share-ai-text">{ov_headline}</div>')

    # 2. 质量课总结
    qs_review = review_sec.get("quality_sessions") or {}
    qs_list = weekly_review.get("quality_sessions") or []
    if qs_list:
        qs_items = qs_review.get("items") or qs_list
        qs_lines = []
        for qs in qs_items[:4]:
            if not isinstance(qs, dict):
                continue
            qs_date = qs.get("date") or ""
            try:
                qd = date.fromisoformat(str(qs_date)[:10])
                day_label = f"周{qd.strftime('%u')} {qd.month}/{qd.day}"
            except ValueError:
                day_label = qs_date
            qs_type = qs.get("quality_type") or ""
            qs_label = QUALITY_TYPE_LABELS.get(qs_type, qs_type)
            qs_dist = qs.get("distance_km")
            qs_dur = qs.get("duration_minutes")
            metrics = qs.get("metrics") or qs.get("facts") or {}
            qs_pace = metrics.get("average_pace") or {}
            qs_pace_val = qs_pace.get("value") if isinstance(qs_pace, dict) else qs_pace
            parts = []
            if qs_dist:
                parts.append(f"{float(qs_dist):.1f}km")
            if qs_dur:
                parts.append(_duration_short(float(qs_dur) * 60))
            if qs_pace_val:
                parts.append(f"{_pace_text(qs_pace_val)}/km")
            meta = "  ".join(parts)
            qs_lines.append(f'<div class="share-ai-text" style="font-size:11px">\u2022 <span style="font-weight:600;color:var(--text)">{day_label} {qs_label}</span>  {meta}</div>')
        if qs_lines:
            ai_sections.append(f'<div class="share-ai-cat" style="margin-top:8px">\u26a1 质量课总结</div>')
            ai_sections.append(f'<div class="share-ai-text" style="margin-bottom:2px">本周识别 {len(qs_list)} 节质量课：</div>')
            ai_sections.extend(qs_lines)
    elif running_km:
        ai_sections.append(f'<div class="share-ai-cat" style="margin-top:8px">\u26a1 质量课总结</div>')
        ai_sections.append(f'<div class="share-ai-text">本周以有氧跑为主，未检测到满足证据门槛的质量课。</div>')

    # 3. 近期变化
    trend_sec = review_sec.get("trend") or {}
    trend_headline = str(trend_sec.get("headline") or "").strip()
    if trend_headline:
        ai_sections.append(f'<div class="share-ai-cat" style="margin-top:8px">\U0001f4c8 近期变化</div>')
        ai_sections.append(f'<div class="share-ai-text">{trend_headline}</div>')

    # 4. 风险与建议
    risk_sec = review_sec.get("recovery_and_risk") or {}
    risk_headline = str(risk_sec.get("headline") or "").strip()
    next_week = review_sec.get("next_week") or {}
    actions = next_week.get("actions") or []
    has_risk_or_action = bool(risk_headline or actions)
    if has_risk_or_action:
        ai_sections.append(f'<div class="share-ai-cat" style="margin-top:8px">\u26a0\ufe0f 风险与建议</div>')
        if risk_headline:
            ai_sections.append(f'<div class="share-ai-text">{risk_headline}</div>')
        if actions and isinstance(actions, list):
            for act in actions[:3]:
                if isinstance(act, str) and len(act) < 120:
                    ai_sections.append(f'<div class="share-ai-text" style="font-size:11px;margin-top:3px">\u2192 {act}</div>')

    if ai_sections:
        ai_insight_html = f'<div class="share-ai"><div class="share-ai-badge">AI 教练</div>{"".join(ai_sections)}</div>'

    running_trend = trend.get("running_km") or {}
    reference_average = running_trend.get("reference_average")
    delta = running_trend.get("delta")
    delta_percent = running_trend.get("delta_percent")
    trend_html = ""
    if reference_average is not None and delta is not None:
        direction = "\u2191" if float(delta) > 0 else "\u2193" if float(delta) < 0 else "\u2192"
        pct = f" <span class=\"val\">{float(delta_percent):+.0f}%</span>" if delta_percent is not None else ""
        trend_html = f"""<div class="share-trend">
  较前 {int(trend.get('reference_week_count', 0))} 周均 {float(reference_average):.1f} km {direction}{pct}
</div>"""

    quality_html = ""
    if quality_sessions:
        items = []
        for qs in quality_sessions[:3]:
            if not isinstance(qs, dict):
                continue
            qdate = qs.get("date") or ""
            try:
                qd = date.fromisoformat(str(qdate)[:10])
                day_label = f"周{qd.strftime('%u')} \u00b7 {qd.month}/{qd.day}"
            except ValueError:
                day_label = qdate
            qtype = qs.get("quality_type") or ""
            qtype_label = QUALITY_TYPE_LABELS.get(qtype, qtype)
            dist_val = qs.get("distance_km")
            metrics = qs.get("metrics") or qs.get("facts") or {}
            pace = metrics.get("average_pace") or {}
            effect = metrics.get("training_effect") or {}
            pace_val = pace.get("value") if isinstance(pace, dict) else pace
            effect_val = effect.get("value") if isinstance(effect, dict) else effect
            dist_str = f"{float(dist_val):.1f}<span class=\"unit\">km</span>" if dist_val is not None else ""
            pace_str = f"<span class=\"unit\">@</span> {_pace_text(pace_val)}<span class=\"unit\">/km</span>" if pace_val else ""
            effect_str = _effect_text(effect_val) if isinstance(effect_val, dict) else ""
            items.append(f"""<div class="share-quality-item">
  <div class="share-quality-day">{day_label}</div>
  <div class="share-quality-type">{qtype_label}</div>
  <div class="share-quality-meta">{dist_str} {pace_str}</div>
  {f'<div class="share-quality-effect">{effect_str}</div>' if effect_str else ''}
</div>""")
        if items:
            quality_html = f"""<div class="share-section">
  <div class="share-section-title">\u26a1 本周质量课 <span style="font-weight:700;color:var(--text)">{len(quality_sessions)}</span> 节</div>
  <div class="share-quality-list">{''.join(items)}</div>
</div>"""
    elif running_km:
        quality_html = """<div class="share-section">
  <div class="share-section-title">\u26a1 本周质量课</div>
  <div style="text-align:center;padding:10px;font-size:12px;color:var(--text-muted)">本周以有氧跑为主，未检测到满足证据门槛的质量课</div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{SHARE_CSS}</style>
</head>
<body data-theme="sport">
<div class="share-card">
  <div class="share-card-inner">
    <div class="share-brand">
      <div class="share-brand-mark">N</div>
      <div>
        <div class="share-brand-text">neurun</div>
        <div class="share-brand-desc">你的 AI 跑步教练</div>
      </div>
    </div>
    <div class="share-date">本周训练{f' \u00b7 {week_range}' if week_range else ''}</div>
    <div class="share-hero">
      <div class="share-hero-type">跑步</div>
      <div class="share-hero-dist">{running_km:.1f}<span class="unit">km</span></div>

    </div>
    <div class="share-stats">
      <div class="share-stat"><div class="share-stat-val">{longest_km:.1f}<span class="unit">km</span></div><div class="share-stat-lbl">最长单次</div></div>
      <div class="share-stat"><div class="share-stat-val">{_pace_text(running_pace) if running_pace else '—'}<span class="unit">/km</span></div><div class="share-stat-lbl">平均配速</div></div>
      <div class="share-stat"><div class="share-stat-val">{active_days}</div><div class="share-stat-lbl">跑步天数</div></div>
    </div>
    {trend_html}
    {quality_html}
    {ai_insight_html}<div class="share-watermark">\u2500\u2500 AI 教练 \u00b7 NeuRun \u2500\u2500</div>
  </div>
</div>
</body>
</html>"""


def render_share_card_image(html: str, output_path: str, theme: str = "sport", height: int | None = None) -> str:
    """将分享卡 HTML 渲染为 PNG 图片。

    height 为 None 时按内容实际高度自适应裁切（日报/周报内容量不同，
    避免固定高度导致底部大片留白）。
    """
    html = html.replace('data-theme="sport"', f'data-theme="{theme}"')
    tmp = tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False)
    tmp.write(html)
    tmp.close()
    html_path = tmp.name
    try:
        from .image import render_image
        return render_image(html_path, output_path, theme=theme, width=375, scale=3, height=height)
    finally:
        Path(html_path).unlink(missing_ok=True)


def generate_daily_share_image(fm: dict[str, Any], output_path: str, theme: str = "sport") -> str | None:
    """生成日报分享卡 PNG（高度按内容自适应）。"""
    html = render_daily_share_card(fm)
    if html is None:
        return None
    return render_share_card_image(html, output_path, theme=theme)


def generate_weekly_share_image(weekly_review: dict[str, Any], output_path: str, theme: str = "sport") -> str | None:
    """生成周复盘分享卡 PNG（高度按内容自适应）。"""
    html = render_weekly_share_card(weekly_review)
    if html is None:
        return None
    return render_share_card_image(html, output_path, theme=theme)
