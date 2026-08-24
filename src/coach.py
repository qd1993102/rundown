"""日报在线教练入口。

通用 Web Chat 与模型 Tool Use 已移除。调用方先构建完整日报事实，本模块只运行一次
``review-daily-training`` Skill 并返回兼容现有日报的结构化洞察。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .coach_runtime import (
    CoachRunContext,
    CoachSkillRunner,
    OpenAICompatibleSkillModel,
    SkillRegistry,
)
from .config import get_ai_config

logger = logging.getLogger(__name__)

_CORE_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "coach-core.md"


def _normalize_report_date_language(value: Any, target_date: date) -> Any:
    """把模型相对日称谓转换为报告日期上下文中的稳定表达。"""

    if isinstance(value, str):
        previous_date = str(target_date - timedelta(days=1))
        next_date = str(target_date + timedelta(days=1))
        return (
            value.replace("昨天", previous_date)
            .replace("昨日", previous_date)
            .replace("明天", next_date)
            .replace("明日", next_date)
            .replace("今天", "当日")
            .replace("今日", "当日")
        )
    if isinstance(value, list):
        return [_normalize_report_date_language(item, target_date) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_report_date_language(item, target_date)
            for key, item in value.items()
        }
    return value


def _daily_fact_pack(fm: dict[str, Any], target_date: date) -> dict[str, Any]:
    """只向日报 Skill 提供已经过服务端门禁和计算的事实。"""

    return {
        "date": str(target_date),
        "data_readiness": fm.get("data_readiness", "unknown"),
        "report_finality": fm.get("report_finality", "unknown"),
        "data_as_of": fm.get("data_as_of"),
        "data_coverage": fm.get("data_coverage", {}),
        "omitted_sections": fm.get("omitted_sections", []),
        "daily_activities": fm.get("daily_activities") or fm.get("yesterday_activities", {}),
        "last_night_sleep": fm.get("last_night_sleep", {}),
        "this_morning": fm.get("this_morning", {}),
        "training_load": fm.get("training_load", {}),
        "recovery": fm.get("recovery", {}),
        "trends_7d": fm.get("trends_7d", {}),
        "anomalies": fm.get("anomalies", {}),
        "recommendation": fm.get("recommendation", {}),
        "session_analyses": fm.get("session_analyses", []),
        "training_day_summary": fm.get("training_day_summary", {}),
        "athlete_context": fm.get("athlete_context", {}),
        "plan_context": fm.get("plan_context", {}),
        "plan_execution_summary": fm.get("plan_execution_summary", {}),
        "running_analysis": fm.get("running_analysis_daily", {}),
    }


def _daily_runner() -> CoachSkillRunner:
    registry = SkillRegistry.default()
    core_prompt = (
        _CORE_PROMPT_FILE.read_text(encoding="utf-8")
        if _CORE_PROMPT_FILE.exists() else ""
    )
    return CoachSkillRunner(
        registry,
        OpenAICompatibleSkillModel(),
        core_prompt=core_prompt,
    )


def get_coach_insight(
    fm: dict[str, Any],
    target_date: date | None = None,
) -> dict[str, Any] | None:
    """用一次结构化 Skill 调用生成日报教练洞察。"""

    ai_config = get_ai_config()
    if not ai_config.api_key:
        logger.info("未设置 NEURUN_AI_API_KEY，跳过在线 AI 洞察")
        return None

    target_date = target_date or date.today()
    plan_context = fm.get("plan_context") or {}
    context = CoachRunContext(
        request={"intent": "review_daily_training", "target_date": str(target_date)},
        facts={
            "daily_facts": _daily_fact_pack(fm, target_date),
            "active_scheme": plan_context,
        },
        uncertainties=tuple(str(item) for item in fm.get("omitted_sections", [])),
        policy={"coaching_mode": fm.get("coaching_mode", "continuous_running")},
    )
    try:
        result, _final_context = _daily_runner().run(
            "review-daily-training",
            context,
        )
    except Exception as exc:
        logger.error("日报在线教练调用失败: %s", exc)
        return None

    result = _normalize_report_date_language(result, target_date)
    if plan_context.get("status") in {
        "no_effective_plan", "draft_available", "scheduled_plan",
    }:
        execution = result.get("plan_execution") or {}
        result["plan_execution"] = {
            "today_planned": "当日尚无生效计划",
            "today_actual": execution.get("today_actual", ""),
            "today_match": "不适用",
            "comparison_status": "not_applicable",
            "week_completion": "",
            "on_track": None,
            "deviation_note": (
                "报告日期早于首个生效方案，不进行计划执行评价。"
                if plan_context.get("status") == "no_effective_plan"
                else "报告日只有草稿或未来排期，仅供参考，不进行计划执行评价。"
            ),
        }
    result["plan_adjusted"] = False
    result["confidence"] = "ai"
    result["generation_mode"] = "online_ai"
    result["semantic_status"] = "available"
    result["fallback_reason"] = None
    result["model"] = ai_config.model
    logger.info("在线日报教练洞察已生成 (review-daily-training)")
    return result
