"""调用训练方案草稿生成，并统计每次 AI 交互的 Prompt 内容、token 长度与调用时长。

用法（项目根目录执行）:
    .venv/bin/python scripts/ai_draft_stats.py --user <api_key> [--output json]

默认使用 qd1993102@gmail.com 的 MrGrass 账号（rd_321b036cac9d99d8d5a92ab4fc9701bf）。
与 Web 服务共用同一套事实构造逻辑（load_week / load_setup），
只是给 OpenAICompatibleSkillModel.generate 包了一层统计探针。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.coach_runtime import CoachSkillRunner, OpenAICompatibleSkillModel
from src.coach_runtime.runner import _OUTPUT_JSON_EXAMPLES, _OUTPUT_JSON_RULES
from src.config import get_config
from src.memory import MemoryStore, MemoryType
from src.storage import Storage
from src.training import TrainingService
from src.training_planning import ProfessionalSchemePlanner

logger = logging.getLogger("ai_draft_stats")


# ── 用户与草稿参数 ────────────────────────────────
DEFAULT_USER = "rd_321b036cac9d99d8d5a92ab4fc9701bf"  # qd1993102@gmail.com (MrGrass)
DEFAULT_GOAL_ID = "goal-230-a90ab75e"  # 全马230, 2026-10-18
# 沿用该用户上一份草稿的约束
DRAFT_PAYLOAD: dict[str, Any] = {
    "goal_id": DEFAULT_GOAL_ID,
    "available_days": [0, 1, 2, 3, 5, 6],
    "max_session_minutes": 180,
    "preferred_terrain": "公路,操场",
    "cross_training": False,
    "medical_limitations": "",
    "reported_weekly_mileage": None,
    "additional_context": "",
}


# ── 统计探针 ──────────────────────────────────────
def _cjk_aware_token_estimate(text: str) -> int:
    """混合中英文的轻量 token 估算：CJK 字符约 1 token/字，其余约 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, math.ceil(other / 4))


class AiCallRecord:
    """一次 AI 交互的完整统计。"""

    def __init__(
        self,
        call_index: int,
        step: str,
        spec_name: str,
        output_model: str,
        prompt: str,          # 最终发给 provider 的 system prompt
        user_payload: str,    # 最终发给 provider 的 user payload(JSON 文本)
        started: float,
        duration: float,
        result: dict[str, Any] | None,
        error: str | None,
    ):
        self.call_index = call_index
        self.step = step
        self.spec_name = spec_name
        self.output_model = output_model
        self.prompt = prompt
        self.user_payload = user_payload
        self.started = started
        self.duration = duration
        self.result = result
        self.error = error

    def sizes(self) -> dict[str, Any]:
        prompt_bytes = len(self.prompt.encode("utf-8"))
        payload_bytes = len(self.user_payload.encode("utf-8"))
        result_bytes = 0
        if self.result is not None:
            result_bytes = len(json.dumps(self.result, ensure_ascii=False).encode("utf-8"))
        return {
            "prompt_bytes": prompt_bytes,
            "prompt_tokens_est": _cjk_aware_token_estimate(self.prompt),
            "prompt_tokens_est_byte_div4": math.ceil(prompt_bytes / 4),
            "payload_bytes": payload_bytes,
            "payload_tokens_est": _cjk_aware_token_estimate(self.user_payload),
            "payload_tokens_est_byte_div4": math.ceil(payload_bytes / 4),
            "total_bytes": prompt_bytes + payload_bytes,
            "total_tokens_est": _cjk_aware_token_estimate(self.prompt) + _cjk_aware_token_estimate(self.user_payload),
            "result_bytes": result_bytes,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_index": self.call_index,
            "step": self.step,
            "skill": self.spec_name,
            "output_model": self.output_model,
            "duration_seconds": round(self.duration, 3),
            **self.sizes(),
            "error": self.error,
            "prompt_content": self.prompt,
            "user_payload_content": self.user_payload,
        }


class InstrumentedSkillModel:
    """包装 OpenAICompatibleSkillModel，记录每次 generate 的完整统计。"""

    def __init__(self, inner: OpenAICompatibleSkillModel):
        self._inner = inner
        self.calls: list[AiCallRecord] = []

    def generate(
        self, spec, prompt: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        call_index = len(self.calls) + 1
        request_meta = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        ai_call_index = request_meta.get("ai_call_index")
        step = {
            "build-training-framework": "training_framework (AI 调用 1/2)",
            "build-near-term-schedule": "near_term_schedule (AI 调用 2/2)",
        }.get(spec.name, spec.name)
        # 与 OpenAICompatibleSkillModel.generate 完全一致地重建实际发送的 system prompt
        example = _OUTPUT_JSON_EXAMPLES.get(spec.output_model, '{"result":"value"}')
        contract = (
            "Provider response contract: return exactly one valid json object, "
            f"compatible with the {spec.output_model} output model. "
            "Do not use Markdown fences or add text outside the object. "
            f"Use these exact json keys and value types: {example}"
        )
        output_rules = _OUTPUT_JSON_RULES.get(spec.output_model, "")
        structured_prompt = f"{prompt}\n\n{contract}{output_rules}"
        user_payload = json.dumps(payload, ensure_ascii=False)
        started = time.monotonic()
        error = None
        result = None
        try:
            result = self._inner.generate(spec, prompt, payload)
        except Exception as exc:  # noqa: BLE001 - 统计探针必须记录失败
            error = str(exc)
            raise
        finally:
            duration = time.monotonic() - started
            self.calls.append(AiCallRecord(
                call_index=call_index,
                step=step,
                spec_name=spec.name,
                output_model=spec.output_model,
                prompt=structured_prompt,
                user_payload=user_payload,
                started=started,
                duration=duration,
                result=result,
                error=error,
            ))
        return result


# ── 与 Web 服务一致的数据装载 ───────────────────────
def make_load_week(user_cfg):
    """复刻 web.register_web_routes 内 load_week 闭包。"""
    from src.training_analysis import display_name, get_latest_training_analysis

    def load_week(start: date, end: date):
        if not Path(user_cfg.db_path).exists():
            return [], {}
        storage = Storage(user_cfg)
        try:
            user_id = storage.get_local_user_id()
            if user_id is None:
                return [], {}
            activities = storage.get_activities_range(user_id, start, end)
            fixed_week_activities: list[dict[str, Any]] = []
            for item in activities:
                raw_date = item.get("activity_date") or item.get("date")
                try:
                    activity_date = date.fromisoformat(str(raw_date)[:10])
                except (TypeError, ValueError):
                    continue
                if start <= activity_date <= end:
                    fixed_week_activities.append(item)
            history = MemoryStore(
                user_cfg.memory_dir, db_getter=lambda: storage.db,
            ).get_training_history_entries(
                user_id=user_id, days=(end - start).days + 1, end_date=end,
            )
            for activity in activities:
                analysis = get_latest_training_analysis(
                    storage.db, user_id, str(activity.get("activity_id") or ""),
                )
                if analysis:
                    activity["training_analysis"] = {
                        **analysis,
                        "display_name": display_name(
                            analysis["primary_type"], analysis["terrain"],
                        ),
                    }
            states = {
                str(item["date"]): str(item.get("activity_state") or "unknown")
                for item in history
            }
            return activities, states
        finally:
            storage.close()

    return load_week


def make_load_setup(user_cfg):
    """复刻 web.register_web_routes 内 load_setup 闭包。"""

    def load_setup(target: date | None = None) -> dict[str, Any]:
        baseline = {
            "coverage": "unknown", "window_days": 28,
            "activity_count": 0, "distance_km": 0,
            "longest_distance_km": 0,
        }
        known_constraints: dict[str, Any] = {}
        memory_store = MemoryStore(user_cfg.memory_dir)
        athlete_profile: dict[str, Any] = {}
        recovery_snapshot: dict[str, Any] = {}
        profile = memory_store.get("fitness-assessment")
        if profile:
            athlete_profile = dict(profile.front_matter)
        reports = memory_store.list_by_type(MemoryType.DAILY_REPORT)
        eligible_reports = [
            report for report in reports
            if target is None or (
                report.created_date is not None
                and report.created_date <= target
            )
        ]
        latest_report = max(
            eligible_reports,
            key=lambda report: report.created_date or date.min,
            default=None,
        )
        if latest_report:
            latest_fm = latest_report.front_matter
            recovery_snapshot = {
                "date": latest_fm.get("date"),
                "recovery": latest_fm.get("recovery") or {},
                "morning": latest_fm.get("morning") or {},
                "sleep": latest_fm.get("sleep") or {},
                "training_load": latest_fm.get("training_load") or {},
            }

        def result() -> dict[str, Any]:
            return {
                "baseline": baseline,
                "known_constraints": known_constraints,
                "athlete_profile": athlete_profile,
                "recovery_snapshot": recovery_snapshot,
            }

        preferences = memory_store.get("preferences")
        if preferences:
            training_preferences = (
                preferences.front_matter.get("training_preferences") or {}
            )
            preferred_terrain = training_preferences.get("preferred_terrain") or []
            if isinstance(preferred_terrain, list) and preferred_terrain:
                known_constraints["preferred_terrain"] = preferred_terrain[0]
            injuries = preferences.front_matter.get("injury_history") or []
            if injuries:
                known_constraints["medical_limitations"] = str(
                    injuries[0].get("description") or ""
                )
        if not Path(user_cfg.db_path).exists():
            return result()
        storage = Storage(user_cfg)
        try:
            user_id = storage.get_local_user_id()
            if user_id is None:
                return result()
            as_of = target or date.today()
            current_monday = as_of - timedelta(days=as_of.weekday())
            end = current_monday - timedelta(days=1)
            start = end - timedelta(days=6)
            activities = storage.get_activities_range(user_id, start, end)
            calendar = storage.get_sync_calendar(user_id, start, end, today=end)
            calendar_counts = calendar.get("summary") or calendar.get("counts") or {}
            synced_days = int(calendar_counts.get("synced") or 0)
            coverage = "sufficient" if synced_days >= 5 else "partial" if synced_days else "unknown"
            fixed_week_activities: list[dict[str, Any]] = []
            for item in activities:
                raw_date = item.get("activity_date") or item.get("date")
                try:
                    activity_date = date.fromisoformat(str(raw_date)[:10])
                except (TypeError, ValueError):
                    continue
                if start <= activity_date <= end:
                    fixed_week_activities.append(item)
            running_activities = [
                item for item in fixed_week_activities
                if "run" in str(item.get("activity_type") or "").lower()
                or "跑" in str(item.get("activity_name") or "")
            ]
            distances = [
                float(item.get("distance_meters") or 0) / 1000
                for item in running_activities
            ]
            previous_week_km = round(sum(distances), 1)
            baseline = {
                "coverage": coverage, "window_days": 7,
                "reference_window_kind": "previous_completed_natural_week",
                "reference_window_start": str(start),
                "reference_window_end": str(end),
                "activity_count": len(running_activities),
                "distance_km": previous_week_km,
                "longest_distance_km": round(max(distances, default=0), 1),
                "previous_week_km": previous_week_km,
                "average_weekly_km": previous_week_km,
                "recent_7d_km": 0,
            }
            return result()
        finally:
            storage.close()

    return load_setup


def build_service(user_cfg) -> tuple[TrainingService, ProfessionalSchemePlanner, InstrumentedSkillModel]:
    """按 Web 服务的默认方式构建 planner，但替换为带统计探针的模型。"""
    from src.coach_runtime import SkillRegistry
    from src.training_planning import create_default_professional_planner

    registry = SkillRegistry.default(str(ROOT))
    root = registry.get("draft-training-scheme").prompt_path.parents[2]
    core_path = root / "coach-core.md"
    core_prompt = core_path.read_text(encoding="utf-8") if core_path.exists() else ""
    instrumented = InstrumentedSkillModel(OpenAICompatibleSkillModel())
    runner = CoachSkillRunner(
        registry,
        instrumented,
        core_prompt=core_prompt,
    )
    planner = ProfessionalSchemePlanner(runner=runner)
    service = TrainingService(
        user_cfg.memory_dir,
        activity_loader=make_load_week(user_cfg),
        setup_context_loader=make_load_setup(user_cfg),
        scheme_planner=planner,
    )
    return service, planner, instrumented


def main() -> None:
    parser = argparse.ArgumentParser(description="调用草稿生成并统计每次 AI 交互")
    parser.add_argument("--user", default=DEFAULT_USER, help="用户 api_key(默认 MrGrass qd1993102@gmail.com)")
    parser.add_argument("--goal-id", default=DEFAULT_GOAL_ID, help="目标 id")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    args = parser.parse_args()

    config = get_config(validate_credentials=False)
    user_cfg = config.for_user(args.user)
    # 确认账号
    user_json = Path(config.data_dir) / "users" / f"{args.user}.json"
    email_label = "unknown"
    if user_json.exists():
        try:
            email_label = json.loads(user_json.read_text(encoding="utf-8")).get(
                "garmin_email") or json.loads(user_json.read_text(encoding="utf-8")).get("email") or "unknown"
        except Exception:
            pass

    print(f"账号: {email_label} (api_key={args.user})", file=sys.stderr)
    print(f"目标: {args.goal_id}", file=sys.stderr)

    payload = dict(DRAFT_PAYLOAD)
    payload["goal_id"] = args.goal_id

    service, planner, instrumented = build_service(user_cfg)
    total_started = time.monotonic()
    draft = service.create_draft(payload)
    total_duration = time.monotonic() - total_started

    summary = {
        "account_email": email_label,
        "api_key": args.user,
        "goal_id": args.goal_id,
        "plan_id": draft.get("plan_id"),
        "generation_mode": draft.get("generation_mode"),
        "captured_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "total_duration_seconds": round(total_duration, 3),
        "ai_calls": [record.to_dict() for record in instrumented.calls],
    }
    if instrumented.calls:
        try:
            first_payload = json.loads(instrumented.calls[0].user_payload)
            summary["generation_id"] = (
                (first_payload.get("request") or {}).get("generation_id") or None
            )
        except Exception:
            pass
    if args.output == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # ── 文本报告 ──
    print("\n" + "=" * 72)
    print("草稿生成完成  plan_id=%s  总耗时 %.2fs" % (draft.get("plan_id"), total_duration))
    print("=" * 72)
    if not instrumented.calls:
        print("⚠️  没有发生 AI 调用（可能走了确定性兜底）")
        return
    grand = {"prompt_tokens_est": 0, "payload_tokens_est": 0, "total_tokens_est": 0}
    for call in instrumented.calls:
        s = call.sizes()
        for key in grand:
            grand[key] += s[key]
        print(f"\n── AI 调用 #{call.call_index}  {call.step} ──")
        print(f"  Skill        : {call.spec_name} ({call.output_model})")
        print(f"  调用时长     : {call.duration:.3f}s")
        print(f"  Prompt 大小  : {s['prompt_bytes']} B / 约 {s['prompt_tokens_est']} tokens"
              f"（字节/4 估 {s['prompt_tokens_est_byte_div4']}）")
        print(f"  Payload 大小 : {s['payload_bytes']} B / 约 {s['payload_tokens_est']} tokens"
              f"（字节/4 估 {s['payload_tokens_est_byte_div4']}）")
        print(f"  合计请求     : {s['total_bytes']} B / 约 {s['total_tokens_est']} tokens")
        print(f"  返回结果     : {s['result_bytes']} B")
        if call.error:
            print(f"  错误         : {call.error}")
        print(f"  ---- Prompt 内容 (system) ----")
        print(call.prompt)
        print(f"  ---- Payload 内容 (user, JSON) ----")
        print(call.user_payload)
    print("\n" + "=" * 72)
    print(f"两次 AI 调用合计: prompt 约 {grand['prompt_tokens_est']} tokens, "
          f"payload 约 {grand['payload_tokens_est']} tokens, "
          f"总计约 {grand['total_tokens_est']} tokens")


if __name__ == "__main__":
    main()
