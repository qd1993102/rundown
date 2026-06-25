"""AI 教练模块 — 通过 DeepSeek API 生成训练洞察。

DeepSeek 提供 OpenAI 兼容接口，使用 httpx 直接调用。
上下文包含：当日日报 + 前 7 天数据趋势 + 活跃目标。
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


def _collect_history(memory_store: Any, target_date: date) -> str:
    """从 memory store 收集前 7 天的日报摘要，构建趋势上下文。

    返回压缩后的文本，供 AI prompt 使用。
    """
    lines: list[str] = []

    # 1. 前 7 天日报摘要
    daily_summaries: list[dict[str, Any]] = []
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

    if daily_summaries:
        lines.append("## 前 7 天数据趋势")
        lines.append("| 日期 | 训练 | 睡眠 | HRV | RHR | 电量 | 恢复 |")
        lines.append("|------|------|------|-----|-----|------|------|")
        for d in daily_summaries:
            lines.append(
                f"| {d['date']} | {d['training']} | {d['sleep']} | "
                f"{d['hrv']} | {d['rhr']} | {d['body_battery']} | {d['recovery']} |"
            )

    # 2. 活跃目标
    goals = memory_store.list_by_type("goal", status="active")
    if goals:
        lines.append("\n## 活跃训练目标")
        for g in goals[:3]:
            fm = g.front_matter
            lines.append(
                f"- **{fm.get('title', g.id)}**: "
                f"当前 {fm.get('metrics', {}).get('current_5k_time', '?')} "
                f"→ 目标 {fm.get('metrics', {}).get('target_5k_time', '?')}"
                f"（截止 {fm.get('target_date', '?')}）"
            )

    return "\n".join(lines) if lines else "（暂无历史数据）"


# ── Prompt 构建 ──────────────────────────────


def _build_coach_prompt(
    fm: dict[str, Any],
    target_date: date,
    history_context: str,
) -> str:
    """根据日报数据 + 历史上下文构建 AI 教练 prompt。"""
    ya = fm.get("yesterday_activities", {})
    sleep = fm.get("last_night_sleep", {})
    morning = fm.get("this_morning", {})
    load = fm.get("training_load", {})
    recovery = fm.get("recovery", {})
    anomalies = fm.get("anomalies", {})

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

    prompt = f"""你是一位专业的跑步教练 AI，名叫 Rundown Coach。

## 今日数据 [{target_date}]

**训练**: {activity_desc}
**睡眠**: {sleep.get('total_hours', '?')}h，{sleep.get('quality', '?')}，评分 {sleep.get('sleep_score', '?')}
**晨起**: RHR {morning.get('resting_hr', '?')} | HRV {morning.get('hrv_ms', '?')}ms ({morning.get('hrv_status', '?')}) | 电量 {morning.get('body_battery_morning', '?')} | 准备 {morning.get('training_readiness_score', '?')}
**负荷**: ACWR {load.get('acwr', '?')} ({load.get('acwr_status', '?')}) | 恢复 {recovery.get('overall_score', '?')}/100 ({recovery.get('level', '?')})
{anomaly_text}
{detail_section}
{history_context}

## 你的任务

请基于以上数据（包括训练细节中的分段配速、步频、功率、触地时间等），结合历史趋势，用 JSON 回复：

{{"conclusion": "核心结论（1-2句。有训练细节时，分析配速/步频/功率等数据；有历史数据时做对比）",
 "observations": ["关键观察3-5条（必须引用具体数据，发现数据间关联）"],
 "recommendations": ["训练建议1-2条（结合训练细节和趋势给出具体可执行的建议）"],
 "warnings": ["注意事项"]}}

要求：中文，像朋友聊天，200字内。训练细节丰富时重点分析技术数据。"""

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

    # 收集历史上下文
    history_context = ""
    if memory_store:
        try:
            history_context = _collect_history(memory_store, target_date)
        except Exception as exc:
            logger.warning("收集历史上下文失败: %s", exc)

    prompt = _build_coach_prompt(fm, target_date, history_context)

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
                            "content": "你是专业的跑步教练 AI，用中文 JSON 回复。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.7,
                    "max_tokens": 800,
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
