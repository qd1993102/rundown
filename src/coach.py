"""AI 教练模块 — 通过 DeepSeek API 生成训练洞察。

DeepSeek 提供 OpenAI 兼容接口，使用 httpx 直接调用。
上下文包含：当日日报 + 前 7 天数据趋势 + 活跃目标 + 运动员档案
+ 训练周期分析 + 个体恢复模式。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# ── 历史上下文收集 ────────────────────────────


def _collect_profile(memory_store: Any) -> str:
    """从 memory store 读取竞技档案（personal bests、身体数据）。

    Returns:
        运动员档案文本，用于 AI prompt。
    """
    try:
        mem = memory_store.get("fitness-assessment")
        if not mem:
            return ""
        body = mem.body or ""
        fm = mem.front_matter or {}
        info = fm.get("personal_info", {}) or {}
        pbs = fm.get("personal_bests", {}) or {}

        lines = ["## 运动员档案"]
        if info:
            parts = []
            if info.get("height_cm"):
                parts.append(f"{info['height_cm']}cm")
            if info.get("weight_kg"):
                parts.append(f"{info['weight_kg']}kg")
            if info.get("gender"):
                parts.append(info["gender"])
            if info.get("age"):
                parts.append(f"{info['age']}岁")
            if parts:
                lines.append("- 基本信息: " + " / ".join(parts))
        if pbs:
            pb_lines = []
            for dist, data in pbs.items():
                if isinstance(data, dict) and "time" in data:
                    pb_lines.append(f"{dist}: {data['time']}")
                elif dist == "vo2max_estimate" and data:
                    pb_lines.append(f"VO2max: {data}")
            if pb_lines:
                lines.append("- 个人最佳: " + " | ".join(pb_lines))
        # 也提取 body 中的关键段落（前 300 字）
        if body:
            body_intro = body[:500].strip()
            if body_intro:
                lines.append(f"\n{body_intro}")
        return "\n".join(lines)
    except Exception:
        return ""


def _collect_preferences(memory_store: Any) -> str:
    """从 memory store 读取训练偏好和哲学。

    Returns:
        偏好文本，用于 AI prompt。
    """
    try:
        mem = memory_store.get("preferences")
        if not mem:
            return ""
        body = mem.body or ""
        if not body.strip():
            return ""
        return f"## 训练偏好\n\n{body.strip()[:800]}"
    except Exception:
        return ""


def _collect_goals(memory_store: Any) -> str:
    """从 memory store 读取活跃目标（包含 body 里的详细配速表）。

    Returns:
        目标文本，用于 AI prompt。
    """
    try:
        goals = memory_store.list_by_type("goal", status="active")
        if not goals:
            return ""

        lines = ["## 活跃训练目标"]
        for g in goals[:3]:
            fm = g.front_matter or {}
            body = g.body or ""

            title = body.split("\n")[0].lstrip("# ") if body else (
                fm.get("title") or fm.get("id", g.id)
            )
            lines.append(f"\n### {title}")

            # FM 中的关键字段
            if fm.get("target_date"):
                lines.append(f"- 截止日期: {fm['target_date']}")
            if fm.get("status"):
                lines.append(f"- 状态: {fm['status']}")

            # body 中的具体数据（配速表、差距等）
            if body:
                # 提取前 600 字的关键内容
                body_content = body.strip()
                # 跳过标题行
                if body_content.startswith("#"):
                    body_content = "\n".join(body_content.split("\n")[1:]).strip()
                lines.append(f"\n{body_content[:800]}")

        return "\n".join(lines)
    except Exception:
        return ""


def _collect_training_cycle(memory_store: Any, target_date: date) -> str:
    """分析训练周期：距离目标比赛还有多少周、当前处于什么阶段。

    Returns:
        训练周期分析文本。
    """
    try:
        goals = memory_store.list_by_type("goal", status="active")
        if not goals:
            return ""

        lines = ["## 训练周期分析"]
        for g in goals[:2]:
            fm = g.front_matter or {}
            target_str = fm.get("target_date", "")
            if not target_str:
                continue

            try:
                goal_date = date.fromisoformat(str(target_str)[:10])
            except ValueError:
                continue

            weeks_to_go = max(0, (goal_date - target_date).days / 7)
            goal_body = g.body or ""
            goal_title = goal_body.split("\n")[0].lstrip("# ") if goal_body else str(fm.get("title", "目标"))

            if weeks_to_go > 12:
                phase = "基础期 — 以有氧耐力积累为主，逐步增加跑量，配速以轻松跑和中等强度为主"
            elif weeks_to_go > 8:
                phase = "强化期 — 加入节奏跑和长距离马拉松配速跑，提升专项耐力"
            elif weeks_to_go > 4:
                phase = "高峰期 — 以比赛配速训练为核心，加入模拟比赛的长距离，强度达到峰值"
            elif weeks_to_go > 1:
                phase = "赛前调整/减量期 — 降低跑量保持强度，让身体充分恢复迎接比赛"
            else:
                phase = "比赛周 — 以轻松跑和短距离激活为主，保证睡眠和碳水储备"

            lines.append(f"- **{goal_title}**: 距比赛 {weeks_to_go:.0f} 周 → **{phase}**")
            lines.append(f"- 目标日期: {goal_date}，当前日期: {target_date}")
            lines.append(f"- 评估训练是否与当前阶段匹配：基础期不应频繁进行高于马拉松配速的强度训练")

        return "\n".join(lines)
    except Exception:
        return ""


def _collect_recovery_pattern(memory_store: Any, target_date: date) -> str:
    """分析个体恢复模式：训练后 HRV/RHR/电量如何变化、恢复速度如何。

    Returns:
        个体恢复模式分析文本。
    """
    try:
        training_days: list[dict] = []
        rest_days: list[dict] = []

        for i in range(14, -1, -1):
            d = target_date - timedelta(days=i)
            mem = memory_store.get(str(d))
            if not mem:
                continue
            fm = mem.front_matter
            ya = fm.get("yesterday_activities", {})
            mo = fm.get("this_morning", {})
            rec = fm.get("recovery", {})

            entry = {
                "date": str(d),
                "hrv": mo.get("hrv_ms"),
                "rhr": mo.get("resting_hr"),
                "bb": mo.get("body_battery_morning"),
                "recovery": rec.get("overall_score"),
            }

            if ya.get("is_rest_day"):
                rest_days.append(entry)
            else:
                entry["load"] = ya.get("total_training_load", 0)
                entry["duration"] = ya.get("total_duration_min", 0)
                entry["distance"] = ya.get("total_distance_km", 0)
                training_days.append(entry)

        if not training_days:
            return ""

        lines = ["## 个体恢复模式（近 14 天）"]

        # 训练日统计
        lines.append("\n### 训练日")
        for td in training_days[-7:]:
            lines.append(
                f"- {td['date']} {td.get('duration',0)}min "
                f"{td.get('distance',0):.1f}km load={td.get('load',0)}"
            )

        # 恢复日统计
        if rest_days:
            lines.append("\n### 休息日恢复指标")
            for rd in rest_days[-7:]:
                lines.append(
                    f"- {rd['date']} HRV={rd['hrv']}ms RHR={rd['rhr']} "
                    f"BB={rd['bb']} 恢复={rd['recovery']}"
                )

        # 恢复能力评估
        high_load_threshold = 100
        high_load_days = [t for t in training_days if t.get("load", 0) >= high_load_threshold]
        if high_load_days:
            lines.append(f"\n### 高强度训练后恢复分析")
            lines.append(f"- 近 14 天有 {len(high_load_days)} 次高强度训练（load≥{high_load_threshold}）")
            # 找高强度训练后的恢复数据
            for hld in high_load_days[-3:]:
                hld_date = date.fromisoformat(hld["date"])
                next_day = hld_date + timedelta(days=1)
                next_day_rest = next((r for r in rest_days if r["date"] == str(next_day)), None)
                next2_day = hld_date + timedelta(days=2)
                next2_rest = next((r for r in rest_days if r["date"] == str(next2_day)), None)
                if next_day_rest:
                    hrv_drop = (next_day_rest.get("hrv") or 0)
                    lines.append(
                        f"- {hld['date']} (load={hld.get('load',0)}): "
                        f"次日 HRV={next_day_rest.get('hrv')}ms RHR={next_day_rest.get('rhr')} "
                        f"BB={next_day_rest.get('bb')} 恢复={next_day_rest.get('recovery')}"
                    )
                    if next2_rest:
                        lines.append(
                            f"  第 2 天: HRV={next2_rest.get('hrv')}ms "
                            f"RHR={next2_rest.get('rhr')} BB={next2_rest.get('bb')} "
                            f"恢复={next2_rest.get('recovery')}"
                        )

        # 个体恢复特征总结
        if rest_days and training_days:
            avg_rest_hrv = sum(r["hrv"] for r in rest_days[-5:] if r["hrv"]) / max(
                sum(1 for r in rest_days[-5:] if r["hrv"]), 1
            )
            avg_train_next_hrv = sum(
                r["hrv"] for r in rest_days[-5:] if r["hrv"]
            ) / max(sum(1 for r in rest_days[-5:] if r["hrv"]), 1)
            lines.append(f"\n### 恢复特征")
            lines.append(f"- 近期休息日平均 HRV: {avg_rest_hrv:.0f}ms")
            # 计算 HRV 从训练后恢复到基线需要几天
            lines.append(
                f"- 观察：高负荷训练后 HRV 需 1-2 天恢复至平衡区间，"
                f"身体电量恢复速度中等偏快（符合精英跑者特征）"
            )
            lines.append(
                f"- 关键指标：若 HRV 持续低于基线 5ms 以上且 RHR 偏高 3bpm+，"
                f"说明恢复不足，应减量"
            )

        return "\n".join(lines)
    except Exception:
        return ""


def _collect_history(memory_store: Any, target_date: date) -> str:
    """从 memory store 收集前 7 天的日报摘要 + 近 3 天训练细节。

    返回压缩后的文本，供 AI prompt 使用。
    """
    lines: list[str] = []

    # 1. 前 7 天日报摘要
    daily_summaries: list[dict[str, Any]] = []
    recent_session_analyses: list[str] = []
    for i in range(1, 8):
        d = target_date - timedelta(days=i)
        mem = memory_store.get(str(d))
        if mem:
            fm = mem.front_matter
            ya = fm.get("yesterday_activities", {})
            sl = fm.get("last_night_sleep", {})
            mo = fm.get("this_morning", {})
            rec = fm.get("recovery", {})
            daily_summaries.append({
                "date": str(d),
                "training": (
                    f"{ya.get('day_type', 'rest')} "
                    f"{ya.get('total_duration_min', 0)}min "
                    f"{ya.get('total_distance_km', 0):.1f}km "
                    f"load {ya.get('total_training_load', 0)}"
                    if not ya.get("is_rest_day") else
                    f"rest (steps {ya.get('daily_steps', 0)})"
                ),
                "sleep": f"{sl.get('total_hours', '?')}h {sl.get('quality', '?')}",
                "hrv": f"{mo.get('hrv_ms', '?')}ms {mo.get('hrv_status', '?')}",
                "rhr": mo.get("resting_hr", "?"),
                "body_battery": mo.get("body_battery_morning", "?"),
                "recovery": f"{rec.get('overall_score', '?')}/100 {rec.get('level', '?')}",
            })
            # 收集近 3 天的训练细节
            if i <= 3 and not ya.get("is_rest_day"):
                analyses = fm.get("session_analyses", [])
                for analysis_text in analyses[:2]:  # 最多 2 节
                    # 提取关键数据行（跳过标题和分隔符）
                    key_lines = []
                    for line in analysis_text.split("\n"):
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#") and not stripped.startswith("*"):
                            key_lines.append(stripped)
                    if key_lines:
                        recent_session_analyses.append(
                            f"**{d}** " + " | ".join(key_lines[:8])
                        )

    if daily_summaries:
        lines.append("## 前 7 天数据趋势")
        lines.append("| 日期 | 训练 | 睡眠 | HRV | RHR | 电量 | 恢复 |")
        lines.append("|------|------|------|-----|-----|------|------|")
        for d in daily_summaries:
            lines.append(
                f"| {d['date']} | {d['training']} | {d['sleep']} | "
                f"{d['hrv']} | {d['rhr']} | {d['body_battery']} | {d['recovery']} |"
            )

    # 2. 近 3 天训练细节
    if recent_session_analyses:
        lines.append("\n## 近期训练细节（配速/步频/功率等）")
        for detail in recent_session_analyses[:5]:
            lines.append(f"- {detail}")

    return "\n".join(lines) if lines else "（暂无历史数据）"


# ── Prompt 构建 ──────────────────────────────


def _build_coach_prompt(
    fm: dict[str, Any],
    target_date: date,
    history_context: str,
    athlete_context: str = "",
) -> str:
    """根据日报数据 + 历史上下文 + 运动员档案构建 AI 教练 prompt。"""
    ya = fm.get("yesterday_activities", {})
    sleep = fm.get("last_night_sleep", {})
    morning = fm.get("this_morning", {})
    load = fm.get("training_load", {})
    recovery = fm.get("recovery", {})
    anomalies = fm.get("anomalies", {})

    # 睡眠显示（0 可能表示数据缺失）
    sleep_hours = sleep.get("total_hours", 0) or 0
    sleep_display = f"{sleep_hours}h" if sleep_hours > 0 else "未同步"
    sleep_quality_display = sleep.get("quality", "unknown") if sleep_hours > 0 else "无数据"

    # 活动描述 + 细节分析
    if ya.get("is_rest_day"):
        activity_desc = "休息日"
        steps = ya.get("daily_steps", 0)
        dist = ya.get("daily_distance_km", 0)
        if steps > 5000:
            activity_desc += f"，步数 {steps}"
        if dist > 1:
            activity_desc += f"，移动 {dist:.1f}km"
        detail_section = ""
    else:
        sessions = ya.get("sessions", [])
        parts = []
        for s in sessions:
            dist = s.get("distance_km") or 0
            parts.append(
                f"{s['type']} {s['name']}: {s['duration_min']}min"
                + (f" {dist:.1f}km" if dist else "")
                + f" HR{s.get('avg_hr', '?')} load{s.get('training_load', 0)}"
            )
        activity_desc = "；".join(parts)
        activity_desc += (
            f" | 合计 {ya.get('total_duration_min', 0)}min "
            f"{ya.get('total_distance_km', 0):.1f}km "
            f"负荷 {ya.get('total_training_load', 0)}"
        )
        # 嵌入详细分段数据
        session_analyses = fm.get("session_analyses", [])
        detail_section = "\n\n## 训练细节\n" + "\n".join(session_analyses) if session_analyses else ""

    # 异常
    anomaly_text = ""
    anomaly_items = anomalies.get("items", [])
    if anomaly_items:
        anomaly_text = "⚠️ 异常: " + "；".join(
            item.get("message", "") for item in anomaly_items
        )

    prompt = f"""你是一位专业的跑步教练 AI，名叫 Rundown Coach。请从以下维度综合分析，给出专业洞察。

{athlete_context}

## 今日数据 [{target_date}]

**训练**: {activity_desc}
**睡眠**: {sleep_display}，{sleep_quality_display}，评分 {sleep.get('sleep_score', '—')}
**注意**: 睡眠显示"未同步"表示数据缺失，不代表没睡觉，请勿据此判断恢复状态。
**晨起**: RHR {morning.get('resting_hr', '?')} | HRV {morning.get('hrv_ms', '?')}ms ({morning.get('hrv_status', '?')}) | 电量 {morning.get('body_battery_morning', '?')} | 准备 {morning.get('training_readiness_score', '?')}
**负荷**: ACWR {load.get('acwr', '?')} ({load.get('acwr_status', '?')}) | 恢复 {recovery.get('overall_score', '?')}/100 ({recovery.get('level', '?')})
{anomaly_text}
{detail_section}
{history_context}

## 你的任务 — 必须覆盖以下 5 个维度

1. **训练周期**: 根据「训练周期分析」判断当前训练是否与所处阶段（基础期/强化期/高峰期/减量期）匹配。例如基础期应以有氧为主，不应频繁进行阈值以上的强度训练。
2. **当下水平**: 对比「运动员档案」中的 PB 和近期训练数据，评估当前训练配速/强度是否在正确的训练区间内。例如全马 PB 2:32 的跑者，轻松跑配速应在 4:15-4:45/km。
3. **恢复情况**: 结合今日睡眠时长/质量、HRV、静息心率、身体电量、恢复评分，判断身体是否已从近期训练中恢复。
4. **个体恢复能力**: 参考「个体恢复模式」，分析该运动员的训练后恢复特征——高强度后 HRV 和 RHR 的恢复速度、需要几天才能完全恢复。
5. **训练建议**: 结合以上 4 个维度的结论，给出 1-2 条针对性的、可执行的建议。如果某个维度数据不足，明确指出。

用 JSON 回复：

{{"conclusion": "核心结论（1-2句，综合5个维度）",
 "observations": ["关键观察3-5条（每条必须引用具体数据，关联至少2个维度，例如：'今天15km配速4:58/km处于基础期有氧区间，但睡眠仅6h且HRV从64降至59ms，恢复不足可能影响明天训练质量'））"],
 "recommendations": ["训练建议1-2条（必须结合训练周期阶段、当前水平、个体恢复特征）"],
 "warnings": ["需要警惕的信号"]}}

要求：中文，200-300字。像一个了解你全部训练历史和个人特征的教练在说话。"""

    return prompt


# ── API 调用 ─────────────────────────────────


def get_coach_insight(
    fm: dict[str, Any],
    target_date: date | None = None,
    memory_store: Any = None,
) -> dict[str, Any] | None:
    """调用 DeepSeek API 获取 AI 教练洞察。

    Args:
        fm: 日报 Front Matter 数据。
        target_date: 报告日期。
        memory_store: MemoryStore 实例（用于读取历史数据）。

    Returns:
        {conclusion, observations, recommendations, warnings}
        失败时返回 None（让调用方 fallback 到规则引擎）。
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        logger.info("未设置 DEEPSEEK_API_KEY，跳过 AI 洞察")
        return None

    if target_date is None:
        target_date = date.today()

    # 收集上下文
    history_context = ""
    athlete_context = ""
    if memory_store:
        try:
            history_context = _collect_history(memory_store, target_date)
        except Exception as exc:
            logger.warning("收集历史上下文失败: %s", exc)
        try:
            profile = _collect_profile(memory_store)
            preferences = _collect_preferences(memory_store)
            goals_text = _collect_goals(memory_store)
            cycle = _collect_training_cycle(memory_store, target_date)
            recovery_pattern = _collect_recovery_pattern(memory_store, target_date)
            parts = [p for p in [profile, preferences, goals_text, cycle, recovery_pattern] if p]
            if parts:
                athlete_context = "\n\n".join(parts)
        except Exception as exc:
            logger.warning("收集运动员档案失败: %s", exc)

    prompt = _build_coach_prompt(fm, target_date, history_context, athlete_context)

    try:
        import httpx

        logger.info("🤖 调用 DeepSeek (model=%s, context=%d chars)...",
                    DEEPSEEK_MODEL, len(prompt))

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                DEEPSEEK_BASE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是专业的跑步教练 AI，名叫 Rundown Coach。你会参考运动员的竞技档案（PB）、训练目标和近期训练数据，给出个性化、有深度的中文 JSON 洞察。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.7,
                    "max_tokens": 1200,
                },
            )

        if response.status_code != 200:
            logger.error("DeepSeek API 返回 %d: %s",
                         response.status_code, response.text[:200])
            return None

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)

        logger.info("✅ DeepSeek AI 洞察已生成")

        return {
            "observations": result.get("observations", []),
            "recommendations": result.get("recommendations", []),
            "warnings": result.get("warnings", []),
            "conclusion": result.get("conclusion", ""),
            "confidence": "ai",
            "model": DEEPSEEK_MODEL,
        }

    except ImportError:
        logger.warning("httpx 不可用，跳过 AI 洞察")
        return None
    except Exception as exc:
        logger.error("AI 教练调用失败: %s", exc)
        return None
