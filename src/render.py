"""日报 HTML 渲染器 — 三主题静态 HTML 日报 + 图片导出。

内置 清新版 / 运动版 / 暗黑版 切换，无需服务器。
支持通过 Chrome headless 截图导出为 PNG 图片。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .memory import Memory, parse_front_matter


def _md_to_html(md: str) -> str:
    """将 Markdown 正文转为 HTML 片段。"""
    lines = md.split("\n")
    html: list[str] = []
    in_list = False
    in_table = False

    for line in lines:
        if line.startswith("### "):
            if in_list: html.append("</ul>"); in_list = False
            html.append(f'<h3>{_inline(line[4:])}</h3>'); continue
        if line.startswith("## "):
            if in_list: html.append("</ul>"); in_list = False
            html.append(f'<h2>{_inline(line[3:])}</h2>'); continue
        if line.startswith("# "):
            if in_list: html.append("</ul>"); in_list = False
            html.append(f'<h1>{_inline(line[2:])}</h1>'); continue
        if line.strip() == "---":
            if in_list: html.append("</ul>"); in_list = False
            html.append("<hr>"); continue
        if line.startswith("> "):
            if in_list: html.append("</ul>"); in_list = False
            html.append(f'<blockquote>{_inline(line[2:])}</blockquote>'); continue
        if line.startswith("|"):
            if not in_table: in_table = True; html.append('<table>')
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if all(c.startswith("---") or c.startswith(":--") for c in cells): continue
            is_first = in_table and html and html[-1] == "<table>"
            tag = "th" if is_first else "td"
            html.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>"); continue
        elif in_table:
            html.append("</table>"); in_table = False
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            if not in_list: html.append('<ul>'); in_list = True
            html.append(f"<li>{_inline(line.strip()[2:])}</li>"); continue
        elif in_list and line.strip():
            html[-1] = html[-1][:-5] + " " + _inline(line.strip()) + "</li>"; continue
        elif in_list and not line.strip():
            html.append("</ul>"); in_list = False; continue
        if line.strip().startswith("**") and line.strip().endswith("**"):
            html.append(f'<p class="bold-line">{_inline(line.strip()[2:-2])}</p>'); continue
        if not line.strip(): continue
        html.append(f"<p>{_inline(line)}</p>")
    if in_list: html.append("</ul>")
    if in_table: html.append("</table>")
    return "\n".join(html)


def _inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _sparkline(values: list[float], color: str) -> str:
    if len(values) < 2: return ""
    vmin, vmax = min(values), max(values)
    if vmax == vmin: vmax = vmin + 1
    w, h, pad = 120, 28, 2
    points = []
    for i, v in enumerate(values):
        x = pad + (w - 2 * pad) * i / (len(values) - 1)
        y = h - pad - (h - 2 * pad) * (v - vmin) / (vmax - vmin)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    return f'<svg width="{w}" height="{h}" class="sparkline"><polyline fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" points="{polyline}"/><circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" r="2.5" fill="{color}"/></svg>'


# ═══════════════════════════════════════════════════════════════
# HTML Builders
# ═══════════════════════════════════════════════════════════════

def _hero(fm: dict) -> str:
    sleep = fm.get("last_night_sleep", {})
    rec = fm.get("recovery", {})
    morning = fm.get("this_morning", {})
    return f"""<div class="hero-grid">
  <div class="hero-card"><div class="hero-value sleep">{sleep.get('total_hours','—')}<span>h</span></div><div class="hero-label">睡眠</div><div class="hero-sub">{sleep.get('quality','—')}</div></div>
  <div class="hero-card"><div class="hero-value recovery">{rec.get('overall_score','—')}<span>/100</span></div><div class="hero-label">恢复评分</div><div class="hero-sub">{rec.get('level','—')}</div></div>
  <div class="hero-card"><div class="hero-value readiness">{morning.get('training_readiness_score','—')}</div><div class="hero-label">训练准备</div><div class="hero-sub">{morning.get('training_readiness_level','—') or '—'}</div></div>
</div>"""


def _metric_strip(fm: dict) -> str:
    m = fm.get("this_morning", {})
    return f"""<div class="metric-strip">
  <div class="metric-item"><div class="metric-val hrv">{m.get('hrv_ms','—')}</div><div class="metric-lbl">HRV ms</div></div>
  <div class="metric-item"><div class="metric-val rhr">{m.get('resting_hr','—')}</div><div class="metric-lbl">静息心率</div></div>
  <div class="metric-item"><div class="metric-val bb">{m.get('body_battery_morning','—')}</div><div class="metric-lbl">身体电量</div></div>
  <div class="metric-item"><div class="metric-val stress">{m.get('avg_stress','—')}</div><div class="metric-lbl">压力</div></div>
</div>"""


def _sessions(fm: dict) -> str:
    ya = fm.get("yesterday_activities", {})
    if ya.get("is_rest_day"):
        steps = ya.get("daily_steps", 0)
        dist = ya.get("daily_distance_km", 0)
        detail = f"{steps} 步 · {dist:.1f} km" if steps > 5000 else ""
        return f'<div class="rest-card"><div class="rest-emoji">🧘</div><h3>休息日</h3><p class="rest-detail">{detail or "身体在恢复中"}</p></div>'
    sessions = ya.get("sessions", [])
    cards = []
    for s in sessions:
        icon = {'running':'🏃','cycling':'🚴','swimming':'🏊','strength':'🏋️'}.get(s.get('type','running'),'💪')
        cls = 'indoor' if '室内' in s.get('name','') else 'run'
        d = s.get('distance_km') or 0
        cards.append(f"""<div class="session-card"><div class="session-icon {cls}">{icon}</div>
  <div class="session-info"><div class="session-name">{s.get('name','')}</div><div class="session-type">{s.get('type','')}</div></div>
  <div class="session-metrics"><div class="session-metric"><div class="val">{s.get('duration_min',0)}</div><div class="lbl">min</div></div>
  <div class="session-metric"><div class="val">{d:.1f}</div><div class="lbl">km</div></div>
  <div class="session-metric"><div class="val">{s.get('avg_hr','—')}</div><div class="lbl">bpm</div></div>
  <div class="session-metric"><div class="val">{s.get('training_load',0)}</div><div class="lbl">load</div></div></div></div>""")
    return f"""<div class="activity-summary-strip"><span>🏃 {ya.get('total_duration_min',0)} min</span><span>📏 {ya.get('total_distance_km',0):.1f} km</span><span>⚡ 负荷 {ya.get('total_training_load',0)}</span></div>
<div class="session-cards">{''.join(cards)}</div>"""


def _load(fm: dict) -> str:
    l = fm.get("training_load", {})
    r = fm.get("recovery", {})
    acwr = l.get('acwr', 0)
    st = l.get('acwr_status', 'optimal')
    colors = {'optimal':'var(--performance)','undertraining':'var(--warning)','overreaching':'var(--accent)','high_risk':'var(--danger)'}
    color = colors.get(st, colors['optimal'])
    pct = min(acwr / 2.0 * 100, 100) if acwr else 50
    zl, zr = 0.8/2*100, 1.3/2*100
    return f"""<div class="load-card">
  <div class="load-header"><h3>训练负荷</h3><div class="load-score {st}" style="color:{color}">{acwr}</div></div>
  <div class="load-bar-wrap"><div class="load-bar"><div class="load-zone-optimal" style="left:{zl}%;width:{zr-zl}%"></div><div class="load-indicator" style="left:{pct}%;background:{color}"></div></div><div class="load-bar-labels"><span>0</span><span>0.8</span><span>1.3</span><span>1.5</span><span>2.0</span></div></div>
  <div class="load-stats"><div class="load-stat"><div class="val">{l.get('acute_load_7d',0)}</div><div class="lbl">急性 7d</div></div><div class="load-stat"><div class="val">{l.get('chronic_load_28d',0):.0f}</div><div class="lbl">慢性 28d</div></div><div class="load-stat"><div class="val" style="color:{color}">{r.get('overall_score','—')}</div><div class="lbl">恢复 /100</div></div></div>
</div>"""


def _rec(fm: dict) -> str:
    r = fm.get("recommendation", {})
    ready = r.get('ready_to_train', False)
    intensity = r.get('intensity', 'moderate')
    advice = r.get('training_advice', '')
    colors = {'rest':'var(--text-muted)','easy':'var(--performance)','moderate':'var(--accent)','hard':'var(--danger)'}
    color = colors.get(intensity, 'var(--accent)')
    cautions = r.get('caution', [])
    ch = '<div class="cautions"><h4>⚠️ 注意</h4><ul>' + ''.join(f'<li>{c}</li>' for c in cautions) + '</ul></div>' if cautions else ''
    return f"""<div class="rec-card" style="border-top:3px solid {color}">
  <div class="rec-header"><span class="rec-ready" style="color:{color}">{'✅ 可以训练' if ready else '⚠️ 建议休息'}</span><span class="rec-intensity {intensity}">{intensity}</span></div>
  <p class="rec-advice">{advice}</p>{ch}</div>"""


def _trends(fm: dict) -> str:
    trends = fm.get("trends_7d", {})
    if not trends: return ""
    labels = {'hrv':'HRV','resting_heart_rate':'静息心率','sleep_score':'睡眠评分','sleep_duration':'睡眠时长'}
    rows = []
    for k, label in labels.items():
        d = trends.get(k, {})
        if not d: continue
        vals = d.get('values', [])
        direction = d.get('direction', 'stable')
        emoji = {'improving':'↗','declining':'↘','stable':'→'}.get(direction, '→')
        color = 'var(--performance)' if direction == 'improving' else ('var(--danger)' if direction == 'declining' else 'var(--text-muted)')
        spark = _sparkline(vals, color) if vals and len(vals) >= 2 else ''
        rows.append(f'<div class="trend-row"><div class="trend-label">{label}</div><div class="trend-spark">{spark}</div><div class="trend-dir" style="color:{color}">{emoji} {direction}</div></div>')
    return f'<div class="trends-section"><h3>📈 7日趋势</h3>{"".join(rows)}</div>' if rows else ""


def _ai_section(fm: dict) -> str:
    ai = fm.get("ai_insight", {})
    if not ai: return ""
    conclusion = ai.get('conclusion', '')
    observations = ai.get('observations', [])
    warnings = ai.get('warnings', [])
    recs = ai.get('recommendations', [])
    conf = ai.get('confidence', 'medium')
    emoji = {'high':'🎯','medium':'💡','low':'🔍','ai':'🤖'}.get(conf, '💡')
    model = f'<span style="font-size:10px;color:var(--text-muted);margin-left:8px;font-weight:400">{ai.get("model","")}</span>' if ai.get('model') else ''
    parts = []
    if conclusion: parts.append(f'<div class="ai-conclusion">{emoji} {conclusion}{model}</div>')
    if observations: parts.append(f'<div class="ai-block obs"><h4>观察</h4><ul>{"".join(f"<li>{o}</li>" for o in observations)}</ul></div>')
    if warnings: parts.append(f'<div class="ai-block warn"><h4>⚠️ 注意</h4><ul>{"".join(f"<li>{w}</li>" for w in warnings)}</ul></div>')
    if recs: parts.append(f'<div class="ai-block rec"><h4>建议</h4><ul>{"".join(f"<li>{r}</li>" for r in recs)}</ul></div>')
    return f'<div class="ai-section"><h3>🤖 AI 教练洞察</h3>{"".join(parts)}</div>'


def _detail(fm: dict) -> str:
    analyses = fm.get("session_analyses", [])
    if not analyses: return ""
    parts = []
    for a in analyses:
        lines = a.strip().split("\n")
        if len(lines) < 2: continue
        title = lines[0].replace("### ", "")
        meta = lines[1]
        parts.append(f'<div class="detail-section"><h3>{title}</h3><div class="detail-meta">{meta}</div>')
        splits = [l for l in lines[2:] if l.startswith("- ")]
        if splits:
            parts.append('<table class="split-table"><tr><th>分段</th><th>心率</th><th>步频</th></tr>')
            for sl in splits:
                cols = [c.strip() for c in sl[2:].split("|")]
                parts.append(f'<tr>{"".join(f"<td>{c}</td>" for c in cols)}</tr>')
            parts.append('</table>')
        parts.append('</div>')
    return "\n".join(parts)


def _anomalies(fm: dict) -> str:
    a = fm.get("anomalies", {})
    items = a.get("items", [])
    if not items: return ""
    return '<div class="anomalies-section"><h3>⚠️ 异常提醒 · ' + a.get('level','warning') + '</h3>' + ''.join(f'<div class="anomaly-item">{i.get("message","")}</div>' for i in items) + '</div>'


# ═══════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════

CSS = r"""
/* ═══════════════════════════════════════════
   Rundown Theme System
   data-theme: fresh | sport | dark
   ═══════════════════════════════════════════ */

[data-theme="fresh"] {
  --bg:#e8efe9; --bg-card:#fff; --text:#1a2e23; --text-secondary:#5a7d6e; --text-muted:#8aa89a;
  --accent:#2d9d6f; --accent-glow:rgba(45,157,111,.1); --recovery:#3b82b6; --recovery-glow:rgba(59,130,246,.08);
  --performance:#22a86e; --performance-glow:rgba(34,168,110,.08); --sleep:#7c6fcf; --sleep-glow:rgba(124,111,207,.08);
  --warning:#d4a017; --danger:#dc5b51; --hrv:#3b9fc6;
  --card-shadow:0 2px 8px rgba(0,0,0,.06); --card-shadow-hover:0 8px 30px rgba(0,0,0,.1);
  --border-subtle:#dde5df; --bg-subtle:#f0f4f0;
}

[data-theme="sport"] {
  --bg:#f0f0f0; --bg-card:#fff; --text:#171717; --text-secondary:#525252; --text-muted:#a3a3a3;
  --accent:#f15b2a; --accent-glow:rgba(241,91,42,.08); --recovery:#2563eb; --recovery-glow:rgba(37,99,235,.08);
  --performance:#16a34a; --performance-glow:rgba(22,163,74,.08); --sleep:#7c3aed; --sleep-glow:rgba(124,58,237,.08);
  --warning:#eab308; --danger:#ef4444; --hrv:#0891b2;
  --card-shadow:0 2px 10px rgba(0,0,0,.07); --card-shadow-hover:0 10px 35px rgba(0,0,0,.1);
  --border-subtle:#ebebeb; --bg-subtle:#f5f5f5;
}

[data-theme="dark"] {
  --bg:#0b1120; --bg-card:#1a2333; --text:#e2e8f0; --text-secondary:#94a3b8; --text-muted:#64748b;
  --accent:#34d399; --accent-glow:rgba(52,211,153,.12); --recovery:#60a5fa; --recovery-glow:rgba(96,165,250,.1);
  --performance:#4ade80; --performance-glow:rgba(74,222,128,.1); --sleep:#a78bfa; --sleep-glow:rgba(167,139,250,.1);
  --warning:#fbbf24; --danger:#f87171; --hrv:#22d3ee;
  --card-shadow:0 2px 10px rgba(0,0,0,.35); --card-shadow-hover:0 10px 30px rgba(0,0,0,.5);
  --border-subtle:#243044; --bg-subtle:#151e2c;
}

*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Roboto,sans-serif;line-height:1.6;-webkit-font-smoothing:antialiased;transition:background .3s,color .3s}
.container{max-width:720px;margin:0 auto;padding:24px 20px 80px}

/* Theme Switcher */
.theme-bar{display:flex;justify-content:flex-end;gap:6px;padding:12px 0 0;margin-bottom:8px}
.theme-btn{border:none;padding:6px 14px;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer;background:var(--border-subtle);color:var(--text-muted);transition:all .2s;letter-spacing:.5px}
.theme-btn:hover{opacity:.8}
.theme-btn.active{background:var(--accent);color:#fff}

/* Header */
.header{padding:20px 0 36px;display:flex;justify-content:space-between;align-items:flex-start}
.header-brand{font-size:13px;font-weight:800;letter-spacing:1.5px;background:linear-gradient(135deg,var(--accent),#ef4444);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;text-transform:uppercase}
.header-date{font-family:-apple-system,'SF Pro Display','Helvetica Neue',sans-serif;font-size:42px;font-weight:800;color:var(--text);letter-spacing:-1.5px;line-height:1.1}
.header-date span{font-weight:500;font-size:18px;color:var(--text-muted);margin-left:12px}
.header-gen{font-size:11px;color:var(--text-muted);text-align:right;padding-top:8px}

/* Hero Grid */
.hero-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:24px}
.hero-card{background:var(--bg-card);border-radius:16px;padding:24px 18px;box-shadow:var(--card-shadow);text-align:center;transition:transform .15s,box-shadow .15s}
.hero-card:hover{transform:translateY(-2px);box-shadow:var(--card-shadow-hover)}
.hero-value{font-family:-apple-system,'SF Pro Display','Helvetica Neue',sans-serif;font-size:38px;font-weight:800;letter-spacing:-1px;line-height:1;margin-bottom:4px}
.hero-value span{font-size:16px;font-weight:500;opacity:.5;margin-left:4px}
.hero-value.sleep{color:var(--sleep)}.hero-value.recovery{color:var(--recovery)}.hero-value.readiness{color:var(--performance)}
.hero-label{font-size:13px;color:var(--text-secondary);font-weight:600}
.hero-sub{font-size:11px;color:var(--text-muted);margin-top:2px;text-transform:capitalize}

/* Metric Strip */
.metric-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:32px}
.metric-item{background:var(--bg-card);border-radius:12px;padding:18px 12px;text-align:center;box-shadow:var(--card-shadow)}
.metric-val{font-family:-apple-system,'SF Pro Display','Helvetica Neue',sans-serif;font-size:26px;font-weight:700;letter-spacing:-.5px;line-height:1}
.metric-val.hrv{color:var(--hrv)}.metric-val.rhr{color:var(--performance)}.metric-val.bb{color:var(--accent)}.metric-val.stress{color:var(--sleep)}
.metric-lbl{font-size:10px;color:var(--text-muted);margin-top:4px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}

/* Section Titles */
.section-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--text-muted);margin:28px 0 12px}

/* Session Cards */
.session-cards{display:flex;flex-direction:column;gap:10px;margin-bottom:8px}
.session-card{background:var(--bg-card);border-radius:16px;padding:18px 20px;box-shadow:var(--card-shadow);display:flex;align-items:center;gap:16px;transition:box-shadow .15s}
.session-card:hover{box-shadow:var(--card-shadow-hover)}
.session-icon{width:44px;height:44px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
.session-icon.run{background:#fef2f2}.session-icon.indoor{background:#eff6ff}.session-icon.cycle{background:#f0fdf4}
.session-info{flex:1;min-width:0}
.session-name{font-weight:600;font-size:15px;color:var(--text)}
.session-type{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px}
.session-metrics{display:flex;gap:20px;flex-shrink:0}
.session-metric{text-align:center}
.session-metric .val{font-family:-apple-system,'SF Pro Display','Helvetica Neue',sans-serif;font-size:18px;font-weight:700;color:var(--text)}
.session-metric .lbl{font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px}
.activity-summary-strip{display:flex;gap:12px;margin-bottom:14px;font-size:13px;color:var(--text-secondary)}
.activity-summary-strip span{background:var(--bg-card);padding:6px 14px;border-radius:20px;box-shadow:var(--card-shadow);font-weight:600}

/* Rest Day */
.rest-card{background:var(--bg-card);border-radius:16px;padding:36px;text-align:center;box-shadow:var(--card-shadow);margin-bottom:8px}
.rest-card .rest-emoji{font-size:48px;margin-bottom:8px}
.rest-card h3{font-size:18px;color:var(--text);margin-bottom:4px}
.rest-card .rest-detail{font-size:13px;color:var(--text-secondary)}

/* Load Card */
.load-card{background:var(--bg-card);border-radius:16px;padding:28px;box-shadow:var(--card-shadow);margin-bottom:8px}
.load-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:18px}
.load-header h3{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--text-muted)}
.load-score{font-family:-apple-system,'SF Pro Display','Helvetica Neue',sans-serif;font-size:44px;font-weight:800;letter-spacing:-1px}
.load-bar-wrap{margin-bottom:8px}
.load-bar{position:relative;height:8px;background:var(--border-subtle);border-radius:4px;overflow:visible}
.load-zone-optimal{position:absolute;top:0;height:100%;background:var(--performance-glow);border-radius:4px}
.load-indicator{position:absolute;top:-6px;width:20px;height:20px;border-radius:50%;border:3px solid #fff;box-shadow:0 1px 8px rgba(0,0,0,.2);margin-left:-10px;transition:left .6s cubic-bezier(.4,0,.2,1)}
.load-bar-labels{display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);font-weight:500;margin-top:6px}
.load-stats{display:flex;gap:24px;justify-content:center;margin-top:18px}
.load-stat{text-align:center}
.load-stat .val{font-family:-apple-system,'SF Pro Display','Helvetica Neue',sans-serif;font-size:20px;font-weight:700;color:var(--text)}
.load-stat .lbl{font-size:11px;color:var(--text-muted)}

/* Trends */
.trends-section{background:var(--bg-card);border-radius:16px;padding:22px;box-shadow:var(--card-shadow);margin-bottom:8px}
.trends-section h3{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--text-muted);margin-bottom:14px}
.trend-row{display:flex;align-items:center;gap:12px;padding:7px 0;border-bottom:1px solid var(--border-subtle)}
.trend-row:last-child{border-bottom:none}
.trend-label{flex:0 0 70px;font-size:12px;color:var(--text-secondary);font-weight:500}
.trend-spark{flex:1;display:flex;justify-content:center}
.trend-dir{flex:0 0 80px;font-size:12px;font-weight:600;text-align:right}

/* Recommendation */
.rec-card{background:var(--bg-card);border-radius:16px;padding:28px;box-shadow:var(--card-shadow);margin-bottom:8px}
.rec-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.rec-ready{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px}
.rec-intensity{font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:2px;padding:4px 12px;border-radius:20px}
.rec-intensity.moderate{background:#fef3c7;color:#b45309}
.rec-intensity.hard{background:#fee2e2;color:#b91c1c}
.rec-intensity.easy{background:#dcfce7;color:#15803d}
.rec-intensity.rest{background:var(--border-subtle);color:var(--text-muted)}
.rec-advice{font-size:16px;color:var(--text);line-height:1.7}
.cautions{margin-top:14px;padding-top:14px;border-top:1px solid var(--border-subtle)}
.cautions h4{font-size:11px;color:var(--warning);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
.cautions ul{list-style:none}
.cautions li{font-size:13px;color:var(--text-secondary);padding:2px 0}

/* AI Insight */
.ai-section{background:var(--bg-card);border-radius:16px;padding:28px;box-shadow:var(--card-shadow);margin-bottom:8px;border-left:3px solid var(--accent)}
.ai-section h3{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--accent);margin-bottom:14px}
.ai-conclusion{font-size:16px;color:var(--text);line-height:1.8;font-weight:500;padding:14px 18px;background:var(--accent-glow);border-radius:12px;margin-bottom:14px}
.ai-block{margin-bottom:10px}
.ai-block h4{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}
.ai-block.obs h4{color:var(--text-muted)}.ai-block.warn h4{color:var(--warning)}.ai-block.rec h4{color:var(--performance)}
.ai-block ul{list-style:none}
.ai-block li{font-size:13px;color:var(--text-secondary);padding:2px 0}

/* Detail / Splits */
.detail-section{background:var(--bg-card);border-radius:16px;padding:22px;box-shadow:var(--card-shadow);margin-bottom:8px}
.detail-section h3{font-size:15px;font-weight:700;margin-bottom:2px}
.detail-meta{font-size:12px;color:var(--text-muted);margin-bottom:10px}
.split-table{width:100%;border-collapse:collapse;margin-top:6px}
.split-table th{text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted);padding:6px 10px;border-bottom:1px solid var(--border-subtle)}
.split-table td{font-size:13px;font-weight:500;padding:6px 10px;border-bottom:1px solid var(--border-subtle);color:var(--text-secondary)}

/* Anomalies */
.anomalies-section{background:var(--bg-card);border-radius:16px;padding:20px 24px;box-shadow:var(--card-shadow);margin-bottom:8px;border:1px solid var(--warning);border-left:3px solid var(--warning)}
.anomalies-section h3{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--warning);margin-bottom:6px}
.anomaly-item{padding:3px 0;font-size:13px;color:var(--text-secondary)}

/* Content */
.content-section{background:var(--bg-card);border-radius:16px;padding:28px;box-shadow:var(--card-shadow);margin-bottom:8px}
.content-section h1{font-size:20px;font-weight:800;color:var(--text);margin-bottom:10px}
.content-section h2{font-size:15px;font-weight:700;color:var(--text);margin:20px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--border-subtle)}
.content-section h3{font-size:14px;font-weight:600;color:var(--text);margin:14px 0 4px}
.content-section p{margin-bottom:6px;color:var(--text-secondary);line-height:1.8}
.content-section ul{list-style:none;margin:6px 0}
.content-section li{padding:2px 0;padding-left:14px;color:var(--text-secondary);position:relative}
.content-section li::before{content:"·";position:absolute;left:0;color:var(--text-muted)}
.content-section strong{color:var(--text);font-weight:600}
.content-section a{color:var(--recovery);text-decoration:none}
.content-section hr{border:none;border-top:1px solid var(--border-subtle);margin:16px 0}
.content-section blockquote{border-left:3px solid var(--accent);padding:8px 16px;margin:10px 0;color:var(--text-secondary);font-style:italic;background:var(--accent-glow);border-radius:0 8px 8px 0}
.content-section table{width:100%;border-collapse:collapse;margin:10px 0}
.content-section th{text-align:left;padding:6px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted);border-bottom:1px solid var(--border-subtle)}
.content-section td{padding:6px 10px;font-size:13px;color:var(--text-secondary);border-bottom:1px solid var(--border-subtle)}

/* Footer */
.footer{text-align:center;padding:40px 0 20px;font-size:11px;color:var(--text-muted);letter-spacing:1px;font-weight:500}
.footer span{background:linear-gradient(135deg,var(--accent),var(--danger));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;font-weight:700}

@media(max-width:640px){
  .container{padding:16px 14px 60px}
  .header{flex-direction:column;gap:8px;padding:28px 0 24px}
  .header-date{font-size:32px}
  .hero-grid{grid-template-columns:repeat(3,1fr);gap:8px}
  .hero-card{padding:14px 8px}
  .hero-value{font-size:28px}
  .metric-strip{grid-template-columns:repeat(2,1fr)}
  .metric-val{font-size:22px}
  .session-card{flex-direction:column;align-items:flex-start;gap:12px}
  .session-metrics{width:100%;justify-content:space-between}
  .load-stats{flex-direction:column;gap:8px}
}
@media print{
  body{background:#fff}
  .hero-card,.session-card,.load-card,.trends-section,.rec-card,.ai-section,.content-section{box-shadow:none;border:1px solid #e2e8f0}
}
"""

# ═══════════════════════════════════════════════════════════════
# HTML Template
# ═══════════════════════════════════════════════════════════════

THEME_SCRIPT = """<script>
(function(){
  var initial = document.body.getAttribute('data-theme') || 'sport';
  var t = localStorage.getItem('rundown-theme') || initial;
  document.body.setAttribute('data-theme', t);
  document.querySelectorAll('.theme-btn').forEach(function(b){
    if (b.dataset.theme === t) b.classList.add('active');
  });
  window.setTheme = function(theme){
    document.body.setAttribute('data-theme', theme);
    localStorage.setItem('rundown-theme', theme);
    document.querySelectorAll('.theme-btn').forEach(function(b){
      b.classList.toggle('active', b.dataset.theme === theme);
    });
  };
})();
</script>"""


def render_daily_html(memory: Memory, output_path: str | None = None) -> str:
    fm = memory.front_matter
    target_str = fm.get("date", str(date.today()))
    target_date = date.fromisoformat(str(target_str)[:10])
    gen_time = fm.get("generated", "")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    try:
        wd = weekday_names[target_date.weekday()]
    except Exception:
        wd = ""

    has_anomalies = bool(fm.get("anomalies", {}).get("items"))
    has_trends = bool(fm.get("trends_7d", {}))

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RUNDOWN · {target_date} · Daily Report</title>
<style>{CSS}</style>
</head>
<body data-theme="sport">
{THEME_SCRIPT}
<div class="container">

  <div class="theme-bar">
    <button class="theme-btn" data-theme="fresh" onclick="setTheme('fresh')">🌿 清新</button>
    <button class="theme-btn active" data-theme="sport" onclick="setTheme('sport')">☀️ 运动</button>
    <button class="theme-btn" data-theme="dark" onclick="setTheme('dark')">🌙 暗黑</button>
  </div>

  <header class="header">
    <div>
      <div class="header-brand">RUNDOWN</div>
      <div class="header-date">{target_date} <span>{wd}</span></div>
    </div>
    <div class="header-gen">生成于 {gen_time[:16] if gen_time else '—'}</div>
  </header>

  {_hero(fm)}
  {_metric_strip(fm)}

  <div class="section-title">🏃 今日训练</div>
  {_sessions(fm)}

  <div class="section-title">📈 训练负荷</div>
  {_load(fm)}

  <div class="section-title">🎯 今日建议</div>
  {_rec(fm)}

  {_trends(fm) if has_trends else ''}
  {_anomalies(fm) if has_anomalies else ''}
  {_ai_section(fm)}
  {_detail(fm)}

  <details class="content-section" style="margin-bottom:8px;cursor:pointer">
    <summary style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--text-muted);padding:20px 28px;list-style:none;user-select:none">📋 完整文字报告 ▸</summary>
    <div style="padding:0 28px 20px">{_md_to_html(memory.body)}</div>
  </details>

  <footer class="footer">
    <span>RUNDOWN</span> · {target_date}
  </footer>

</div>
</body>
</html>"""

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(full_html, encoding="utf-8")
        import logging
        logging.getLogger(__name__).info("📄 HTML 日报已生成: %s", output_path)

    return full_html


# ═══════════════════════════════════════════════════════════════
# Image Export (Chrome headless)
# ═══════════════════════════════════════════════════════════════

def render_image(
    html_path: str,
    output_path: str | None = None,
    theme: str = "sport",
    width: int = 1600,
    scale: int = 2,
) -> str:
    """使用 Chrome headless 将 HTML 文件渲染为高清 PNG 图片。

    两步法：先以充裕高度截图（width×8），再用 Pillow 自动裁剪底部和两侧留白，
    确保内容完整、无截断、无多余空白。

    Args:
        html_path: HTML 文件路径。
        output_path: 输出 PNG 路径，默认与 HTML 同名 .png。
        theme: 初始主题 (fresh/sport/dark)。
        width: 视口宽度（像素），默认 1600。
        scale: 设备像素比，默认 2x。

    Returns:
        生成的图片路径。
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    html_file = Path(html_path).resolve()
    if not html_file.exists():
        raise FileNotFoundError(f"HTML 文件不存在: {html_path}")

    if output_path is None:
        output_path = str(html_file.with_suffix(".png"))
    output_file = Path(output_path).resolve()

    # 查找 Chrome / Chromium
    chrome_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "google-chrome",
        "chromium",
    ]
    chrome = None
    for p in chrome_paths:
        if shutil.which(p) or Path(p).exists():
            chrome = p
            break

    if chrome is None:
        raise RuntimeError(
            "未找到 Chrome/Chromium。请安装 Chrome 后重试。\n"
            "macOS: brew install --cask google-chrome"
        )

    # 注入主题设置到 HTML（在 body 标签上设置 data-theme）
    html_content = html_file.read_text(encoding="utf-8")
    # 将 body 标签的 data-theme 替换为目标主题
    import re
    html_content = re.sub(
        r'<body data-theme="[^"]*"',
        f'<body data-theme="{theme}"',
        html_content,
    )

    # 写临时文件
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    tmp.write(html_content)
    tmp.close()

    try:
        import logging
        logger = logging.getLogger(__name__)
        logger.info("📸 使用 Chrome 截图 (theme=%s, width=%d)...", theme, width)

        # Chrome --screenshot 只接受文件名，写入当前工作目录
        # 所以我们 cd 到 output 目录，截图后再确认文件位置
        out_dir = output_file.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        # 用充裕高度截图，靠 auto-crop 去除底部留白
        tall_height = min(int(width * 8), 12000)
        bg_color = {'fresh': 'e8efe9', 'sport': 'f0f0f0', 'dark': '0b1120'}.get(theme, 'f0f0f0')
        result = subprocess.run(
            [
                chrome,
                "--headless=new",
                f"--screenshot={output_file.name}",
                f"--window-size={width},{tall_height}",
                f"--force-device-scale-factor={scale}",
                f"--default-background-color={bg_color}",
                "--no-sandbox",
                "--disable-gpu",
                f"file://{tmp.name}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(out_dir),
        )

        # Chrome 有时输出到 cwd，也可能输出到别处
        # 检查 cwd 下是否有文件
        screenshot = out_dir / output_file.name
        if not screenshot.exists():
            # Chrome 可能在当前目录写了
            fallback = Path.cwd() / output_file.name
            if fallback.exists():
                import shutil
                shutil.move(str(fallback), str(screenshot))

        if not screenshot.exists():
            raise RuntimeError(
                f"Chrome 截图未生成。stderr: {result.stderr[:300]}"
            )

        logger.info("✅ 截图已生成: %s (%d bytes)", screenshot, screenshot.stat().st_size)

        # 自动裁剪底部和两侧留白
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
            img = Image.open(screenshot)
            w, h = img.size
            pixels = img.load()
            # 从角落采样实际背景色（Chrome 可能做色彩空间转换）
            bg_rgb = pixels[10, h - 10][:3]

            # 颜色容差判断（允许色差 ±3）
            def is_bg(px):
                return all(abs(int(px[i]) - int(bg_rgb[i])) <= 3 for i in range(3))

            # 从底部向上找第一个非背景色行（大步长加速）
            content_bottom = h - 1
            step = max(1, h // 200)
            for y in range(h - 1, 0, -step):
                if any(not is_bg(pixels[x, y]) for x in range(w // 3, 2 * w // 3, max(1, w // 100))):
                    content_bottom = min(h - 1, y + step)
                    break
            # 精确行
            for y in range(content_bottom, max(0, content_bottom - step - 1), -1):
                if any(not is_bg(pixels[x, y]) for x in range(0, w, max(1, w // 50))):
                    content_bottom = y
                    break

            # 从左侧向中间找第一个非背景色列
            content_left = 0
            step_x = max(1, w // 100)
            for x in range(0, w, step_x):
                if any(not is_bg(pixels[x, y]) for y in range(0, min(h, 500), 5)):
                    content_left = x
                    break

            # 从右侧向中间找第一个非背景色列
            content_right = w - 1
            for x in range(w - 1, 0, -step_x):
                if any(not is_bg(pixels[x, y]) for y in range(0, min(h, 500), 5)):
                    content_right = x
                    break

            # 裁剪（两侧留 60px 呼吸空间，底部留 80px）
            padding_h = 60
            padding_v = 80
            if content_bottom < h - 20 or content_left > 10 or content_right < w - 10:
                crop_box = (max(0, content_left - padding_h), 0,
                            min(w, content_right + padding_h), min(h, content_bottom + padding_v))
                cropped = img.crop(crop_box)
                cropped.save(screenshot, "PNG")
                logger.info("✂️  自动裁剪: %dx%d → %dx%d", w, h, cropped.width, cropped.height)
            else:
                logger.info("✂️  无需裁剪 (内容已填满)")

        except Exception as exc:
            logger.warning("自动裁剪失败: %s", exc)

        return str(screenshot)

    finally:
        Path(tmp.name).unlink(missing_ok=True)


def render_daily_image(
    memory: Memory,
    output_path: str | None = None,
    theme: str = "sport",
) -> str:
    """一步生成 HTML 并导出为 PNG 图片。"""
    import tempfile
    from pathlib import Path

    # 先生成 HTML
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    html_path = str(Path(tmp.name))
    tmp.close()

    try:
        render_daily_html(memory, html_path)
        png_path = render_image(html_path, output_path, theme=theme)
        return png_path
    finally:
        Path(html_path).unlink(missing_ok=True)
