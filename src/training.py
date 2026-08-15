"""交互式训练方案领域服务。

训练方案以 Markdown + YAML Front Matter 持久化。任何反馈都先形成待确认提案，
只有显式批准且基础版本仍匹配时，才会生成新的 active 版本。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable

from .coach_runtime import CoachRunContext
from .local_files import atomic_write_private, ensure_private_dir, read_private_text
from .memory import build_memory_file, parse_front_matter
from .training_analysis import natural_week_bounds
from .training_day_summary import TrainingDaySummaryBuilder
from .training_pace import (
    DailyPaceAdjustmentEngine,
    PaceCalibrationProfileBuilder,
)
from .training_planning import (
    PlanningFactPackBuilder,
    ProfessionalSchemePlanner,
    TrainingSchemeCandidateUnavailable,
    TrainingLoadEnvelope,
    _parse_fixed_unavailable,
    ensure_training_prescription,
    iter_leaf_workout_steps,
    workout_steps_summary,
)


WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
FEEDBACK_TYPES = {
    "constraint_change", "fatigue", "pain", "post_workout",
    # 兼容已存在的客户端；写入时统一归一化为 constraint_change。
    "time_limited", "schedule_conflict", "weather", "venue",
}
_CONSTRAINT_FEEDBACK_ALIASES = {
    "time_limited": "time_limited",
    "schedule_conflict": "schedule_conflict",
    "weather": "weather",
    "venue": "venue",
}


class TrainingError(ValueError):
    """带稳定错误码和 HTTP 语义的训练领域错误。"""

    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:32] or "running"


def _identifier(value: Any, *, field: str) -> str:
    identifier = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", identifier):
        raise TrainingError("invalid_identifier", f"{field} 格式无效")
    return identifier


def _facts_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


# 重规划注入当前方案时只保留结构字段，剔除旧结论/依据（data_basis、feasibility、
# review、adjustments 等）：AI 若看到旧 data_basis（如旧的长距离能力结论）会复述旧结论，
# 与重规划时刻实时计算的能力事实冲突；同时大幅减小载荷（active_scheme 曾达 180KB/45K tokens）。
_REVISION_SCHEME_STRUCTURE_KEYS = (
    "plan_id", "version", "goal_id", "goal_snapshot", "constraints",
    "coaching_mode", "periodization", "weekly_pattern", "first_four_weeks",
    "load_progression", "weekly_mileage_target", "current_phase",
    "activation", "effective_from", "created_at", "updated_at",
)


def _active_scheme_structure(scheme: dict[str, Any]) -> dict[str, Any]:
    """重规划时只向 AI 暴露当前方案的结构，不暴露历史结论字段。"""
    return {
        key: copy.deepcopy(scheme[key])
        for key in _REVISION_SCHEME_STRUCTURE_KEYS
        if key in scheme
    }


def _coerce_date(value: Any, *, field: str = "date") -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise TrainingError("invalid_date", f"{field} 必须使用 YYYY-MM-DD") from exc


def _scheme_effective_from(scheme: dict[str, Any]) -> date | None:
    """读取方案版本的本地生效日期，并兼容旧版本时间字段。"""
    for field in ("effective_from", "activated_at", "updated_at", "created_at"):
        value = scheme.get(field)
        if value in (None, ""):
            continue
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
    return None


def _scheme_effective_to(scheme: dict[str, Any]) -> date | None:
    value = scheme.get("effective_to")
    if value not in (None, ""):
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    if scheme.get("status") in {"completed", "archived", "superseded"}:
        for field in ("closed_at", "superseded_at", "updated_at"):
            terminal_value = scheme.get(field)
            if terminal_value in (None, ""):
                continue
            try:
                return date.fromisoformat(str(terminal_value)[:10])
            except ValueError:
                continue
    return None


def _coerce_days(values: Any) -> list[int]:
    if not isinstance(values, list):
        raise TrainingError("invalid_available_days", "available_days 必须是星期数字数组")
    try:
        days = sorted({int(item) for item in values})
    except (TypeError, ValueError) as exc:
        raise TrainingError("invalid_available_days", "available_days 只接受 0 到 6") from exc
    if not days or any(item < 0 or item > 6 for item in days):
        raise TrainingError("invalid_available_days", "至少选择一个训练日，值范围为 0 到 6")
    return days


def _is_trainable_weekday(weekday: int, constraints: dict[str, Any]) -> bool:
    """该星期几是否可训练：available_days 之外或 fixed_unavailable 的日期不可排课。

    无 available_days 约束时不限制（兼容旧草稿/兜底）。
    """
    if not constraints.get("available_days"):
        return True
    from .training_planning import _effective_available_days
    return weekday in _effective_available_days(constraints)


def _project_draft_near_term_schedule(
    weeks: list[dict[str, Any]], *, anchor: date,
    pace_profile: dict[str, Any] | None = None,
    recovery_snapshot: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    goal_context: dict[str, Any] | None = None,
    weather_context: str = "",
) -> list[dict[str, Any]]:
    """把 AI 的相对星期槽位投影为草稿预览使用的具体日期。

    AI 不负责生成 ISO 日期，避免用户修改生效日后出现日期与课程逻辑分离。
    该投影只读且不改变候选周次/课程内容；会为每节非休息课附上与草稿首周
    一致的确定性处方、当天配速安全边界和汇总的 ``pace_targets``。生效后仍由
    ``_materialize_week`` 生成带稳定 workout_id 的正式 Weekly Plan。
    天气文本（``weather_context``）来自用户补充信息，仅用于配速保守降级，
    不参与课程内容。
    """
    monday, _ = natural_week_bounds(anchor)
    projected: list[dict[str, Any]] = []
    for index, raw_week in enumerate(weeks[:2], start=1):
        if not isinstance(raw_week, dict):
            continue
        try:
            week_number = int(raw_week.get("week") or index)
        except (TypeError, ValueError):
            week_number = index
        week_start = monday + timedelta(days=(week_number - 1) * 7)
        week_end = week_start + timedelta(days=6)
        workouts: list[dict[str, Any]] = []
        for raw_workout in raw_week.get("workouts") or []:
            if not isinstance(raw_workout, dict):
                continue
            try:
                weekday = int(raw_workout.get("weekday", -1))
            except (TypeError, ValueError):
                continue
            if not 0 <= weekday <= 6:
                continue
            item = copy.deepcopy(raw_workout)
            workout_date = week_start + timedelta(days=weekday)
            item.update({
                "date": str(workout_date),
                "weekday_name": WEEKDAY_NAMES[weekday],
                "week_start": str(week_start),
                "week_end": str(week_end),
            })
            # 确定性校验：课程不得排在不可训练日（available_days 之外或 fixed_unavailable）
            if item.get("type") != "rest" and not _is_trainable_weekday(
                weekday, constraints or {},
            ):
                item = {
                    "date": str(workout_date),
                    "weekday_name": WEEKDAY_NAMES[weekday],
                    "week_start": str(week_start),
                    "week_end": str(week_end),
                    "weekday": weekday,
                    "title": "休息",
                    "type": "rest",
                    "purpose": "该日不可训练，课程已移至休息",
                    "is_key": False,
                }
            if item.get("type") != "rest":
                item = ensure_training_prescription(
                    item,
                    pace_reference_sec_per_km=(
                        ((pace_profile or {}).get("zones") or {}).get("z2", {})
                        .get("recent_median_sec_per_km")
                    ),
                    synthesize_steps=True,
                )
                if pace_profile:
                    item = DailyPaceAdjustmentEngine().apply(
                        item,
                        profile=pace_profile,
                        recovery_snapshot=recovery_snapshot or {},
                        constraints={
                            **(constraints or {}),
                            "weather": weather_context or "",
                        },
                        goal_context=goal_context or {},
                    )
                item = _attach_pace_targets(item)
            workouts.append(item)
        projected.append({
            "week": week_number,
            "week_start": str(week_start),
            "week_end": str(week_end),
            "target_km": raw_week.get("target_km"),
            "target_load": raw_week.get("target_load"),
            "focus": raw_week.get("focus"),
            "recovery_week": bool(raw_week.get("recovery_week")),
            "workouts": workouts,
        })
    return projected


def _attach_pace_targets(workout: dict[str, Any]) -> dict[str, Any]:
    """把确定性层的当天配速结论汇总到课程顶层 `pace_targets`，随草稿持久化。

    数值只来自 `DailyPaceAdjustmentEngine` 依据个人校准档案生成的结果，
    不在此处编造；无档案或降级时显式标记，不伪造依据。
    """
    prescription = workout.get("training_prescription")
    prescription = prescription if isinstance(prescription, dict) else {}
    guidance = prescription.get("pace_guidance")
    guidance = guidance if isinstance(guidance, dict) else {}
    today_target = guidance.get("today_target")
    today_target = today_target if isinstance(today_target, dict) else {}
    pace_target = prescription.get("targets")
    pace_target = (pace_target.get("pace") if isinstance(pace_target, dict) else None) or {}
    zone = prescription.get("intensity_zone")
    try:
        zone = int(zone) if zone is not None else None
    except (TypeError, ValueError):
        zone = None
    status = str(today_target.get("status") or pace_target.get("status") or "unavailable")
    if status in {"available", "slower", "progressed", "unchanged"}:
        workout["pace_targets"] = {
            "status": status,
            "zone": zone,
            "min_sec_per_km": today_target.get("min_sec_per_km"),
            "max_sec_per_km": today_target.get("max_sec_per_km"),
            "display_range": today_target.get("display_range"),
            "basis": pace_target.get("basis"),
            "feel_fallback": None,
        }
    else:
        explanation = guidance.get("explanation")
        explanation = explanation if isinstance(explanation, dict) else {}
        feel = (workout.get("intensity_intent") or {}).get("fallback_feel")
        workout["pace_targets"] = {
            "status": "feel_only" if status == "feel_only" else "unavailable",
            "zone": zone,
            "min_sec_per_km": None,
            "max_sec_per_km": None,
            "display_range": None,
            "basis": pace_target.get("basis") or str(explanation.get("summary") or ""),
            "feel_fallback": feel,
        }
    return workout


def _coerce_goal_intent(value: Any, *, target_time: Any = None) -> str:
    """Normalize the product-level success intent for legacy goals."""
    raw = str(value or "").strip().lower()
    if not raw:
        return "performance" if str(target_time or "").strip() else "completion"
    if raw not in {"completion", "performance"}:
        raise TrainingError(
            "invalid_goal_intent",
            "goal_intent 只能是 completion（完赛）或 performance（成绩突破）",
        )
    return raw


def _read_document(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    front_matter, body = parse_front_matter(read_private_text(path))
    if not isinstance(front_matter, dict):
        return None
    result = copy.deepcopy(front_matter)
    result["_body"] = body.strip()
    return result


def _document_body(item: dict[str, Any]) -> str:
    kind = item.get("type")
    if kind == "training_scheme":
        goal = item.get("goal_snapshot") or item.get("goal", {})
        return (
            f"# {goal.get('name', '训练方案')}\n\n"
            f"当前阶段：{item.get('current_phase', {}).get('name', '基础期')}\n\n"
            "此文件由训练方案服务维护；结构化字段是运行时真相源。"
        )
    if kind == "weekly_plan":
        return f"# {item.get('week_start')} 本周安排\n\n由训练方案版本自动生成。"
    if kind == "training_feedback":
        return f"# 训练反馈\n\n{item.get('note') or '用户提交了结构化反馈。'}"
    if kind == "adjustment_proposal":
        return f"# 调整提案\n\n{item.get('reason', '')}"
    if kind == "coaching_context":
        return f"# 教练模式\n\n当前模式：{item.get('coaching_mode', 'continuous_running')}"
    return item.get("_body", "")


def _write_document(path: Path, item: dict[str, Any]) -> None:
    payload = {key: value for key, value in item.items() if not key.startswith("_")}
    atomic_write_private(path, build_memory_file(payload, _document_body(item)))


class TrainingGoalRepository:
    """Training Goal 的唯一文件真相源。"""

    def __init__(self, memory_dir: str | Path):
        self.root = Path(memory_dir) / "goals"
        self.active = self.root / "active"
        self.archived = self.root / "archived"

    @staticmethod
    def _name(item: dict[str, Any], fallback: str) -> str:
        if item.get("title"):
            return str(item["title"])
        body = str(item.get("_body") or "")
        heading = next(
            (line[2:].strip() for line in body.splitlines() if line.startswith("# ")),
            "",
        )
        return heading or fallback

    @classmethod
    def _normalize(cls, item: dict[str, Any], fallback_id: str) -> dict[str, Any]:
        goal_id = str(item.get("id") or fallback_id)
        metrics = copy.deepcopy(item.get("metrics") or {})
        distance = str(item.get("distance") or "")
        if not distance:
            distance = next(
                (key.removeprefix("target_") for key in metrics if key.startswith("target_")),
                "general",
            )
        target_time = item.get("target_time")
        if target_time is None:
            target_time = metrics.get(f"target_{distance}")
        goal_intent = _coerce_goal_intent(
            item.get("goal_intent"), target_time=target_time,
        )
        return {
            "goal_id": goal_id,
            "id": goal_id,
            "name": cls._name(item, goal_id),
            "title": cls._name(item, goal_id),
            "goal_type": str(item.get("goal_type") or "time_based"),
            "distance": distance,
            "goal_intent": goal_intent,
            "target_time": str(target_time or "") or None,
            "target_date": str(item.get("target_date") or "") or None,
            "status": str(item.get("status") or "active"),
            "priority": str(item.get("priority") or "high"),
            "created": str(item.get("created") or ""),
            "metrics": metrics,
        }

    def list_active(self) -> list[dict[str, Any]]:
        goals = []
        for path in sorted(self.active.glob("*.md")):
            item = _read_document(path)
            if item and item.get("type") == "goal" and item.get("status", "active") == "active":
                goals.append(self._normalize(item, path.stem))
        return goals

    def get(self, goal_id: str) -> dict[str, Any] | None:
        goal_id = _identifier(goal_id, field="goal_id")
        item = _read_document(self.active / f"{goal_id}.md")
        if not item or item.get("type") != "goal" or item.get("status", "active") != "active":
            return None
        return self._normalize(item, goal_id)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise TrainingError("goal_required", "请填写训练目标")
        distance = str(payload.get("distance") or "general").strip().lower()
        if distance not in {"5k", "10k", "hm", "marathon", "general"}:
            raise TrainingError("invalid_goal_distance", "目标距离不受支持")
        target_date = str(payload.get("target_date") or "").strip()
        if target_date:
            _coerce_date(target_date, field="target_date")
        target_time = str(payload.get("target_time") or "").strip()
        goal_intent = _coerce_goal_intent(
            payload.get("goal_intent"), target_time=target_time,
        )
        if goal_intent == "performance" and not target_time:
            raise TrainingError(
                "performance_target_required",
                "成绩突破目标需要填写目标成绩",
            )
        goal_id = f"goal-{_slug(name)}-{uuid.uuid4().hex[:8]}"
        metrics = {f"target_{distance}": target_time}
        item = {
            "type": "goal", "id": goal_id, "title": name,
            "goal_type": "time_based" if target_time else "distance_based",
            "goal_intent": goal_intent,
            "category": "running", "status": "active", "priority": "high",
            "created": str(date.today()), "updated": _now(),
            "target_date": target_date or None, "distance": distance,
            "target_time": target_time or None, "review_cycle": "weekly",
            "metrics": metrics, "tags": [distance, str(date.today().year), "active"],
        }
        body = (
            f"# {name}\n\n## 目标\n- 距离: {distance}\n"
            f"- 目标意图: {goal_intent}\n"
            f"- 目标成绩: {target_time or '—'}\n"
            f"- 截止日期: {target_date or '—'}\n"
        )
        atomic_write_private(
            self.active / f"{goal_id}.md", build_memory_file(item, body),
        )
        return self._normalize(item, goal_id)

    def update(self, goal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        goal_id = _identifier(goal_id, field="goal_id")
        path = self.active / f"{goal_id}.md"
        item = _read_document(path)
        if not item:
            raise TrainingError("goal_not_found", "训练目标不存在", status_code=404)
        current = self._normalize(item, goal_id)
        name = str(payload.get("name") or current["name"]).strip()
        distance = str(payload.get("distance") or current["distance"]).lower()
        if not name:
            raise TrainingError("goal_required", "请填写训练目标")
        if distance not in {"5k", "10k", "hm", "marathon", "general"}:
            raise TrainingError("invalid_goal_distance", "目标距离不受支持")
        target_time = str(
            payload.get("target_time")
            if payload.get("target_time") is not None else current.get("target_time") or ""
        ).strip()
        goal_intent = _coerce_goal_intent(
            payload.get("goal_intent")
            if payload.get("goal_intent") is not None
            else current.get("goal_intent"),
            target_time=target_time,
        )
        if goal_intent == "performance" and not target_time:
            raise TrainingError(
                "performance_target_required",
                "成绩突破目标需要填写目标成绩",
            )
        target_date = str(
            payload.get("target_date")
            if payload.get("target_date") is not None else current.get("target_date") or ""
        ).strip()
        if target_date:
            _coerce_date(target_date, field="target_date")
        metrics = {f"target_{distance}": target_time}
        item.update({
            "id": goal_id, "title": name, "distance": distance,
            "goal_intent": goal_intent,
            "target_time": target_time or None, "target_date": target_date or None,
            "metrics": metrics, "status": str(payload.get("status") or item.get("status") or "active"),
            "updated": _now(),
        })
        item["_body"] = (
            f"# {name}\n\n## 目标\n- 距离: {distance}\n"
            f"- 目标意图: {goal_intent}\n"
            f"- 目标成绩: {target_time or '—'}\n"
            f"- 截止日期: {target_date or '—'}\n"
        )
        _write_document(path, item)
        return self._normalize(item, goal_id)

    def archive(self, goal_id: str) -> None:
        goal_id = _identifier(goal_id, field="goal_id")
        path = self.active / f"{goal_id}.md"
        item = _read_document(path)
        if not item:
            raise TrainingError("goal_not_found", "训练目标不存在", status_code=404)
        item["status"] = "archived"
        item["updated"] = _now()
        ensure_private_dir(self.archived)
        _write_document(self.archived / f"{goal_id}.md", item)
        path.unlink()


class TrainingRepository:
    """用户隔离的训练方案文件仓库。"""

    def __init__(self, memory_dir: str | Path):
        self.root = Path(memory_dir)
        self.plans = self.root / "plans"
        self.active_path = self.plans / "active-plan.md"
        self.coaching_context_path = self.plans / "coaching-context.md"
        self.activation_previews = self.plans / "activation-previews"
        self.rescheduling_previews = self.plans / "rescheduling-previews"
        self.activations = self.plans / "activations"
        self.scheduled = self.plans / "scheduled"
        self.progression = self.plans / "progression"
        self.capacity_facts_path = self.root / "profile" / "capacity-facts.md"
        self.weekly_reports = self.root / "reports" / "weekly"

    def get_active(self, *, migrate_legacy: bool = True) -> dict[str, Any] | None:
        active = _read_document(self.active_path)
        if active and active.get("type") != "training_scheme" and migrate_legacy:
            return self._migrate_legacy(active)
        return active

    def _migrate_legacy(self, legacy: dict[str, Any]) -> dict[str, Any]:
        """将旧 active-plan 原子迁移为 v1，并完整保留旧正文。"""
        with _lock_for(self.root):
            current = _read_document(self.active_path)
            if current and current.get("type") == "training_scheme":
                return current
            current = current or legacy
            today = date.today()
            raw_weekly = current.get("weekly_structure") or {}
            pattern = []
            for index, name in enumerate(WEEKDAY_NAMES):
                raw = raw_weekly.get(name) or raw_weekly.get(name[-1])
                pattern.append({
                    "weekday": index,
                    "title": str(raw or "休息"),
                    "type": "rest" if not raw or "休" in str(raw) else "easy",
                    "purpose": "沿用旧训练方案安排",
                    "duration_minutes": None,
                    "distance_km": None,
                    "intensity": "以原方案正文为准",
                    "reason": "由旧 active-plan.md 迁移",
                    "is_key": False,
                })
            plan_id = f"legacy-{today:%Y%m%d}"
            scheme = {
                "type": "training_scheme",
                "plan_id": plan_id,
                "version": 1,
                "status": "active",
                "created_at": current.get("created") or current.get("updated") or _now(),
                "updated_at": _now(),
                "activated_at": current.get("updated") or current.get("created") or _now(),
                "effective_from": str(
                    _scheme_effective_from(current) or date.today()
                ),
                "effective_to": None,
                "goal": {
                    "name": current.get("target_race") or current.get("goal_id") or "跑步训练目标",
                    "target_time": current.get("target_time"),
                    "target_date": str(current.get("target_date") or ""),
                },
                "constraints": {"available_days": [], "max_session_minutes": None},
                "current_phase": {
                    "name": current.get("current_phase") or "基础期",
                    "purpose": "逐步建立与目标匹配的训练能力",
                },
                "weekly_mileage_target": current.get("weekly_mileage_target"),
                "weekly_pattern": pattern,
                "week_overrides": {},
                "safety_guardrails": {"pain_requires_reduction": True},
                "legacy_notes": current.get("_body", ""),
                "migration": {"source": "legacy-active-plan", "migrated_at": _now()},
            }
            self.save_scheme(scheme)
            return scheme

    def get_draft(self, plan_id: str) -> dict[str, Any] | None:
        plan_id = _identifier(plan_id, field="plan_id")
        return _read_document(self.plans / "drafts" / f"{plan_id}.md")

    def save_draft(self, scheme: dict[str, Any]) -> None:
        _write_document(self.plans / "drafts" / f"{scheme['plan_id']}.md", scheme)

    def supersede_other_drafts(self, keep_plan_id: str) -> int:
        """把其他 draft 状态的草稿标记为 superseded（保留文件便于回溯），返回处理数。"""
        superseded = 0
        for path in (self.plans / "drafts").glob("*.md"):
            item = _read_document(path)
            if not item or item.get("status") != "draft":
                continue
            plan_id = str(item.get("plan_id") or "")
            if plan_id == keep_plan_id:
                continue
            item["status"] = "superseded"
            item["superseded_by"] = keep_plan_id
            item["updated_at"] = _now()
            _write_document(path, item)
            superseded += 1
        return superseded

    def list_drafts(self) -> list[dict[str, Any]]:
        drafts = []
        for path in (self.plans / "drafts").glob("*.md"):
            item = _read_document(path)
            if item and item.get("type") == "training_scheme":
                drafts.append(item)
        return sorted(drafts, key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)

    def save_activation_preview(self, preview: dict[str, Any]) -> None:
        _write_document(self.activation_previews / f"{preview['preview_id']}.md", preview)

    def get_activation_preview(self, preview_id: str) -> dict[str, Any] | None:
        preview_id = _identifier(preview_id, field="preview_id")
        return _read_document(self.activation_previews / f"{preview_id}.md")

    def save_rescheduling_preview(self, preview: dict[str, Any]) -> None:
        _write_document(self.rescheduling_previews / f"{preview['preview_id']}.md", preview)

    def get_rescheduling_preview(self, preview_id: str) -> dict[str, Any] | None:
        preview_id = _identifier(preview_id, field="preview_id")
        return _read_document(self.rescheduling_previews / f"{preview_id}.md")

    def save_activation(self, activation: dict[str, Any]) -> None:
        _write_document(self.activations / f"{activation['activation_id']}.md", activation)

    def save_scheduled(self, scheme: dict[str, Any]) -> None:
        _write_document(self.scheduled / f"{scheme['plan_id']}.md", scheme)

    def get_scheduled(self, plan_id: str) -> dict[str, Any] | None:
        plan_id = _identifier(plan_id, field="plan_id")
        return _read_document(self.scheduled / f"{plan_id}.md")

    def list_scheduled(self) -> list[dict[str, Any]]:
        result = []
        for path in self.scheduled.glob("*.md"):
            item = _read_document(path)
            if item and item.get("type") == "training_scheme" and item.get("status") == "scheduled":
                result.append(item)
        return sorted(result, key=lambda item: item.get("effective_from") or "")

    def remove_scheduled(self, plan_id: str) -> None:
        path = self.scheduled / f"{_identifier(plan_id, field='plan_id')}.md"
        if path.exists():
            path.unlink()

    def get_capacity_facts(self) -> dict[str, Any]:
        facts = _read_document(self.capacity_facts_path)
        return facts if facts and facts.get("type") == "athlete_capacity_facts" else {
            "type": "athlete_capacity_facts", "updated_at": None,
        }

    def save_capacity_facts(self, facts: dict[str, Any]) -> None:
        _write_document(self.capacity_facts_path, facts)

    def save_progression_decision(self, decision: dict[str, Any]) -> None:
        _write_document(self.progression / f"{decision['decision_id']}.md", decision)

    def save_weekly_report(self, report: dict[str, Any]) -> None:
        _write_document(self.weekly_reports / f"{report['week_id']}.md", report)

    def get_weekly_report(self, week_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"\d{4}-W\d{2}", str(week_id)):
            raise TrainingError("invalid_week_id", "week_id 必须使用 YYYY-Www")
        return _read_document(self.weekly_reports / f"{week_id}.md")

    def list_weekly_reports(self) -> list[dict[str, Any]]:
        reports = []
        for path in self.weekly_reports.glob("*.md"):
            report = _read_document(path)
            if report and report.get("type") == "weekly_report":
                reports.append(report)
        return sorted(reports, key=lambda item: item.get("week_start") or "", reverse=True)

    def save_scheme(self, scheme: dict[str, Any]) -> None:
        scheme = copy.deepcopy(scheme)
        plan_id = scheme["plan_id"]
        version = int(scheme["version"])
        effective_from = _scheme_effective_from(scheme) or date.today()
        scheme.setdefault("activated_at", scheme.get("updated_at") or _now())
        scheme["effective_from"] = str(effective_from)
        scheme.setdefault("effective_to", None)

        current = self.get_active(migrate_legacy=False)
        if (
            current
            and current.get("type") == "training_scheme"
            and current.get("plan_id") == plan_id
            and int(current.get("version") or 0) < version
        ):
            superseded = copy.deepcopy(current)
            superseded["status"] = "superseded"
            superseded["effective_to"] = str(effective_from - timedelta(days=1))
            superseded["superseded_at"] = scheme["activated_at"]
            _write_document(
                self.plans / "history" / f"{plan_id}-v{int(current['version'])}.md",
                superseded,
            )
        _write_document(self.plans / "history" / f"{plan_id}-v{version}.md", scheme)
        _write_document(self.active_path, scheme)

    def list_scheme_versions(self) -> list[dict[str, Any]]:
        """列出可用于按日期解析的方案版本。"""
        versions: dict[tuple[str, int], dict[str, Any]] = {}
        for path in sorted((self.plans / "history").glob("*-v*.md")):
            item = _read_document(path)
            if not item or item.get("type") != "training_scheme":
                continue
            key = (str(item.get("plan_id") or ""), int(item.get("version") or 0))
            versions[key] = item
        active = self.get_active(migrate_legacy=False)
        if active and active.get("type") == "training_scheme":
            key = (str(active.get("plan_id") or ""), int(active.get("version") or 0))
            versions[key] = active
        return list(versions.values())

    def close_active_scheme(self, *, reason: str) -> dict[str, Any] | None:
        active = self.get_active()
        if not active:
            return None
        closed = copy.deepcopy(active)
        closed["version"] = int(active.get("version") or 0) + 1
        closed["status"] = "completed"
        closed["updated_at"] = _now()
        closed["closed_at"] = _now()
        # 作废即昨天结束：当天起 resolve 不再选中该方案，训练页回到无方案引导
        closed["effective_to"] = str(date.today() - timedelta(days=1))
        closed["closure_reason"] = reason
        _write_document(
            self.plans / "history" / f"{closed['plan_id']}-v{closed['version']}.md",
            closed,
        )
        # 历史中同方案仍为 active/scheduled 的旧版本一并标记 superseded，
        # 避免 resolve 按日期解析时重新选中已作废方案。
        for path in (self.plans / "history").glob("*.md"):
            item = _read_document(path)
            if not (
                item
                and item.get("plan_id") == closed.get("plan_id")
                and item.get("status") in {"active", "scheduled"}
            ):
                continue
            item["status"] = "superseded"
            item["superseded_at"] = _now()
            item["effective_to"] = str(date.today() - timedelta(days=1))
            _write_document(path, item)
        # 作废时把该方案未决提案标记为 superseded，避免重新制定方案后残留待确认
        for path in (self.plans / "proposals").glob("*.md"):
            item = _read_document(path)
            if (
                item
                and item.get("plan_id") == closed.get("plan_id")
                and item.get("status") == "pending"
            ):
                item["status"] = "superseded"
                item["superseded_at"] = _now()
                _write_document(path, item)
        if self.active_path.exists():
            self.active_path.unlink()
        return closed

    def get_coaching_context(self) -> dict[str, Any]:
        """教练模式由生效方案派生：有赛事备赛方案 → race_preparation；否则 continuous_running。

        不再持久化 coaching-context.md，也不依赖模式转换提案；旧文件仅作读取兼容。
        """
        active = self.get_active(migrate_legacy=False)
        mode = (
            "race_preparation"
            if active and active.get("goal_snapshot")
            else "continuous_running"
        )
        return {
            "type": "coaching_context",
            "coaching_mode": mode,
            "updated_at": None,
            "last_transition": None,
        }

    def save_week(self, week: dict[str, Any]) -> None:
        filename = f"{week['plan_id']}-{week['week_id']}.md"
        _write_document(self.plans / "weeks" / filename, week)
        versioned = (
            f"{week['plan_id']}-v{int(week['scheme_version'])}-{week['week_id']}.md"
        )
        _write_document(self.plans / "weeks/history" / versioned, week)

    def save_feedback(self, feedback: dict[str, Any]) -> None:
        filename = f"{feedback['target_date']}-{feedback['feedback_id']}.md"
        _write_document(self.plans / "feedback" / filename, feedback)

    def get_feedback(self, feedback_id: str) -> dict[str, Any] | None:
        feedback_id = _identifier(feedback_id, field="feedback_id")
        matches = list((self.plans / "feedback").glob(f"*-{feedback_id}.md"))
        return _read_document(matches[0]) if matches else None

    def feedback_for_date(self, target_date: date) -> list[dict[str, Any]]:
        result = []
        for path in (self.plans / "feedback").glob(f"{target_date}-*.md"):
            item = _read_document(path)
            if item and item.get("type") == "training_feedback":
                result.append(item)
        return sorted(result, key=lambda item: item.get("created_at") or "", reverse=True)

    def feedback_by_idempotency(
        self, plan_id: str, idempotency_key: str,
    ) -> dict[str, Any] | None:
        for path in (self.plans / "feedback").glob("*.md"):
            item = _read_document(path)
            if item and item.get("plan_id") == plan_id and item.get("idempotency_key") == idempotency_key:
                return item
        return None

    def save_proposal(self, proposal: dict[str, Any]) -> None:
        _write_document(self.plans / "proposals" / f"{proposal['proposal_id']}.md", proposal)

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        proposal_id = _identifier(proposal_id, field="proposal_id")
        return _read_document(self.plans / "proposals" / f"{proposal_id}.md")

    def pending_proposals(self, plan_id: str) -> list[dict[str, Any]]:
        result = []
        for path in (self.plans / "proposals").glob("*.md"):
            item = _read_document(path)
            if item and item.get("plan_id") == plan_id and item.get("status") == "pending":
                result.append(item)
        return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)

    def supersede_pending_proposals(
        self, plan_id: str, scope: str, target_date: str | None = None,
    ) -> int:
        """把同方案同范围（可选同日）的其他 pending 提案标记为 superseded
        （保留文件便于回溯），保证同一调整范围只保留最新一个待决策提案。
        scheme 级（重规划）全局互斥；today 级按 target_date 区分。"""
        count = 0
        for path in (self.plans / "proposals").glob("*.md"):
            item = _read_document(path)
            if not (
                item
                and item.get("plan_id") == plan_id
                and item.get("scope") == scope
                and item.get("status") == "pending"
            ):
                continue
            if target_date is not None and item.get("target_date") != target_date:
                continue
            item["status"] = "superseded"
            item["superseded_at"] = _now()
            _write_document(path, item)
            count += 1
        return count

    def list_adjustment_records(self) -> list[dict[str, Any]]:
        """Return terminal adjustment proposals as immutable history records."""
        records = []
        terminal_statuses = {"approved", "rejected", "expired"}
        for path in (self.plans / "proposals").glob("*.md"):
            item = _read_document(path)
            if not item or item.get("type") != "adjustment_proposal":
                continue
            if item.get("status") not in terminal_statuses:
                continue
            records.append(item)
        return sorted(
            records,
            key=lambda item: (
                item.get("approved_at") or item.get("rejected_at")
                or item.get("expired_at") or item.get("created_at") or ""
            ),
            reverse=True,
        )


class ActivityMatcher:
    """保守关联计划课次和同日实际活动；缺数据时保持 unmatched。"""

    @staticmethod
    def match(
        workouts: list[dict[str, Any]],
        activities: list[dict[str, Any]],
        activity_states: dict[str, str],
        today: date,
    ) -> list[dict[str, Any]]:
        by_date: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for activity in activities:
            activity_id = str(activity.get("activity_id") or "")
            if activity_id and activity_id in seen:
                continue
            if activity_id:
                seen.add(activity_id)
            key = str(activity.get("activity_date") or activity.get("start_time") or "")[:10]
            if key:
                by_date.setdefault(key, []).append(activity)

        matched = []
        for workout in workouts:
            item = copy.deepcopy(workout)
            key = item["date"]
            candidates = by_date.get(key, [])
            # 第一版只自动关联同日、单活动且高置信度的一项；同日其它活动
            # 仍是事实，但不拼接到计划课次，也不被判作替代完成。
            def confidence(activity: dict[str, Any]) -> float:
                analysis = activity.get("training_analysis")
                if not isinstance(analysis, dict) or analysis.get("confidence") in (None, ""):
                    return 1.0  # 旧活动没有分析字段时保持兼容
                try:
                    return float(analysis.get("confidence") or 0)
                except (TypeError, ValueError):
                    return 0.0

            actual = []
            if candidates:
                candidate = max(candidates, key=confidence)
                if confidence(candidate) >= 0.6:
                    actual = [candidate]
            workout_date = date.fromisoformat(key)
            if item.get("type") == "rest":
                actual = []
                item["execution_status"] = "unmatched"
            elif actual:
                planned_distance = float(item.get("distance_km") or 0)
                planned_duration = float(item.get("duration_minutes") or 0)
                actual_distance = float(actual[0].get("distance_meters") or 0) / 1000
                actual_duration = float(actual[0].get("duration_seconds") or 0) / 60
                materially_short = bool(
                    isinstance(actual[0].get("training_analysis"), dict)
                    and (
                        (planned_distance > 0 and actual_distance < planned_distance * 0.7)
                        or (planned_duration > 0 and actual_duration < planned_duration * 0.7)
                    )
                )
                item["execution_status"] = (
                    "partially_completed" if materially_short else "completed"
                )
            elif workout_date > today:
                item["execution_status"] = "planned"
            elif activity_states.get(key) == "unknown":
                item["execution_status"] = "unmatched"
            elif workout_date == today:
                item["execution_status"] = "planned"
            else:
                item["execution_status"] = "unmatched"
            item["actual_activities"] = [{
                "activity_id": activity.get("activity_id"),
                "name": activity.get("activity_name") or "实际活动",
                "distance_km": round(float(activity.get("distance_meters") or 0) / 1000, 2),
                "duration_minutes": round(float(activity.get("duration_seconds") or 0) / 60),
                "training_analysis": (
                    {
                        key: copy.deepcopy(value)
                        for key, value in activity["training_analysis"].items()
                        if key != "features"
                    }
                    if isinstance(activity.get("training_analysis"), dict)
                    else activity.get("training_analysis")
                ),
            } for activity in actual]
            matched.append(item)
        return matched


class TrainingService:
    """训练首页、方案、反馈和提案的统一业务入口。"""

    def __init__(
        self,
        memory_dir: str | Path,
        *,
        activity_loader: Callable[[date, date], tuple[list[dict[str, Any]], dict[str, str]]] | None = None,
        setup_context_loader: Callable[..., dict[str, Any]] | None = None,
        scheme_planner: ProfessionalSchemePlanner | None = None,
        pace_profile_builder: PaceCalibrationProfileBuilder | None = None,
        pace_adjustment_engine: DailyPaceAdjustmentEngine | None = None,
        platform_threshold_loader: Callable[[date], dict[str, Any]] | None = None,
    ):
        self.repository = TrainingRepository(memory_dir)
        self.goals = TrainingGoalRepository(memory_dir)
        self.activity_loader = activity_loader
        self.setup_context_loader = setup_context_loader
        self.scheme_planner = scheme_planner or ProfessionalSchemePlanner()
        self.pace_profile_builder = pace_profile_builder or PaceCalibrationProfileBuilder()
        self.pace_adjustment_engine = pace_adjustment_engine or DailyPaceAdjustmentEngine()
        self.platform_threshold_loader = platform_threshold_loader
        self._lock = _lock_for(self.repository.root)

    def list_goals(self) -> list[dict[str, Any]]:
        return self.goals.list_active()

    def create_goal(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.repository.get_active(migrate_legacy=False)
            drafts = self.repository.list_drafts()
            scheduled = self.repository.list_scheduled()
            if current or drafts or scheduled:
                raise TrainingError(
                    "goal_creation_blocked",
                    "已有训练方案或待确认草稿；请在当前目标下调整，不要创建新的目标",
                    status_code=409,
                )
        return self.goals.create(payload)

    def update_goal(self, goal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.repository.get_active(migrate_legacy=False)
        if current and current.get("goal_id") == goal_id:
            current_goal = current.get("goal_snapshot") or {}
            changing = any(
                str(payload.get(field) or "") != str(current_goal.get(field) or "")
                for field in (
                    "name", "distance", "goal_intent", "target_time", "target_date",
                )
                if field in payload
            )
            if changing:
                raise TrainingError(
                    "goal_change_requires_preview",
                    "生效方案的目标变化需要先生成改期预览并确认",
                    status_code=409,
                )
        return self.goals.update(goal_id, payload)

    def archive_goal(self, goal_id: str) -> None:
        self.goals.archive(goal_id)

    def close_active_scheme(self, *, reason: str) -> dict[str, Any] | None:
        """作废当前方案：标记 completed 并归档，回到无方案引导。"""
        with self._lock:
            return self.repository.close_active_scheme(reason=reason)

    def _athlete_profile_document(
        self, target: date | None = None,
    ) -> dict[str, Any] | None:
        """读取个人档案 Front Matter（fitness-assessment），供阈值/能力锚定使用。

        档案缺失显式阈值时并入平台自算乳酸阈值（Coros lthr/ltsp），优先级：
        用户档案显式阈值 > 平台 lthr/ltsp > 档案推导（Karvonen/PB）> 无。
        """
        try:
            from .memory import MemoryStore
            memory = MemoryStore(self.repository.root)
            profile_mem = memory.get("fitness-assessment")
            profile = profile_mem.front_matter if profile_mem else None
            if (
                profile is not None
                and self.platform_threshold_loader is not None
                and target is not None
            ):
                try:
                    thresholds = self.platform_threshold_loader(target)
                    for key in ("threshold_heart_rate", "threshold_pace_sec_per_km"):
                        if not profile.get(key) and thresholds.get(key):
                            profile[key] = thresholds[key]
                except Exception:
                    pass
            return profile
        except Exception:
            return None

    def _setup_context(self, *, target: date | None = None) -> dict[str, Any]:
        context: dict[str, Any] = {}
        if self.setup_context_loader:
            try:
                try:
                    loaded = (
                        self.setup_context_loader(target)
                        if target is not None else self.setup_context_loader()
                    )
                except TypeError:
                    loaded = self.setup_context_loader()
                if isinstance(loaded, dict):
                    context = loaded
            except Exception:
                context = {}
        baseline = context.get("baseline") if isinstance(context.get("baseline"), dict) else {}
        baseline = {
            "coverage": str(baseline.get("coverage") or "unknown"),
            "window_days": int(baseline.get("window_days") or 28),
            "activity_count": int(baseline.get("activity_count") or 0),
            "distance_km": round(float(baseline.get("distance_km") or 0), 1),
            "longest_distance_km": round(float(baseline.get("longest_distance_km") or 0), 1),
            "previous_week_km": round(float(baseline.get("previous_week_km") or 0), 1),
            "average_weekly_km": round(float(baseline.get("average_weekly_km") or 0), 1),
            "recent_7d_km": round(float(baseline.get("recent_7d_km") or 0), 1),
            "reference_window_kind": str(
                baseline.get("reference_window_kind")
                or "previous_completed_natural_week"
            ),
            "reference_window_start": baseline.get("reference_window_start"),
            "reference_window_end": baseline.get("reference_window_end"),
        }
        return {
            "active_goals": self.goals.list_active(),
            "baseline": baseline,
            "known_constraints": copy.deepcopy(context.get("known_constraints") or {}),
            "athlete_profile": copy.deepcopy(context.get("athlete_profile") or {}),
            "recovery_snapshot": copy.deepcopy(context.get("recovery_snapshot") or {}),
            "short_term_training": copy.deepcopy(context.get("short_term_training") or {}),
        }

    def _recent_two_days_training(self, *, target: date) -> dict[str, Any]:
        """读取最近两个已结束自然日，仅用于课程衔接，不作为周量基线。"""
        start = target - timedelta(days=2)
        end = target - timedelta(days=1)
        if not self.activity_loader:
            return {
                "status": "unknown",
                "window_start": str(start), "window_end": str(end),
                "activities": [], "constraints": {"avoid_quality_after_heavy": True},
            }
        try:
            activities, states = self.activity_loader(start, end)
        except Exception:
            activities, states = [], {}
        running: list[dict[str, Any]] = []
        quality_tokens = (
            "间歇", "节奏", "阈值", "变速", "tempo", "interval", "fartlek", "race pace",
        )
        for item in activities or []:
            if not (
                "run" in str(item.get("activity_type") or "").lower()
                or "跑" in str(item.get("activity_name") or "")
            ):
                continue
            try:
                activity_date = date.fromisoformat(
                    str(item.get("activity_date") or item.get("date") or item.get("start_time"))[:10]
                )
            except (TypeError, ValueError):
                continue
            if not start <= activity_date <= end:
                continue
            distance = round(max(0.0, float(item.get("distance_meters") or 0) / 1000), 1)
            title = str(item.get("activity_name") or item.get("name") or "跑步")
            lowered = title.lower()
            is_quality = any(token in lowered for token in quality_tokens)
            running.append({
                "date": str(activity_date),
                "title": title,
                "distance_km": distance,
                "duration_minutes": round(float(item.get("duration_seconds") or 0) / 60),
                "is_long": distance >= 20.0,
                "is_quality": is_quality,
            })
        covered_days = sum(
            1 for offset in range(2)
            if str(start + timedelta(days=offset)) in (states or {})
        )
        return {
            "status": "sufficient" if covered_days >= 2 else "partial" if covered_days else "unknown",
            "window_start": str(start),
            "window_end": str(end),
            "covered_days": covered_days,
            "activities": sorted(running, key=lambda item: item["date"]),
            "constraints": {
                "avoid_quality_after_heavy": True,
                "prefer_long_run_weekend": True,
            },
        }

    @staticmethod
    def _compact_day_session_summaries(
        day: dict[str, Any], *, baseline: Any = None,
    ) -> dict[str, Any]:
        """消费侧压缩：草稿/周报只携带 session-summary 的规划概要子集。

        数据源仍是统一 ``activity_summary_facts``，这里只投影字段以减小事实包；
        日报保持全量消费，不产生第二套结构。传入 ``baseline``（个人阈值）时
        对每课做确定性结构判定（与日报同口径），供草稿/周报 SKILL 引用。
        """
        from .summary_extraction import (
            classify_training_structure,
            compact_session_summary_for_planning,
        )

        sessions = day.get("sessions") or []
        compacted = []
        structure_features: list[dict[str, Any]] = []
        for session in sessions:
            item = dict(session)
            if item.get("session_summary"):
                item["session_summary"] = compact_session_summary_for_planning(
                    item["session_summary"]
                )
                if baseline is not None:
                    sequence = (
                        item["session_summary"].get("segment_sequence") or []
                    )
                    if sequence:
                        classification = classify_training_structure(
                            sequence,
                            threshold_heart_rate=baseline.threshold_heart_rate,
                            threshold_pace_sec_per_km=(
                                baseline.threshold_pace_sec_per_km
                            ),
                            quantity_reliable=bool(
                                (
                                    item["session_summary"].get("quantity_gate")
                                    or {}
                                ).get("quantity_reliable", True)
                            ),
                        )
                        item["structure_classification"] = classification
                        # 结构判定并入特点层，草稿/周报的 feature 聚合可见
                        structure_type = str(
                            classification.get("structure_type") or "unknown"
                        )
                        if structure_type != "unknown":
                            alternations = classification.get("alternations") or 0
                            label = str(classification.get("label") or structure_type)
                            structure_features.append({
                                "feature_code": f"structure_{structure_type}",
                                "label": (
                                    f"{label}（{alternations} 组快慢交替）"
                                    if alternations else label
                                ),
                                "confidence": float(
                                    classification.get("confidence") or 0
                                ),
                                "evidence": [{
                                    "metric": "structure_classification.alternations",
                                    "value": alternations,
                                    "source": "classify_training_structure",
                                }],
                            })
            compacted.append(item)
        if structure_features:
            day = {
                **day,
                "observed_features": list(day.get("observed_features") or [])
                + structure_features,
            }
        return {**day, "sessions": compacted}

    @staticmethod
    def _weekly_prerequisite_manifest(
        daily_summaries: list[dict[str, Any]], *, week_start: date,
        observed_through: date,
    ) -> dict[str, Any]:
        """Seal the exact daily fact/analysis versions consumed by a week review."""
        entries = []
        quality_refs = []
        for day in daily_summaries:
            analysis = day.get("daily_analysis") or {}
            quality = analysis.get("quality_sessions") or []
            entries.append({
                "date": day.get("date"),
                "fact_version": day.get("fact_version") or day.get("summary_version"),
                "analysis_version": analysis.get("analysis_version"),
                "status": analysis.get("status") or "failed",
                "data_as_of": day.get("data_as_of"),
                "quality_session_refs": [
                    item.get("activity_id") for item in quality if item.get("activity_id")
                ],
                "gaps": copy.deepcopy(analysis.get("gaps") or []),
            })
            quality_refs.extend(
                item.get("activity_id") for item in quality if item.get("activity_id")
            )
        statuses = {str(item["status"]) for item in entries}
        status = (
            "complete" if entries and statuses <= {"ready"}
            else "degraded" if entries and "failed" not in statuses
            else "failed"
        )
        return {
            "schema_version": "weekly-prerequisite-manifest-v1",
            "status": status,
            "sealed": True,
            "week_start": str(week_start),
            "observed_through": str(observed_through),
            "expected_days": (observed_through - week_start).days + 1,
            "prepared_days": len(entries),
            "day_entries": entries,
            "summary_versions": [
                str(item["fact_version"]) for item in entries if item.get("fact_version")
            ],
            "quality_session_refs": list(dict.fromkeys(quality_refs)),
        }

    @staticmethod
    def _quality_session_review(
        quality_sessions: list[dict[str, Any]],
        daily_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidate_gaps = [
            copy.deepcopy(gap)
            for day in daily_summaries
            for gap in (day.get("daily_analysis") or {}).get("quality_candidate_gaps") or []
        ]
        if not quality_sessions:
            return {
                "headline": "本周未识别出满足证据门槛的质量课。",
                "count": 0,
                "type_counts": {},
                "total_distance_km": 0.0,
                "total_duration_minutes": 0,
                "items": [],
                "candidate_gaps": candidate_gaps,
            }
        type_counts = Counter(
            str(item.get("quality_type") or "unknown") for item in quality_sessions
        )
        total_distance = sum(float(item.get("distance_km") or 0) for item in quality_sessions)
        total_duration = sum(float(item.get("duration_minutes") or 0) for item in quality_sessions)
        labels = "、".join(
            f"{next((item.get('label') for item in quality_sessions if item.get('quality_type') == kind), kind)} {count} 次"
            for kind, count in sorted(type_counts.items())
        )
        return {
            "headline": f"本周识别 {len(quality_sessions)} 节质量课：{labels}。",
            "count": len(quality_sessions),
            "type_counts": dict(type_counts),
            "total_distance_km": round(total_distance, 1),
            "total_duration_minutes": round(total_duration),
            "items": copy.deepcopy(quality_sessions),
            "candidate_gaps": candidate_gaps,
        }

    def _training_summary_context(self, *, target: date) -> dict[str, Any]:
        """构造草稿可复用的历史单日摘要和最近两日摘要。

        历史窗口只按完整自然周聚合；当前未结束周不进入长期能力基线。
        """
        current_monday = target - timedelta(days=target.weekday())
        history_end = current_monday - timedelta(days=1)
        history_start = history_end - timedelta(days=55)
        if not self.activity_loader:
            return {
                "schema_version": "training-day-summary-v1",
                "status": "unknown",
                "reference_weeks": [],
                "recent_days": [],
            }
        try:
            activities, states = self.activity_loader(history_start, history_end)
        except Exception:
            activities, states = [], {}
        daily = [
            TrainingDaySummaryBuilder.build(
                target=history_start + timedelta(days=index),
                activities=activities,
                states=states,
                facts_cutoff=history_end,
            )
            for index in range((history_end - history_start).days + 1)
        ]
        reference_weeks = []
        for offset in range(1, 9):
            week_end = current_monday - timedelta(days=(offset - 1) * 7 + 1)
            week_start = week_end - timedelta(days=6)
            week_days = [item for item in daily if week_start <= date.fromisoformat(item["date"]) <= week_end]
            reference_weeks.append(
                TrainingDaySummaryBuilder.aggregate(
                    week_days, start=week_start, end=week_end,
                    include_daily_summaries=False,
                )
            )
        recent_start = target - timedelta(days=2)
        recent_end = target - timedelta(days=1)
        try:
            recent_activities, recent_states = self.activity_loader(recent_start, recent_end)
        except Exception:
            recent_activities, recent_states = [], {}
        # 结构判定需要个人阈值：用历史窗口活动构建 AthleteBaseline（仅档案锚定，不猜测）
        from .training_analysis import AthleteBaselineBuilder

        try:
            baseline = AthleteBaselineBuilder().build(
                activities, profile=self._athlete_profile_document(target),
            )
        except Exception:
            baseline = None
        recent_days = [
            TrainingDaySummaryBuilder.prepare_daily_analysis(
                self._compact_day_session_summaries(
                    TrainingDaySummaryBuilder.build(
                        target=recent_start + timedelta(days=index),
                        activities=recent_activities,
                        states=recent_states,
                        facts_cutoff=recent_end,
                    ),
                    baseline=baseline,
                )
            )
            for index in range(2)
        ]
        return {
            "schema_version": "training-day-summary-v1",
            "status": "sufficient" if activities else "unknown",
            "reference_window": {
                "kind": "previous_complete_natural_weeks",
                "start": str(history_start),
                "end": str(history_end),
                "week_count": len(reference_weeks),
            },
            "reference_weeks": reference_weeks,
            "recent_days": recent_days,
        }

    def capacity_facts(self) -> dict[str, Any]:
        """返回用户确认的长期能力事实；它们不会直接抬高首周负荷。"""
        return self.repository.get_capacity_facts()

    def update_capacity_facts(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.repository.get_capacity_facts()
            numeric_fields = {
                "historical_weekly_km": (0.0, 500.0),
                "historical_long_run_km": (0.0, 200.0),
                "recent_race_distance_km": (0.0, 200.0),
            }
            facts = {"type": "athlete_capacity_facts", **{
                key: current.get(key) for key in numeric_fields
            }}
            for key, (minimum, maximum) in numeric_fields.items():
                if key in payload and payload[key] not in (None, ""):
                    try:
                        value = round(float(payload[key]), 1)
                    except (TypeError, ValueError) as exc:
                        raise TrainingError("invalid_capacity_fact", f"{key} 必须是数字") from exc
                    if not minimum <= value <= maximum:
                        raise TrainingError("invalid_capacity_fact", f"{key} 超出允许范围")
                    facts[key] = value
            for key in ("interruption_context", "injury_context", "confidence_note"):
                if key in payload:
                    facts[key] = str(payload.get(key) or "").strip()[:500]
            facts["updated_at"] = _now()
            self.repository.save_capacity_facts(facts)
            return facts

    def capacity_profile(self, *, target: date | None = None) -> dict[str, Any]:
        """从已同步事实和用户确认历史构建能力档案，不把 PB 当作即时负荷。"""
        target = target or date.today()
        activities: list[dict[str, Any]] = []
        states: dict[str, str] = {}
        if self.activity_loader:
            try:
                activities, states = self.activity_loader(target - timedelta(days=55), target)
            except Exception:
                activities, states = [], {}
        weekly: dict[date, float] = {}
        running_paces: list[float] = []
        longest = 0.0
        latest_activity_date: date | None = None
        for item in activities:
            if not ("run" in str(item.get("activity_type") or "").lower()
                    or "跑" in str(item.get("activity_name") or "")):
                continue
            try:
                activity_date = date.fromisoformat(str(item.get("activity_date") or item.get("date") or item.get("start_time"))[:10])
            except (TypeError, ValueError):
                continue
            if activity_date > target:
                continue
            distance = max(0.0, float(item.get("distance_meters") or 0) / 1000)
            try:
                duration_seconds = float(item.get("duration_seconds") or 0)
            except (TypeError, ValueError):
                duration_seconds = 0
            if distance >= 3 and duration_seconds > 0:
                pace = duration_seconds / distance
                if 180 <= pace <= 720:
                    running_paces.append(pace)
            monday, _ = natural_week_bounds(activity_date)
            weekly[monday] = weekly.get(monday, 0.0) + distance
            longest = max(longest, distance)
            latest_activity_date = max(latest_activity_date or activity_date, activity_date)
        observed = sorted(weekly.values())
        current_weekly = round(observed[len(observed) // 2], 1) if observed else 0.0
        setup = self._setup_context(target=target)
        baseline = setup["baseline"]
        current_weekly = max(current_weekly, float(baseline.get("average_weekly_km") or 0))
        facts = self.repository.get_capacity_facts()
        historical_weekly = float(facts.get("historical_weekly_km") or 0)
        historical_long = float(facts.get("historical_long_run_km") or 0)
        recovery = setup.get("recovery_snapshot") or {}
        recovery_score = float((recovery.get("recovery") or {}).get("score") or 0)
        coverage = str(baseline.get("coverage") or "unknown")
        confidence = "high" if coverage == "sufficient" and len(observed) >= 3 else "medium" if observed else "low"
        current_upper = round(max(current_weekly, float(baseline.get("recent_7d_km") or 0)) * 1.1, 1)
        recent_pace = round(median(running_paces)) if len(running_paces) >= 3 else None
        return {
            "type": "athlete_capacity_profile",
            "as_of": str(target),
            "facts_cutoff": str(latest_activity_date) if latest_activity_date else None,
            "sync_coverage": coverage,
            "confidence": confidence,
            "current_readiness": {"recovery_score": recovery_score or None, "status": "unknown" if not recovery_score else ("cautious" if recovery_score < 45 else "ready")},
            "current_sustainable_capacity": {
                "weekly_km": round(current_weekly, 1),
                "long_run_km": round(longest, 1),
                "observed_weeks": len(observed),
                "recent_running_pace_sec_per_km": recent_pace,
                "pace_sample_count": len(running_paces),
            },
            "historical_proven_capacity": {"weekly_km": historical_weekly or None, "long_run_km": historical_long or None, "interruption_context": facts.get("interruption_context") or None},
            "entry_load_envelope": {"minimum_weekly_km": round(max(5.0, current_weekly * .8), 1), "maximum_weekly_km": round(max(8.0, current_upper), 1)},
            "interpretation": "当前可持续能力决定启用负荷；历史能力只用于判断恢复上限与推进速度，不直接当作首周跑量。",
        }

    def pace_calibration_profile(
        self, *, target: date | None = None, weather_context: str = "",
    ) -> dict[str, Any]:
        """读取截至目标日的同类训练事实并生成只读配速校准档案。

        校准融入：天气归一化（高温季节/用户补充信息折算配速）、
        PB 交叉验证（样本不足兜底、Z4 上限封顶、能力差距标记）。
        """
        target = target or date.today()
        activities: list[dict[str, Any]] = []
        if self.activity_loader:
            try:
                activities, _ = self.activity_loader(target - timedelta(days=42), target)
            except Exception:
                activities = []
        setup = self._setup_context(target=target)
        personal_bests: dict[str, Any] | None = None
        target_time: Any = None
        try:
            from .memory import MemoryStore
            memory = MemoryStore(self.repository.root)
            profile_mem = memory.get("fitness-assessment")
            if profile_mem is not None:
                personal_bests = (
                    profile_mem.front_matter.get("personal_bests") or {}
                )
            active = self.goals.list_active()
            if active:
                target_time = (active[0] or {}).get("target_time")
        except Exception:
            pass
        return self.pace_profile_builder.build(
            activities,
            target=target,
            athlete_profile=setup.get("athlete_profile") or {},
            personal_bests=personal_bests,
            target_time=target_time,
            weather_context=weather_context,
        )

    def _athlete_profile_with_pace_reference(
        self, setup: dict[str, Any], *, target: date,
    ) -> dict[str, Any]:
        """仅把近期跑步事实带入规划，不从目标成绩或统一配速推造处方。"""
        profile = copy.deepcopy(setup.get("athlete_profile") or {})
        current = self.capacity_profile(target=target).get("current_sustainable_capacity") or {}
        pace = current.get("recent_running_pace_sec_per_km")
        if pace:
            profile["recent_running_pace_sec_per_km"] = pace
        return profile

    @staticmethod
    def _suggest_weekly_mileage(
        baseline: dict[str, Any], available_days: list[int], max_minutes: int,
    ) -> tuple[float, list[str]]:
        coverage = str(baseline.get("coverage") or "unknown")
        reported = float(baseline.get("reported_weekly_mileage") or 0)
        previous_week = float(
            baseline.get("previous_week_km")
            or baseline.get("average_weekly_km")
            or 0
        )
        average = float(baseline.get("average_weekly_km") or 0)
        if reported > 0:
            weekly_km = round(reported, 1)
            basis = [f"用户确认当前通常周跑量 {reported:g} km"]
        elif previous_week > 0:
            weekly_km = round(previous_week, 1)
            basis = [f"上一完整自然周实际跑量 {previous_week:g} km"]
        elif average > 0:
            weekly_km = round(average, 1)
            basis = [f"固定周参考跑量 {average:g} km"]
        else:
            weekly_km = 20.0
            basis = ["上一完整自然周缺少可用活动，使用保守起始负荷"]
        # 没有近期个人配速事实时，不能把可训练分钟数按统一配速换算成跑量容量。
        # 调用方会在具备个人事实时使用 TrainingLoadEnvelope 进行容量校验。
        return max(1.0, weekly_km), basis

    def _draft_fields(
        self,
        payload: dict[str, Any],
        *,
        goal_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal_id = _identifier(payload.get("goal_id"), field="goal_id")
        goal = copy.deepcopy(goal_override) if goal_override else self.goals.get(goal_id)
        if not goal:
            raise TrainingError("goal_not_found", "训练目标不存在，请刷新后重新选择", status_code=404)
        if not goal.get("target_date"):
            raise TrainingError(
                "race_date_required",
                "目标赛事备赛方案需要明确的赛事日期；没有赛事时可继续使用持续跑步陪伴。",
            )
        if _coerce_date(goal["target_date"], field="target_date") <= date.today():
            raise TrainingError(
                "race_date_passed",
                "目标赛事日期必须晚于今天；请先调整赛事日期，或继续使用持续跑步陪伴。",
            )
        available_days = _coerce_days(payload.get("available_days", []))
        max_minutes = int(payload.get("max_session_minutes") or 90)
        if not 20 <= max_minutes <= 360:
            raise TrainingError("invalid_session_duration", "单次最长训练需在 20–360 分钟之间")
        setup = self._setup_context(target=date.today())
        baseline = copy.deepcopy(setup["baseline"])
        reported = payload.get("reported_weekly_mileage")
        if reported not in (None, ""):
            try:
                reported = round(float(reported), 1)
            except (TypeError, ValueError) as exc:
                raise TrainingError("invalid_weekly_mileage", "当前通常周跑量必须是数字") from exc
            if not 1 <= reported <= 500:
                raise TrainingError("invalid_weekly_mileage", "当前通常周跑量需在 1–500 km 之间")
            baseline["reported_weekly_mileage"] = reported
        additional_context_raw = str(payload.get("additional_context") or "").strip()
        # 这是单次草稿请求的短期补充，不进入目标、长期能力或训练记忆。
        # 服务端再次限长，避免非浏览器调用绕过前端 maxlength；超出部分静默截断，
        # 不让可选输入把核心草稿生成变成新的失败门禁。
        additional_context = additional_context_raw[:280]
        supplement_request_id = str(payload.get("request_id") or uuid.uuid4().hex)
        supplement_meta = {
            "request_id": supplement_request_id[:80],
            "status": "provided" if additional_context else "no_additional_context",
            "char_count": len(additional_context_raw),
            "accepted_char_count": len(additional_context),
            "truncated": len(additional_context_raw) > len(additional_context),
            "applied": False,
        }
        constraints = {
            "available_days": available_days,
            "max_session_minutes": max_minutes,
            "fixed_unavailable": str(payload.get("fixed_unavailable") or "").strip(),
            # 解析“固定不可训练时间”文本为排除的星期几（如“周三晚”→[2]），供确定性排期与 AI 排课使用
            "fixed_unavailable_days": sorted(
                _parse_fixed_unavailable(str(payload.get("fixed_unavailable") or ""))
            ),
            "preferred_terrain": str(payload.get("preferred_terrain") or "").strip(),
            "cross_training": bool(payload.get("cross_training")),
            "medical_limitations": str(payload.get("medical_limitations") or "").strip(),
        }
        planning_constraints = {
            **constraints,
            "additional_context": additional_context,
        }
        athlete_profile = self._athlete_profile_with_pace_reference(setup, target=date.today())
        pace_profile = self.pace_calibration_profile(target=date.today(), weather_context=additional_context)
        short_term_training = setup.get("short_term_training") or self._recent_two_days_training(
            target=date.today(),
        )
        training_summary = self._training_summary_context(target=date.today())
        facts = PlanningFactPackBuilder.build(
            goal=goal,
            baseline=baseline,
            constraints=planning_constraints,
            athlete_profile=athlete_profile,
            recovery_snapshot=setup.get("recovery_snapshot") or {},
            short_term_training=short_term_training,
            training_summary=training_summary,
        )
        summary_version = _facts_fingerprint({
            "schema_version": training_summary.get("schema_version"),
            "reference_weeks": training_summary.get("reference_weeks"),
            "recent_days": training_summary.get("recent_days"),
        })
        facts["summary_version"] = summary_version
        facts["facts_snapshot_id"] = _facts_fingerprint({
            "goal": goal,
            "baseline": baseline,
            "constraints": planning_constraints,
            "summary_version": summary_version,
            "pace_profile": pace_profile,
        })
        facts["generation_id"] = uuid.uuid4().hex
        coverage = str((facts.get("activity_coverage") or {}).get("status") or "unknown")
        facts["readiness"] = {
            "status": "ready" if coverage == "sufficient" else "partial",
            "activity_coverage": coverage,
            "facts_cutoff": training_summary.get("reference_window", {}).get("end"),
            "health_confidence": "low" if not setup.get("recovery_snapshot") else "normal",
        }
        envelope = TrainingLoadEnvelope.calculate(facts)
        try:
            planning = self.scheme_planner.plan(facts, envelope)
        except TrainingSchemeCandidateUnavailable as exc:
            error = TrainingError(
                "scheme_candidate_unavailable",
                "方案生成失败，请查看失败阶段和缺失信息后点击“重新生成”",
                status_code=503,
            )
            error.failure_stage = getattr(exc, "stage", "training_framework")
            error.failure_type = getattr(exc, "failure_type", "provider_or_schema")
            error.retryable = bool(getattr(exc, "retryable", True))
            error.completed_stages = list(getattr(exc, "completed", []))
            error.framework_id = getattr(exc, "framework_id", None)
            error.facts_snapshot_id = facts.get("facts_snapshot_id")
            error.summary_version = facts.get("summary_version")
            raise error from exc
        supplement_meta["applied"] = bool(
            additional_context and planning.get("generation_mode") == "skill"
        )
        if additional_context and not supplement_meta["applied"]:
            supplement_meta["status"] = "supplement_unavailable"
        first_week = planning["first_four_weeks"][0]
        weekly_km = float(first_week["target_km"])
        data_basis = [
            *planning["feasibility"].get("evidence", []),
            *planning.get("assumptions", []),
        ]
        if baseline.get("reported_weekly_mileage"):
            data_basis.insert(
                0,
                f"用户确认当前通常周跑量 {float(baseline['reported_weekly_mileage']):g} km",
            )
        elif float(baseline.get("previous_week_km") or baseline.get("average_weekly_km") or 0) > 0:
            data_basis.insert(
                0,
                f"上一完整自然周实际跑量 {float(baseline.get('previous_week_km') or baseline.get('average_weekly_km') or 0):g} km",
            )
        elif float(baseline.get("average_weekly_km") or 0) > 0:
            data_basis.insert(0, f"上一完整自然周实际跑量 {float(baseline['average_weekly_km']):g} km")
        elif float(baseline.get("distance_km") or 0) > 0:
            observed_weekly = float(baseline["distance_km"]) / 4
            data_basis.insert(0, f"最近 28 天已记录 {float(baseline['distance_km']):g} km，周均约 {observed_weekly:g} km")
        observed_weekly = float(envelope.get("observed_weekly_km") or 0)
        if float(envelope["initial_target_km"]) + 0.1 < observed_weekly:
            data_basis.append(
                f"受可训练时间上限约束，首周建议降至 {float(envelope['initial_target_km']):g} km"
            )

        weekly_pattern = self._planning_week_pattern(
            first_week.get("workouts") or [], available_days,
            pace_profile=pace_profile,
            recovery_snapshot=setup.get("recovery_snapshot") or {},
            constraints=constraints,
            goal_context=goal,
        )
        recommended_start = date.today()
        near_term_schedule = _project_draft_near_term_schedule(
            planning.get("first_four_weeks") or [],
            anchor=recommended_start,
            pace_profile=pace_profile,
            recovery_snapshot=setup.get("recovery_snapshot") or {},
            constraints=constraints,
            goal_context=goal,
            weather_context=additional_context,
        )
        entry_phase = self._entry_phase_recommendation(
            planning.get("periodization") or [], planning.get("entry_review") or {},
        )

        return {
            "goal_id": goal_id,
            "goal_snapshot": copy.deepcopy(goal),
            "baseline_snapshot": baseline,
            "constraints": constraints,
            "supplement": supplement_meta,
            "coaching_mode": facts["coaching_mode"],
            "generation_mode": planning["generation_mode"],
            "inference_source": planning.get("inference_source"),
            "validation_status": planning.get("validation_status"),
            "recommendation_status": planning.get("recommendation_status"),
            "decision_status": planning.get("decision_status"),
            "adjustments": planning.get("adjustments", []),
            "review": copy.deepcopy(planning.get("review") or {"status": "not_provided", "items": [], "safety_hold": None}),
            "entry_review": copy.deepcopy(planning.get("entry_review") or {}),
            "entry_phase_recommendation": entry_phase,
            "fallback_reason": planning.get("fallback_reason"),
            "facts_snapshot_id": facts.get("facts_snapshot_id"),
            "summary_version": facts.get("summary_version"),
            "audit": copy.deepcopy(planning.get("audit") or {}),
            "readiness": copy.deepcopy(facts.get("readiness") or {}),
            "degraded": bool(planning.get("degraded") or facts.get("uncertainties")),
            "data_gaps": copy.deepcopy(planning.get("data_gaps") or facts.get("uncertainties") or []),
            "feasibility": planning["feasibility"],
            "periodization": planning["periodization"],
            "load_progression": planning["load_progression"],
            "first_four_weeks": planning["first_four_weeks"],
            "planning_trace": planning.get("planning_trace", []),
            "assumptions": planning.get("assumptions", []),
            "uncertainties": planning.get("uncertainties", []),
            "risk_flags": planning.get("risk_flags", []),
            "user_explanation": planning.get("user_explanation", ""),
            "current_phase": {
                "name": str(planning["periodization"][0].get("name") or "基础期"),
                "purpose": str(planning["periodization"][0].get("purpose") or "建立稳定训练节奏"),
            },
            "weekly_mileage_target": round(weekly_km, 1),
            "data_basis": data_basis,
            "weekly_pattern": weekly_pattern,
            "recommended_start_date": str(recommended_start),
            "near_term_schedule": near_term_schedule,
        }

    def create_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        fields = self._draft_fields(payload)

        plan_id = f"{_slug(fields['goal_snapshot']['name'])}-{uuid.uuid4().hex[:8]}"
        scheme = {
            "type": "training_scheme",
            "plan_id": plan_id,
            "version": 1,
            "status": "draft",
            "created_at": _now(),
            "updated_at": _now(),
            **fields,
            "week_overrides": {},
            "safety_guardrails": {"pain_requires_reduction": True, "medical_diagnosis": False},
        }
        # 新草稿生成时自动归档旧 draft（标记 superseded），避免草稿无限堆积
        self.repository.supersede_other_drafts(scheme["plan_id"])
        self.repository.save_draft(scheme)
        return scheme

    def update_draft(self, plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """原地重算未生效草稿，不触碰 active 读模型。"""
        with self._lock:
            draft = self.repository.get_draft(plan_id)
            if not draft:
                raise TrainingError("draft_not_found", "训练方案草稿不存在", status_code=404)
            active = self.repository.get_active(migrate_legacy=False)
            if draft.get("status") != "draft" or (
                active and active.get("plan_id") == draft.get("plan_id")
            ):
                raise TrainingError("invalid_plan_state", "只有未生效草稿可以修改", status_code=409)
            fields = self._draft_fields(payload)
            draft.update(fields)
            draft["updated_at"] = _now()
            self.repository.save_draft(draft)
        return draft

    def preview_goal_rescheduling(self, payload: dict[str, Any]) -> dict[str, Any]:
        """生成不生效的改期替代草稿；不改写目标或当前方案。"""
        with self._lock:
            scheme = self.repository.get_active(migrate_legacy=False)
            if not scheme or scheme.get("status") != "active":
                raise TrainingError(
                    "active_plan_required",
                    "目标改期预览需要当前生效方案",
                    status_code=409,
                )
            current_goal = self.goals.get(str(scheme.get("goal_id") or ""))
            if not current_goal:
                raise TrainingError("goal_not_found", "当前训练目标不存在", status_code=404)
            target_date = _coerce_date(payload.get("target_date"), field="target_date")
            if target_date <= date.today():
                raise TrainingError("race_date_passed", "新的赛事日期必须晚于今天")
            candidate_goal = copy.deepcopy(current_goal)
            candidate_goal["target_date"] = str(target_date)
            if payload.get("target_time") is not None:
                candidate_goal["target_time"] = str(payload.get("target_time") or "") or None
            fields = self._draft_fields(
                {
                    "goal_id": current_goal["goal_id"],
                    "available_days": (scheme.get("constraints") or {}).get("available_days") or [],
                    "max_session_minutes": (scheme.get("constraints") or {}).get("max_session_minutes") or 90,
                },
                goal_override=candidate_goal,
            )
            replacement = {
                "type": "training_scheme",
                "plan_id": scheme["plan_id"],
                "version": int(scheme.get("version") or 0) + 1,
                "status": "draft",
                "created_at": _now(),
                "updated_at": _now(),
                **fields,
                "week_overrides": {},
                "safety_guardrails": {"pain_requires_reduction": True, "medical_diagnosis": False},
            }
            preview = {
                "type": "goal_rescheduling_preview",
                "preview_id": uuid.uuid4().hex,
                "plan_id": scheme["plan_id"],
                "goal_id": current_goal["goal_id"],
                "base_version": int(scheme.get("version") or 0),
                "status": "pending_confirmation",
                "created_at": _now(),
                "expires_at": str(date.today() + timedelta(days=2)),
                "current_goal": copy.deepcopy(current_goal),
                "proposed_goal": copy.deepcopy(candidate_goal),
                "replacement_draft": replacement,
                "changes": [{
                    "field": "target_date",
                    "before": current_goal.get("target_date"),
                    "after": str(target_date),
                }],
            }
            self.repository.save_rescheduling_preview(preview)
            return preview

    def confirm_goal_rescheduling(
        self, preview_id: str, *, idempotency_key: str,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise TrainingError("idempotency_key_required", "确认改期必须提供 idempotency_key")
        with self._lock:
            preview = self.repository.get_rescheduling_preview(preview_id)
            if not preview:
                raise TrainingError("goal_rescheduling_preview_not_found", "改期预览不存在", status_code=404)
            if preview.get("status") == "confirmed":
                return {"preview": preview, "scheme": self.repository.get_draft(preview["plan_id"]), "idempotent": True}
            if preview.get("status") != "pending_confirmation":
                raise TrainingError("goal_rescheduling_preview_stale", "改期预览已不能确认", status_code=409)
            if date.today() > date.fromisoformat(str(preview["expires_at"])[:10]):
                raise TrainingError("goal_rescheduling_preview_stale", "改期预览已过期", status_code=409)
            current = self.repository.get_active(migrate_legacy=False)
            if (
                not current
                or current.get("plan_id") != preview.get("plan_id")
                or int(current.get("version") or 0) != int(preview.get("base_version") or 0)
            ):
                raise TrainingError("goal_rescheduling_preview_stale", "当前方案已变化，请重新生成改期预览", status_code=409)
            replacement = copy.deepcopy(preview.get("replacement_draft") or {})
            if not replacement:
                raise TrainingError("goal_rescheduling_preview_stale", "改期预览缺少替代草稿", status_code=409)
            # 先保存不可变旧版本，再切换目标并把唯一当前读模型交给 draft。
            superseded = copy.deepcopy(current)
            superseded["status"] = "superseded"
            superseded["superseded_at"] = _now()
            superseded["effective_to"] = str(date.today() - timedelta(days=1))
            _write_document(
                self.repository.plans / "history" / f"{current['plan_id']}-v{current['version']}.md",
                superseded,
            )
            self.goals.update(
                str(preview["goal_id"]),
                preview["proposed_goal"],
            )
            if self.repository.active_path.exists():
                self.repository.active_path.unlink()
            self.repository.save_draft(replacement)
            preview["status"] = "confirmed"
            preview["confirmed_at"] = _now()
            preview["idempotency_key"] = idempotency_key
            self.repository.save_rescheduling_preview(preview)
            return {"preview": preview, "scheme": replacement, "idempotent": False}

    @staticmethod
    def _planning_week_pattern(
        workouts: list[dict[str, Any]], available_days: list[int],
        *, pace_profile: dict[str, Any] | None = None,
        recovery_snapshot: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        goal_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        by_day = {int(item["weekday"]): copy.deepcopy(item) for item in workouts}
        pattern = []
        for weekday in range(7):
            if weekday not in by_day or not _is_trainable_weekday(weekday, constraints or {}):
                pattern.append({
                    "weekday": weekday, "title": "休息与恢复", "type": "rest",
                    "purpose": "吸收训练刺激", "duration_minutes": 0,
                    "distance_km": 0, "intensity": "休息或轻度活动",
                    "reason": "按可训练日与周期方案安排", "is_key": False,
                    "alternatives": [], "adjustment_triggers": [],
                })
                continue
            item = by_day[weekday]
            item = ensure_training_prescription(
                item,
                pace_reference_sec_per_km=(
                    ((pace_profile or {}).get("zones") or {}).get("z2", {}).get("recent_median_sec_per_km")
                ),
            )
            if pace_profile:
                item = DailyPaceAdjustmentEngine().apply(
                    item,
                    profile=pace_profile,
                    recovery_snapshot=recovery_snapshot or {},
                    constraints=constraints or {},
                    goal_context=goal_context or {},
                )
            item.setdefault("reason", "由专业方案生成管线安排")
            item.setdefault("alternatives", [])
            item.setdefault("adjustment_triggers", [])
            pattern.append(item)
        return pattern

    @staticmethod
    def _entry_phase_recommendation(
        periodization: list[dict[str, Any]], review: dict[str, Any],
    ) -> dict[str, Any]:
        phases = [item for item in periodization if isinstance(item, dict)]
        fallback = phases[0] if phases else {"name": "基础期", "purpose": "建立稳定训练节奏"}
        requested = str(review.get("recommended_entry_phase") or "").strip().lower()
        selected = None
        for index, phase in enumerate(phases):
            candidates = {
                str(phase.get("name") or "").strip().lower(),
                str(phase.get("phase_id") or "").strip().lower(),
                str(phase.get("id") or "").strip().lower(),
                f"phase_{index + 1}",
                str(index + 1),
            }
            if requested and requested in candidates:
                selected = phase
                break
        if selected is None and str(review.get("decision") or "hold") == "advance" and len(phases) > 1:
            selected = phases[1]
        selected = selected or fallback
        return {
            "name": str(selected.get("name") or "基础期"),
            "purpose": str(selected.get("purpose") or "建立稳定训练节奏"),
            "decision": str(review.get("decision") or "hold"),
            "confidence": float(review.get("confidence") or 0),
            "evidence": [str(item) for item in review.get("evidence") or []],
            "adjustments": [str(item) for item in review.get("adjustments") or []],
            "confirmed": False,
        }

    @staticmethod
    def _default_pattern(days: list[int], weekly_km: float, max_minutes: int) -> list[dict[str, Any]]:
        allocations: dict[int, tuple[str, str, float, bool]] = {}
        for position, weekday in enumerate(days):
            if position == len(days) - 1 and len(days) == 2:
                allocations[weekday] = ("long", "长距离有氧跑", 0.6, True)
            elif position == len(days) - 1 and len(days) >= 3:
                allocations[weekday] = ("long", "长距离有氧跑", 0.35, True)
            elif position == len(days) // 2 and len(days) >= 3:
                allocations[weekday] = ("quality", "节奏或间歇训练", 0.25, True)
            else:
                allocations[weekday] = ("easy", "轻松有氧跑", 0, False)
        fixed = sum(item[2] for item in allocations.values())
        easy_count = sum(item[0] == "easy" for item in allocations.values())
        easy_share = max(0.1, (1 - fixed) / max(1, easy_count))
        pattern = []
        for weekday in range(7):
            if weekday not in allocations:
                pattern.append({
                    "weekday": weekday, "title": "休息与恢复", "type": "rest",
                    "purpose": "吸收训练刺激", "duration_minutes": 0,
                    "distance_km": 0, "intensity": "休息或轻度活动",
                    "reason": "按可训练日约束安排", "is_key": False,
                })
                continue
            workout_type, title, share, is_key = allocations[weekday]
            share = share or easy_share
            distance_km = round(weekly_km * share, 1)
            # 缺少个人配速事实时，最长可训练时间只是时间窗口，不能用统一配速
            # 反推成另一项完成目标。
            duration = max_minutes
            intensity = "呼吸有压力但始终可控" if workout_type == "quality" else "能完整对话，结束仍有余量"
            pattern.append(ensure_training_prescription({
                "weekday": weekday, "title": title, "type": workout_type,
                "purpose": "提升专项能力" if is_key else "积累有氧并保持恢复",
                "duration_minutes": duration, "distance_km": distance_km,
                "intensity": intensity, "reason": "依据目标周跑量和可训练日生成",
                "is_key": is_key,
            }, synthesize_steps=True))
        return pattern

    def activation_preview(self, plan_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """生成可确认的启用安排；预览不能改变方案或教练模式。"""
        payload = payload or {}
        draft = self.repository.get_draft(plan_id)
        if not draft or draft.get("status") != "draft":
            raise TrainingError("draft_not_found", "只能为未确认草稿生成启用安排", status_code=404)
        mode = str(payload.get("start_mode") or "today")
        today = date.today()
        if mode == "today":
            effective_from = today
        elif mode == "next_week":
            effective_from = today + timedelta(days=(7 - today.weekday()) or 7)
        elif mode == "custom":
            effective_from = _coerce_date(payload.get("effective_from"), field="effective_from")
            if effective_from < today:
                raise TrainingError("activation_date_in_past", "启用日期不能早于今天")
        else:
            raise TrainingError("invalid_start_mode", "start_mode 仅支持 today、next_week 或 custom")
        capacity = self.capacity_profile(target=today)
        requested = str(payload.get("entry_strategy") or "recommended")
        if requested not in {"recommended", "normal", "conservative"}:
            raise TrainingError("invalid_entry_strategy", "entry_strategy 仅支持 recommended、normal 或 conservative")
        strategy = "conservative" if capacity["confidence"] == "low" or capacity["current_readiness"]["status"] == "cautious" else "normal"
        if requested != "recommended":
            strategy = requested
        bridge_days = (7 - effective_from.weekday()) % 7
        preview = {
            "type": "activation_preview", "preview_id": uuid.uuid4().hex,
            "plan_id": draft["plan_id"], "draft_updated_at": draft.get("updated_at"),
            "status": "pending_confirmation", "created_at": _now(),
            "start_mode": mode, "effective_from": str(effective_from),
            "entry_strategy": strategy, "capacity_profile": capacity,
            "facts_cutoff": capacity.get("facts_cutoff"),
            "bridge_week": {
                "required": bridge_days > 0,
                "week_start": str(natural_week_bounds(effective_from)[0]),
                "days": bridge_days + 1,
                "counts_toward_phase": False,
                "guidance": "启用周按衔接周执行：只安排生效日后的低风险课次，不计入周期推进。" if bridge_days > 0 else "周一启用，可从完整自然周进入首阶段。",
            },
            "recommendations": [
                "同步完成后再确认，可提高能力档案与首周安排的可信度。" if capacity["confidence"] == "low" else "当前数据足以给出启用建议；后续仍按周复盘决定推进。",
                "历史高峰能力不会自动转化为首周跑量。",
            ],
        }
        self.repository.save_activation_preview(preview)
        return preview

    def activate(self, plan_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            draft = self.repository.get_draft(plan_id)
            if not draft:
                raise TrainingError("draft_not_found", "训练方案草稿不存在", status_code=404)
            if draft.get("status") != "draft":
                raise TrainingError("invalid_plan_state", "只有草稿可以激活", status_code=409)
            active = self.repository.get_active()
            if active and active.get("status") == "active":
                raise TrainingError("active_plan_exists", "已有生效方案，请先通过调整提案修改", status_code=409)
            context = self.repository.get_coaching_context()
            payload = payload or {}
            preview_id = payload.get("preview_id")
            preview = self.repository.get_activation_preview(str(preview_id)) if preview_id else self.activation_preview(plan_id, payload)
            if not preview or preview.get("plan_id") != draft["plan_id"]:
                raise TrainingError("activation_preview_not_found", "启用预览不存在，请重新生成", status_code=409)
            if preview.get("draft_updated_at") != draft.get("updated_at"):
                raise TrainingError("activation_preview_stale", "草稿或同步事实已更新，请重新查看启用建议", status_code=409)
            if preview.get("facts_cutoff") != self.capacity_profile(target=date.today()).get("facts_cutoff"):
                raise TrainingError("activation_preview_stale", "同步数据已变化，请重新查看启用建议", status_code=409)
            effective_from = _coerce_date(preview["effective_from"], field="effective_from")
            is_scheduled = effective_from > date.today()
            draft["status"] = "scheduled" if is_scheduled else "active"
            activated_at = _now()
            draft["updated_at"] = activated_at
            draft["activated_at"] = activated_at
            draft["effective_from"] = str(effective_from)
            draft["effective_to"] = None
            draft["activation_preview_id"] = preview["preview_id"]
            draft["activation"] = {"start_mode": preview["start_mode"], "entry_strategy": preview["entry_strategy"], "facts_cutoff": preview.get("facts_cutoff"), "bridge_week": preview["bridge_week"]}
            self.repository.save_draft(draft)
            activation = {"type": "training_activation", "activation_id": uuid.uuid4().hex, "plan_id": plan_id, "status": "scheduled" if is_scheduled else "active", "effective_from": str(effective_from), "preview_id": preview["preview_id"], "confirmed_at": activated_at}
            self.repository.save_activation(activation)
            if is_scheduled:
                self.repository.save_scheduled(draft)
                return draft
            self.repository.save_scheme(draft)
            self._materialize_week(draft, date.today(), persist=True)
            return draft

    def _promote_due_scheduled(self, today: date) -> None:
        """仅在真实当前日把已到期的排期转为生效方案，避免历史查询改写状态。"""
        if today != date.today():
            return
        active = self.repository.get_active(migrate_legacy=False)
        scheduled = next((item for item in self.repository.list_scheduled() if _scheme_effective_from(item) and _scheme_effective_from(item) <= today), None)
        if not scheduled:
            return
        if active and active.get("plan_id") != scheduled.get("plan_id"):
            # 不同方案主线的排期不应覆盖当前方案；正常流程不会产生这种状态，
            # 这里保守保持当前方案并等待人工处理。
            return
        scheduled["status"] = "active"
        scheduled["updated_at"] = _now()
        self.repository.save_scheme(scheduled)
        self.repository.remove_scheduled(str(scheduled["plan_id"]))

    def cancel_scheduled_activation(self, plan_id: str) -> dict[str, Any]:
        """取消尚未生效的排期，并把同一方案退回可确认草稿。"""
        with self._lock:
            scheduled = self.repository.get_scheduled(plan_id)
            if not scheduled or scheduled.get("status") != "scheduled":
                raise TrainingError(
                    "scheduled_plan_not_found",
                    "没有找到尚未生效的排期方案",
                    status_code=404,
                )
            self.repository.remove_scheduled(plan_id)
            scheduled["status"] = "draft"
            scheduled.pop("activated_at", None)
            scheduled.pop("effective_from", None)
            scheduled.pop("effective_to", None)
            scheduled["updated_at"] = _now()
            scheduled["cancelled_activation_at"] = _now()
            self.repository.save_draft(scheduled)
            return scheduled

    def plan(self, *, target_date: date | None = None) -> dict[str, Any] | None:
        if target_date is not None:
            return self.resolve_plan_context(target_date)
        return self.repository.get_active()

    def _resolve_effective_scheme(self, target: date) -> dict[str, Any] | None:
        candidates = []
        for scheme in self.repository.list_scheme_versions():
            if scheme.get("status") == "draft":
                continue
            effective_from = _scheme_effective_from(scheme)
            effective_to = _scheme_effective_to(scheme)
            if effective_from is None or target < effective_from:
                continue
            if effective_to is not None and target > effective_to:
                continue
            candidates.append((
                effective_from,
                str(scheme.get("activated_at") or scheme.get("updated_at") or ""),
                int(scheme.get("version") or 0),
                scheme,
            ))
        for scheme in self.repository.list_scheduled():
            effective_from = _scheme_effective_from(scheme)
            if effective_from and effective_from <= target:
                candidates.append((effective_from, str(scheme.get("activated_at") or ""), int(scheme.get("version") or 0), scheme))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[:3])[3]

    def resolve_plan_context(self, target_date: date) -> dict[str, Any]:
        """解析目标日期当时生效的方案版本和具体计划课次。"""
        target = _coerce_date(target_date, field="target_date")
        scheme = self._resolve_effective_scheme(target)
        if not scheme:
            scheduled = next((item for item in self.repository.list_scheduled() if _scheme_effective_from(item) and _scheme_effective_from(item) > target), None)
            if scheduled:
                return {"status": "scheduled_plan", "exists": False, "target_date": str(target), "comparison_status": "not_applicable", "plan_id": scheduled.get("plan_id"), "effective_from": str(_scheme_effective_from(scheduled)), "goal": copy.deepcopy(scheduled.get("goal_snapshot") or {}), "reference_only": True}
            draft = next((item for item in self.repository.list_drafts() if item.get("status") == "draft" and str(item.get("created_at") or "")[:10] <= str(target)), None)
            if draft:
                return {"status": "draft_available", "exists": False, "target_date": str(target), "comparison_status": "not_applicable", "plan_id": draft.get("plan_id"), "goal": copy.deepcopy(draft.get("goal_snapshot") or {}), "reference_only": True}
            return {
                "status": "no_effective_plan",
                "exists": False,
                "target_date": str(target),
                "comparison_status": "not_applicable",
            }

        week = self._materialize_week(scheme, target)
        workout = next(
            (item for item in week.get("workouts", []) if item.get("date") == str(target)),
            None,
        )
        status = "effective" if workout is not None else "plan_without_workout"
        effective_to = _scheme_effective_to(scheme)
        return {
            "status": status,
            "exists": True,
            "target_date": str(target),
            "comparison_status": "applicable" if workout is not None else "not_applicable",
            "plan_id": scheme.get("plan_id"),
            "plan_version": int(scheme.get("version") or 0),
            "activated_at": (
                scheme.get("activated_at")
                or scheme.get("updated_at")
                or scheme.get("created_at")
            ),
            "effective_from": str(_scheme_effective_from(scheme)),
            "effective_to": str(effective_to) if effective_to else None,
            "goal": copy.deepcopy(scheme.get("goal_snapshot") or scheme.get("goal") or {}),
            "current_phase": copy.deepcopy(scheme.get("current_phase") or {}),
            "weekly_mileage_target": scheme.get("weekly_mileage_target"),
            "week_id": week.get("week_id"),
            "week_start": week.get("week_start"),
            "week_end": week.get("week_end"),
            "workout": copy.deepcopy(workout),
        }

    def home(self, *, today: date | None = None) -> dict[str, Any]:
        today = today or date.today()
        with self._lock:
            self._promote_due_scheduled(today)
        context = self.repository.get_coaching_context()
        scheme = self._resolve_effective_scheme(today)
        if not scheme:
            drafts = [
                d for d in self.repository.list_drafts()
                if d.get("status") == "draft"
            ]
            pending_draft = max(
                drafts, key=lambda d: d.get("updated_at", ""), default=None,
            )
            if isinstance(pending_draft, dict):
                # 旧草稿没有 near_term_schedule 时只在读模型补投影，不改写候选正文或指纹。
                try:
                    draft_anchor = _coerce_date(
                        pending_draft.get("recommended_start_date") or today,
                        field="recommended_start_date",
                    )
                except TrainingError:
                    draft_anchor = today
                pending_draft = copy.deepcopy(pending_draft)
                pending_draft.setdefault("recommended_start_date", str(draft_anchor))
                pending_draft["near_term_schedule"] = _project_draft_near_term_schedule(
                    pending_draft.get("first_four_weeks") or [], anchor=draft_anchor,
                    pace_profile=self.pace_calibration_profile(target=today),
                    recovery_snapshot=(self._setup_context() or {}).get("recovery_snapshot") or {},
                    constraints=pending_draft.get("constraints") or {},
                    goal_context=pending_draft.get("goal_snapshot") or {},
                )
            return {
                "has_active_plan": False, "scheme": None, "today": None,
                "week": None, "pending_proposals": [], "sync_state": "unknown",
                "setup": self._setup_context(),
                "coaching_mode": context["coaching_mode"],
                "pending_scheme_draft": pending_draft,
                "upcoming_scheme": next(iter(self.repository.list_scheduled()), None),
                "capacity_profile": self.capacity_profile(target=today),
                "pace_calibration_profile": self.pace_calibration_profile(target=today),
            }
        capacity_profile = self.capacity_profile(target=today)
        pace_profile = self.pace_calibration_profile(target=today)
        setup = self._setup_context()
        pace_reference = (capacity_profile.get("current_sustainable_capacity") or {}).get(
            "recent_running_pace_sec_per_km",
        )
        week = self._materialize_week(scheme, today)
        start = date.fromisoformat(week["week_start"])
        end = date.fromisoformat(week["week_end"])
        activities: list[dict[str, Any]] = []
        states: dict[str, str] = {}
        if self.activity_loader:
            try:
                activities, states = self.activity_loader(start, end)
            except Exception:
                activities, states = [], {}
        matched_workouts = ActivityMatcher.match(
            week["workouts"], activities, states, today,
        )
        skipped_dates = {
            str(feedback.get("target_date"))
            for feedback_items in (
                self.repository.feedback_for_date(start + timedelta(days=index))
                for index in range((end - start).days + 1)
            )
            for feedback in feedback_items
            if feedback.get("feedback_type") == "post_workout"
            and feedback.get("completion_status") == "skipped"
        }
        for item in matched_workouts:
            if item.get("date") in skipped_dates and not item.get("actual_activities"):
                item["execution_status"] = "skipped"
        matched_activity_ids = {
            str(activity.get("activity_id"))
            for item in matched_workouts
            for activity in item.get("actual_activities") or []
            if activity.get("activity_id")
        }
        week["unplanned_activities"] = [
            {
                "activity_id": activity.get("activity_id"),
                "date": str(activity.get("activity_date") or activity.get("start_time") or "")[:10],
                "name": activity.get("activity_name") or "实际活动",
                "tag": "unplanned",
                "distance_km": round(float(activity.get("distance_meters") or 0) / 1000, 2),
            }
            for activity in activities
            if str(activity.get("activity_id") or "") not in matched_activity_ids
        ]
        week["workouts"] = []
        for item in matched_workouts:
            prescribed = ensure_training_prescription(
                item, pace_reference_sec_per_km=pace_reference,
            )
            week["workouts"].append(self.pace_adjustment_engine.apply(
                prescribed,
                profile=pace_profile,
                recovery_snapshot=setup.get("recovery_snapshot") or {},
                constraints=setup.get("known_constraints") or {},
                goal_context=scheme.get("goal_snapshot") or scheme.get("goal") or {},
            ))
        completed = [item for item in week["workouts"] if item["execution_status"] == "completed"]
        week["progress"] = {
            "completed_sessions": len(completed),
            "completed_km": round(sum(
                actual.get("distance_km", 0)
                for item in completed for actual in item.get("actual_activities", [])
            ), 1),
            "target_km": scheme.get("weekly_mileage_target"),
            # 本周实际运动事实：周内全部已同步活动（含计划外/部分完成），
            # 与报告页 actual_summary 口径一致，供卡片并列展示计划执行 vs 实际量。
            "actual_sessions": len(activities),
            "actual_running_km": round(sum(
                float(a.get("distance_meters") or 0) / 1000
                for a in activities
                if "run" in str(a.get("activity_type") or "").lower()
                or "跑" in str(a.get("activity_name") or "")
            ), 1),
        }
        today_item = next((item for item in week["workouts"] if item["date"] == str(today)), None)
        coverage_end = min(today, end)
        coverage_dates = [start + timedelta(days=index) for index in range((coverage_end - start).days + 1)]
        incomplete_dates = [
            str(day) for day in coverage_dates
            if str(states.get(str(day)) or "unknown") in {"", "unknown", "syncing", "failed"}
        ]
        sync_state = "current" if coverage_dates and not incomplete_dates else "unknown"
        return {
            "has_active_plan": True,
            "scheme": scheme,
            "today": today_item,
            "week": week,
            "pending_proposals": self.repository.pending_proposals(scheme["plan_id"]),
            "sync_state": sync_state,
            "sync_coverage": {
                "week_start": str(start), "checked_through": str(coverage_end),
                "expected_days": len(coverage_dates),
                "covered_days": len(coverage_dates) - len(incomplete_dates),
                "incomplete_dates": incomplete_dates,
            },
            "coaching_mode": context["coaching_mode"],
            "capacity_profile": capacity_profile,
            "pace_calibration_profile": pace_profile,
            "plan_timeline": self._plan_timeline(scheme, today),
        }

    @staticmethod
    def _plan_timeline(scheme: dict[str, Any], target: date) -> dict[str, Any]:
        """返回计划时间线位置；它不替代基于周复盘的真实推进决定。"""
        phases = [
            {
                "name": str(item.get("name") or "训练阶段"),
                "purpose": str(item.get("purpose") or ""),
                "weeks": max(1, int(item.get("weeks") or 1)),
            }
            for item in (scheme.get("periodization") or [])
            if isinstance(item, dict)
        ]
        if not phases:
            return {"status": "unavailable", "phases": [], "total_weeks": 0}

        effective_from = _scheme_effective_from(scheme) or target
        first_full_week_start = effective_from + timedelta(
            days=(7 - effective_from.weekday()) % 7
        )
        total_weeks = sum(item["weeks"] for item in phases)
        activation = scheme.get("activation") or {}
        bridge_required = bool((activation.get("bridge_week") or {}).get("required"))
        is_bridge_week = bridge_required and target < first_full_week_start

        completed_weeks = 0
        planned_week = 0
        current_phase_index: int | None = None
        current_phase_week = 0
        if target >= first_full_week_start:
            elapsed_weeks = (target - first_full_week_start).days // 7
            completed_weeks = min(total_weeks, max(0, elapsed_weeks))
            planned_week = min(total_weeks, elapsed_weeks + 1)
            phase_end = 0
            for index, phase in enumerate(phases):
                phase_start = phase_end + 1
                phase_end += phase["weeks"]
                phase["start_week"] = phase_start
                phase["end_week"] = phase_end
                if planned_week and phase_start <= planned_week <= phase_end:
                    current_phase_index = index
                    current_phase_week = planned_week - phase_start + 1
                    phase["status"] = "current"
                elif planned_week and phase_end < planned_week:
                    phase["status"] = "completed"
                else:
                    phase["status"] = "upcoming"
        else:
            phase_end = 0
            for phase in phases:
                phase["start_week"] = phase_end + 1
                phase_end += phase["weeks"]
                phase["end_week"] = phase_end
                phase["status"] = "upcoming"

        current_phase = phases[current_phase_index] if current_phase_index is not None else None
        return {
            "status": "bridge" if is_bridge_week else "scheduled_timeline",
            "effective_from": str(effective_from),
            "first_full_week_start": str(first_full_week_start),
            "total_weeks": total_weeks,
            "completed_weeks": completed_weeks,
            "planned_week": planned_week,
            "progress_percent": round(planned_week / total_weeks * 100) if total_weeks and planned_week else 0,
            "current_phase_index": current_phase_index,
            "current_phase_week": current_phase_week,
            "current_phase": current_phase,
            "phases": phases,
            "note": "这是按启用日期计算的计划时间线；是否加快、保持或减量以周复盘和用户确认的进程决定为准。",
        }

    def _run_read_skill(
        self,
        skill_name: str,
        *,
        facts: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
        runner = self.scheme_planner.runner
        if runner is None:
            return None, [], "AI Coach Skill 不可用"
        context = CoachRunContext(
            request={"intent": skill_name},
            facts=copy.deepcopy(facts),
            uncertainties=(),
            policy={"coaching_mode": self.repository.get_coaching_context()["coaching_mode"]},
        )
        try:
            finding, final_context = runner.run(skill_name, context)
            return finding, list(final_context.trace), None
        except Exception as exc:
            return None, [], str(exc)

    def session_brief(self, *, target: date | None = None) -> dict[str, Any]:
        """快速生成面向运动者的训练前说明，不等待在线模型。"""
        target = target or date.today()
        home = self.home(today=target)
        scheme = home.get("scheme")
        session = home.get("today")
        if not scheme or not session:
            raise TrainingError(
                "planned_session_required", "目标日期没有可说明的生效训练安排", status_code=404,
            )
        session = ensure_training_prescription(session)
        recovery = self._setup_context().get("recovery_snapshot") or {}
        recovery_data = recovery.get("recovery") or recovery
        load_data = recovery.get("training_load") or {}
        try:
            recovery_score = float(recovery_data.get("overall_score") or 100)
        except (TypeError, ValueError):
            recovery_score = 100
        load_risk = str(load_data.get("acwr_status") or "").lower()
        needs_conservative_execution = recovery_score < 60 or load_risk in {
            "high_risk", "high", "elevated",
        }
        is_rest = session.get("type") == "rest"
        alternatives = [str(item) for item in session.get("alternatives") or [] if item]
        triggers = [str(item) for item in session.get("adjustment_triggers") or [] if item]
        prescription = session.get("training_prescription") or {}
        pacing_guard = prescription.get("pacing_guard") or {}
        pace_guidance = prescription.get("pace_guidance") or {}
        today_pace = pace_guidance.get("today_target") or {}
        feel = ((prescription.get("targets") or {}).get("feel") or {}).get("label") or session.get("intensity") or "可控强度"
        blocks = prescription.get("blocks") or []
        block_steps = [
            f"{str(block.get('role') or '训练')}：{str(block.get('instruction') or '按处方完成')}"
            for block in blocks if isinstance(block, dict)
        ]
        structure_summary = workout_steps_summary(prescription)
        resolved_step_targets: list[str] = []
        for step in iter_leaf_workout_steps(prescription.get("steps") or []):
            if step.get("role") not in {"work", "recovery"}:
                continue
            resolved = step.get("resolved_target")
            resolved = resolved if isinstance(resolved, dict) else {}
            label = "工作段" if step.get("role") == "work" else "恢复段"
            if resolved.get("status") == "available" and resolved.get("display_range"):
                resolved_step_targets.append(f"{label}按 {resolved['display_range']} 执行")
            elif resolved.get("label"):
                resolved_step_targets.append(f"{label}：{resolved['label']}")
        recommendations = (
            ["进行轻度活动或完全休息，以主观恢复为准"]
            if is_rest else [
                structure_summary or "先轻松热身，再完成主体，最后放松",
                *(resolved_step_targets[:2] or block_steps[:1]),
                f"强度以“{feel}”为主；RPE 只作辅助体感校验",
            ]
        )
        if pacing_guard.get("status") == "requires_review":
            recommendations.insert(
                0,
                f"当前距离与时长待修正；本次只按 {session.get('duration_minutes')} 分钟可对话强度完成，不追逐原距离。",
            )
        if needs_conservative_execution:
            recommendations.append("近期状态或负荷不适合硬撑；明显疲劳时直接改为步行或休息。")
        elif triggers:
            recommendations.append(triggers[0])
        if alternatives:
            recommendations.append(f"时间或场地受限时，可改为{alternatives[0]}。")
        stop_instruction = "出现疼痛、异常胸闷或明显状态下降时停止训练并反馈。"
        if stop_instruction not in recommendations[:4]:
            recommendations = recommendations[:4] + [stop_instruction]
        finding = {
            "conclusion": (
                "今天以恢复和吸收训练刺激为主。"
                if is_rest else f"今天的重点是{session.get('purpose') or '按计划完成训练'}。"
            ),
            "recommendations": recommendations[:5],
            "risk_flags": (
                ["今天以轻松完成为上限，不需要追求配速或额外距离。"]
                if needs_conservative_execution and not is_rest else []
            ) + (["当前课程的距离与时长不一致，已收紧为单一安全执行目标。"] if pacing_guard.get("status") == "requires_review" else []),
            "pace_result": {
                "status": "not_applicable" if is_rest else str(today_pace.get("status") or "feel_only"),
                "range": today_pace.get("display_range"),
                "safety_action": (
                    "恢复日不设配速目标"
                    if is_rest else pace_guidance.get("safety_action")
                ),
            },
            "explanation": copy.deepcopy(pace_guidance.get("explanation") or {}),
        }
        return {
            "type": "training_session_brief",
            "date": str(target),
            "plan_id": scheme["plan_id"],
            "plan_version": scheme["version"],
            "generation_mode": "deterministic",
            "finding": finding,
        }

    def _weekly_actual_context(
        self, target: date,
    ) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any],
        list[dict[str, Any]], dict[str, str],
    ]:
        """Assemble natural-week facts without requiring a training scheme."""
        week_start, week_end = natural_week_bounds(target)
        observed_through = min(week_end, date.today())
        activities: list[dict[str, Any]] = []
        states: dict[str, str] = {}
        if self.activity_loader and observed_through >= week_start:
            try:
                activities, states = self.activity_loader(
                    week_start - timedelta(days=28), observed_through,
                )
            except Exception:
                activities, states = [], {}

        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, activity in enumerate(activities):
            activity_id = str(activity.get("activity_id") or f"row-{index}")
            if activity_id in seen:
                continue
            seen.add(activity_id)
            unique.append(activity)

        def activity_date(activity: dict[str, Any]) -> date | None:
            try:
                return date.fromisoformat(str(
                    activity.get("activity_date")
                    or activity.get("date")
                    or activity.get("start_time")
                )[:10])
            except (TypeError, ValueError):
                return None

        def number(value: Any) -> float:
            try:
                return max(0.0, float(value or 0))
            except (TypeError, ValueError):
                return 0.0

        def is_running(activity: dict[str, Any]) -> bool:
            activity_type = str(activity.get("activity_type") or "")
            return "run" in activity_type.lower() or "跑" in str(
                activity.get("activity_name") or ""
            )

        def summarize(
            items: list[dict[str, Any]], start: date, end: date,
        ) -> dict[str, Any]:
            valid = [
                item for item in items
                if (item_date := activity_date(item)) is not None
                and start <= item_date <= end
            ]
            active_dates: set[date] = set()
            running_dates: set[date] = set()
            running_count = 0
            total_distance_km = 0.0
            running_distance_km = 0.0
            total_duration_minutes = 0.0
            longest_activity: dict[str, Any] | None = None
            longest_running: dict[str, Any] | None = None
            activity_breakdown: dict[str, dict[str, Any]] = {}
            training_breakdown: dict[str, dict[str, Any]] = {}

            for item in valid:
                item_date = activity_date(item)
                if item_date is None:
                    continue
                distance_km = number(item.get("distance_meters")) / 1000
                duration_minutes = number(item.get("duration_seconds")) / 60
                item_type = str(item.get("activity_type") or "other")
                item_name = str(item.get("activity_name") or "实际活动")
                running = is_running(item)
                active_dates.add(item_date)
                total_distance_km += distance_km
                total_duration_minutes += duration_minutes
                bucket = activity_breakdown.setdefault(item_type, {
                    "label": item_type, "sessions": 0,
                    "distance_km": 0.0, "duration_minutes": 0.0,
                })
                bucket["sessions"] += 1
                bucket["distance_km"] += distance_km
                bucket["duration_minutes"] += duration_minutes
                fact = {
                    "date": str(item_date), "name": item_name, "type": item_type,
                    "distance_km": round(distance_km, 1),
                    "duration_minutes": round(duration_minutes),
                }
                if longest_activity is None or distance_km > float(
                    longest_activity.get("distance_km") or 0
                ):
                    longest_activity = fact
                if running:
                    running_count += 1
                    running_dates.add(item_date)
                    running_distance_km += distance_km
                    if longest_running is None or distance_km > float(
                        longest_running.get("distance_km") or 0
                    ):
                        longest_running = fact
                    analysis = item.get("training_analysis") or {}
                    try:
                        confidence = float(analysis.get("confidence") or 0)
                    except (TypeError, ValueError):
                        confidence = 0
                    primary_type = str(analysis.get("primary_type") or "")
                    if confidence >= .6 and primary_type not in {"", "unknown"}:
                        label = str(
                            analysis.get("display_name") or primary_type
                        )
                        training = training_breakdown.setdefault(label, {
                            "label": label, "sessions": 0, "distance_km": 0.0,
                        })
                        training["sessions"] += 1
                        training["distance_km"] += distance_km

            consecutive_running_days = 0
            current_streak = 0
            previous_day: date | None = None
            for running_date in sorted(running_dates):
                current_streak = (
                    current_streak + 1
                    if previous_day and running_date == previous_day + timedelta(days=1)
                    else 1
                )
                consecutive_running_days = max(consecutive_running_days, current_streak)
                previous_day = running_date

            def normalized_breakdown(
                values: dict[str, dict[str, Any]], *, with_duration: bool,
            ) -> list[dict[str, Any]]:
                result = []
                for item in values.values():
                    normalized = {
                        "label": item["label"], "sessions": item["sessions"],
                        "distance_km": round(float(item["distance_km"]), 1),
                    }
                    if with_duration:
                        normalized["duration_minutes"] = round(
                            float(item["duration_minutes"]),
                        )
                    result.append(normalized)
                return sorted(
                    result,
                    key=lambda item: (-float(item["distance_km"]), str(item["label"])),
                )

            return {
                "activity_count": len(valid),
                "active_days": len(active_dates),
                "running_activity_count": running_count,
                "running_days": len(running_dates),
                "consecutive_running_days": consecutive_running_days,
                "total_distance_km": round(total_distance_km, 1),
                "running_distance_km": round(running_distance_km, 1),
                "total_duration_minutes": round(total_duration_minutes),
                "longest_activity": longest_activity,
                "longest_running_activity": longest_running,
                "activity_breakdown": normalized_breakdown(
                    activity_breakdown, with_duration=True,
                ),
                "training_breakdown": normalized_breakdown(
                    training_breakdown, with_duration=False,
                ),
                "latest_activity_date": str(max(active_dates)) if active_dates else None,
            }

        current_activities = [
            item for item in unique
            if (item_date := activity_date(item)) is not None
            and week_start <= item_date <= observed_through
        ]
        current_summary = summarize(current_activities, week_start, observed_through)
        daily_summaries = [
            TrainingDaySummaryBuilder.build(
                target=week_start + timedelta(days=index),
                activities=unique,
                states=states,
                facts_cutoff=observed_through,
            )
            for index in range((observed_through - week_start).days + 1)
        ] if observed_through >= week_start else []

        coverage_dates = [] if observed_through < week_start else [
            week_start + timedelta(days=index)
            for index in range((observed_through - week_start).days + 1)
        ]
        incomplete_dates = [
            str(day) for day in coverage_dates
            if str(states.get(str(day)) or "unknown") in {
                "", "unknown", "syncing", "failed",
            }
        ]
        data_quality = {
            "status": "complete" if coverage_dates and not incomplete_dates else "incomplete",
            "sync_state": "current" if coverage_dates and not incomplete_dates else "unknown",
            "sync_coverage": {
                "week_start": str(week_start),
                "checked_through": str(observed_through),
                "expected_days": len(coverage_dates),
                "covered_days": len(coverage_dates) - len(incomplete_dates),
                "incomplete_dates": incomplete_dates,
            },
        }
        actual_summary = {
            "week_start": str(week_start),
            "week_end": str(week_end),
            "observed_through": str(observed_through),
            "daily_summaries": daily_summaries,
            **current_summary,
        }

        reference_weeks = []
        for offset in range(1, 5):
            reference_start = week_start - timedelta(days=offset * 7)
            reference_end = reference_start + timedelta(days=6)
            reference_dates = [
                reference_start + timedelta(days=index) for index in range(7)
            ]
            reference_incomplete = [
                str(day) for day in reference_dates
                if str(states.get(str(day)) or "unknown") in {
                    "", "unknown", "syncing", "failed",
                }
            ]
            if reference_incomplete:
                continue
            reference_summary = summarize(unique, reference_start, reference_end)
            reference_weeks.append({
                "week_start": str(reference_start),
                "week_end": str(reference_end),
                "running_km": reference_summary["running_distance_km"],
                "running_days": reference_summary["running_days"],
                "activity_count": reference_summary["activity_count"],
                "longest_running_km": float(
                    (reference_summary.get("longest_running_activity") or {}).get(
                        "distance_km",
                    ) or 0
                ),
            })

        reference_count = len(reference_weeks)
        comparison_status = (
            "sufficient" if reference_count >= 3
            else "limited" if reference_count else "unavailable"
        )

        def comparison(current: float, field: str) -> dict[str, Any]:
            if not reference_weeks:
                return {
                    "current": round(current, 1), "reference_average": None,
                    "delta": None, "delta_percent": None, "direction": "unknown",
                }
            average = sum(float(item[field]) for item in reference_weeks) / reference_count
            delta = current - average
            delta_percent = (delta / average * 100) if average > 0 else None
            return {
                "current": round(current, 1),
                "reference_average": round(average, 1),
                "delta": round(delta, 1),
                "delta_percent": round(delta_percent) if delta_percent is not None else None,
                "direction": "higher" if delta > 0 else "lower" if delta < 0 else "stable",
            }

        longest_running_km = float(
            (actual_summary.get("longest_running_activity") or {}).get(
                "distance_km",
            ) or 0
        )
        trend_summary = {
            "comparison_status": comparison_status,
            "reference_week_count": reference_count,
            "reference_weeks": reference_weeks,
            "running_km": comparison(
                float(actual_summary["running_distance_km"]), "running_km",
            ),
            "running_days": comparison(
                float(actual_summary["running_days"]), "running_days",
            ),
            "longest_running_km": comparison(
                longest_running_km, "longest_running_km",
            ),
        }
        return actual_summary, data_quality, trend_summary, current_activities, states

    def _weekly_plan_context(
        self, week_start: date, week_end: date, observed_through: date,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve the scheme that actually overlapped the selected natural week."""
        scheme = next((
            resolved for day_offset in range((week_end - week_start).days, -1, -1)
            if (resolved := self._resolve_effective_scheme(week_start + timedelta(days=day_offset)))
        ), None)
        if not scheme:
            return None, None, None
        effective_from = max(week_start, _scheme_effective_from(scheme) or week_start)
        effective_to = min(
            observed_through,
            _scheme_effective_to(scheme) or week_end,
        )
        if effective_to < effective_from:
            return None, None, None
        week = self._materialize_week(scheme, effective_from)
        plan_context = {
            "status": "effective",
            "plan_id": scheme.get("plan_id"),
            "plan_version": int(scheme.get("version") or 0),
            "effective_from": str(effective_from),
            "effective_to": str(effective_to),
            "goal": copy.deepcopy(scheme.get("goal_snapshot") or scheme.get("goal") or {}),
        }
        return scheme, week, plan_context

    @staticmethod
    def _weekly_review_sections(
        actual: dict[str, Any], trend: dict[str, Any],
        data_quality: dict[str, Any], recovery: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a useful weekly review even when no training plan exists."""
        running_km = float(actual.get("running_distance_km") or 0)
        running_days = int(actual.get("running_days") or 0)
        active_days = int(actual.get("active_days") or 0)
        activity_count = int(actual.get("activity_count") or 0)
        duration = int(actual.get("total_duration_minutes") or 0)
        longest = actual.get("longest_running_activity") or {}
        longest_km = float(longest.get("distance_km") or 0)
        overview_headline = (
            f"本周跑步 {running_km:g} km，分布在 {running_days} 个跑步日；"
            f"最长单次 {longest_km:g} km。"
            if running_km else
            f"本周记录 {activity_count} 次运动，暂未记录到跑步。"
        )
        highlights = [
            f"共 {activity_count} 次运动、{active_days} 个运动日，累计 {duration} 分钟。",
        ]
        training_breakdown = actual.get("training_breakdown") or []
        if training_breakdown:
            highlights.append("训练结构：" + "；".join(
                f"{item.get('label')} {item.get('sessions')} 次 / {float(item.get('distance_km') or 0):g} km"
                for item in training_breakdown[:4]
            ) + "。")
        elif running_km:
            highlights.append("训练类型识别不足，暂不能可靠判断轻松跑、节奏跑等结构占比。")

        running_trend = trend.get("running_km") or {}
        reference_count = int(trend.get("reference_week_count") or 0)
        reference_average = running_trend.get("reference_average")
        delta = running_trend.get("delta")
        delta_percent = running_trend.get("delta_percent")
        if reference_count and reference_average is not None:
            direction = "增加" if float(delta or 0) > 0 else "减少" if float(delta or 0) < 0 else "持平"
            percent_text = (
                f"（{float(delta_percent):+.0f}%）" if delta_percent is not None else ""
            )
            reference_values = "、".join(
                f"{float(item.get('running_km') or 0):g}"
                for item in trend.get("reference_weeks") or []
            )
            trend_headline = (
                f"较最近 {reference_count} 个完整周平均 {float(reference_average):g} km"
                f"{direction} {abs(float(delta or 0)):g} km{percent_text}。"
            )
            trend_evidence = [
                f"参照周跑量：{reference_values} km。",
                f"本周跑步 {running_days} 天，近期完整周平均 {float((trend.get('running_days') or {}).get('reference_average') or 0):g} 天。",
            ]
        else:
            trend_headline = "可比较的完整历史周不足，暂不把缺失数据解释为训练变化。"
            trend_evidence = ["需要至少 1 个同步完整的历史自然周才能计算变化。"]

        flags: list[dict[str, str]] = []
        risk_level = "low"

        def add_flag(code: str, message: str, level: str) -> None:
            nonlocal risk_level
            flags.append({"code": code, "message": message})
            if level == "high" or (level == "medium" and risk_level == "low"):
                risk_level = level

        if delta_percent is not None and float(delta_percent) > 50:
            add_flag(
                "volume_increase",
                f"周跑量较近期完整周平均增加 {float(delta_percent):.0f}%，负荷跃升明显。",
                "high",
            )
        elif delta_percent is not None and float(delta_percent) > 30:
            add_flag(
                "volume_increase",
                f"周跑量较近期完整周平均增加 {float(delta_percent):.0f}%，需要关注负荷吸收。",
                "medium",
            )
        if int(actual.get("consecutive_running_days") or 0) >= 6:
            add_flag("recovery_density", "连续跑步天数较多，恢复窗口偏少。", "medium")
        if running_km and longest_km / running_km >= .45:
            add_flag("long_run_concentration", "最长单次占周跑量比例较高，单次负荷较集中。", "medium")

        recovery_data = recovery.get("recovery") or {}
        sleep_data = recovery.get("sleep") or {}
        load_data = recovery.get("training_load") or {}
        recovery_score = recovery_data.get("overall_score")
        if recovery_score is None:
            recovery_score = recovery_data.get("score")
        try:
            recovery_score_value = float(recovery_score) if recovery_score is not None else None
        except (TypeError, ValueError):
            recovery_score_value = None
        load_status = str(load_data.get("acwr_status") or load_data.get("status") or "")
        if load_status in {"overreaching", "high_risk", "very_high"}:
            add_flag("load_risk", "截至周末的训练负荷指标处于高风险区间。", "high")
        if recovery_score_value is not None and recovery_score_value < 50:
            add_flag("low_recovery", f"截至周末的恢复评分为 {recovery_score_value:g}，恢复偏低。", "medium")
        try:
            sleep_hours = float(sleep_data.get("total_hours"))
        except (TypeError, ValueError):
            sleep_hours = None
        if sleep_hours is not None and sleep_hours < 6:
            add_flag("short_sleep", f"最近一次睡眠仅 {sleep_hours:g} 小时。", "medium")

        incomplete = (data_quality.get("sync_coverage") or {}).get("incomplete_dates") or []
        uncertainties = []
        if incomplete:
            uncertainties.append(f"仍有 {len(incomplete)} 天同步状态不完整，结论只基于当前已见数据。")
        if not recovery:
            uncertainties.append("缺少周末恢复快照，无法判断主观疲劳、睡眠和负荷状态。")
        if not flags:
            risk_headline = "当前数据未识别到明确的负荷或恢复风险信号。"
        elif risk_level == "high":
            risk_headline = "本周存在明显负荷跃升或恢复风险，下周应先吸收训练。"
        else:
            risk_headline = "本周有需要观察的负荷或恢复信号，下周不宜继续加码。"

        actions: list[str] = []
        if incomplete:
            actions.append("先补齐缺失日期的同步数据，再据此调整下一周训练。")
        if any(item["code"] == "volume_increase" for item in flags):
            actions.append("下周先不要继续增加总跑量，并安排 1–2 个完整恢复日。")
        if any(item["code"] in {"load_risk", "low_recovery", "short_sleep"} for item in flags):
            actions.append("关键训练之间至少留出 48 小时；疲劳未改善时改为轻松跑或休息。")
        if training_breakdown:
            actions.append("保留一项本周有效的训练刺激，其余跑步以可对话强度完成。")
        elif running_km:
            actions.append("下周记录每次跑步的主观强度，帮助判断训练结构是否均衡。")
        if len(actions) < 2:
            actions.append("维持当前训练频率，优先保证轻松日和恢复日之间有清晰区分。")
        if len(actions) < 2:
            actions.append("周末结合睡眠、疲劳和疼痛反馈，再决定是否增加负荷。")

        return {
            "overview": {"headline": overview_headline, "highlights": highlights},
            "trend": {
                "headline": trend_headline, "evidence": trend_evidence,
                "comparison_status": trend.get("comparison_status") or "unavailable",
                "reference_week_count": reference_count,
            },
            "recovery_and_risk": {
                "level": "unknown" if not recovery and not flags else risk_level,
                "headline": risk_headline, "flags": flags,
                "uncertainties": uncertainties,
            },
            "next_week": {
                "headline": "下周先根据本周负荷和恢复事实行动，不要求先创建训练方案。",
                "actions": actions[:3],
            },
        }

    def review_week(
        self,
        *,
        target: date | None = None,
        include_ai: bool = True,
    ) -> dict[str, Any]:
        target = target or date.today()
        actual_summary, data_quality, trend_summary, activities, states = self._weekly_actual_context(target)
        week_start = date.fromisoformat(actual_summary["week_start"])
        week_end = date.fromisoformat(actual_summary["week_end"])
        observed_through = date.fromisoformat(actual_summary["observed_through"])
        scheme, week, plan_context = self._weekly_plan_context(
            week_start, week_end, observed_through,
        )
        capacity = self.capacity_profile(target=observed_through)
        recovery = self._setup_context(target=observed_through).get("recovery_snapshot") or {}
        review_sections = self._weekly_review_sections(
            actual_summary, trend_summary, data_quality, recovery,
        )
        plan_execution_summary = None
        adaptation_signal = None
        decision = None
        recommendation = None
        rationale = None

        if scheme and week and plan_context:
            comparison_start = date.fromisoformat(plan_context["effective_from"])
            comparison_end = date.fromisoformat(plan_context["effective_to"])
            workouts = [
                item for item in ActivityMatcher.match(
                    week["workouts"], activities, states, observed_through,
                )
                if comparison_start <= date.fromisoformat(item["date"]) <= comparison_end
            ]
            training_days = [item for item in workouts if item.get("type") != "rest"]
            completed = [
                item for item in training_days
                if item.get("execution_status") == "completed"
            ]
            target_km = round(sum(float(item.get("distance_km") or 0) for item in training_days), 1)
            completed_km = round(sum(
                float(activity.get("distance_km") or 0)
                for item in completed for activity in item.get("actual_activities", [])
            ), 1)
            session_ratio = len(completed) / len(training_days) if training_days else 1.0
            volume_ratio = completed_km / target_km if target_km else 0.0
            plan_execution_summary = {
                "week_start": actual_summary["week_start"],
                "week_end": actual_summary["week_end"],
                "comparison_start": str(comparison_start),
                "comparison_end": str(comparison_end),
                "planned_sessions": len(training_days),
                "completed_sessions": len(completed),
                "session_completion_ratio": round(session_ratio, 2),
                "target_km": target_km,
                "completed_km": completed_km,
                "volume_completion_ratio": round(volume_ratio, 2),
                "sync_state": data_quality["sync_state"],
                "sync_coverage": copy.deepcopy(data_quality["sync_coverage"]),
            }
            if data_quality["sync_state"] == "unknown":
                recommendation = "insufficient_data"
                rationale = "运动同步状态未知，暂不把未匹配训练当作跳过"
            elif session_ratio < 0.5 or volume_ratio < 0.55:
                recommendation = "scheme_revision"
                rationale = "本周执行偏差较大，需要结合原因判断是否重规划"
            elif session_ratio < 0.8 or volume_ratio < 0.8:
                recommendation = "local_adjustment"
                rationale = "存在局部偏差，优先调整下一周负荷而非重写整个周期"
            else:
                recommendation = "keep"
                rationale = "本周执行与计划基本一致，可保持当前阶段方向"

            sustainable = float((capacity.get("current_sustainable_capacity") or {}).get("weekly_km") or 0)
            historical = float((capacity.get("historical_proven_capacity") or {}).get("weekly_km") or 0)
            recovery_score = float((capacity.get("current_readiness") or {}).get("recovery_score") or 0)
            progression_action = "hold"
            progression_reason = "先维持当前周结构，等待更多连续周事实。"
            if data_quality["sync_state"] == "unknown" or capacity.get("confidence") == "low":
                progression_action = "insufficient_data"
                progression_reason = "同步覆盖或能力档案不足，不能据此推进或降载。"
            elif recovery_score and recovery_score < 45:
                progression_action = "deload"
                progression_reason = "恢复信号偏低，优先减量并观察，而不是按日历推进。"
            elif completed_km > max(sustainable, historical, target_km) * 1.2:
                progression_action = "deload"
                progression_reason = "本周实际负荷明显超过当前可持续或历史参照，需要先吸收负荷。"
            elif session_ratio >= .8 and volume_ratio >= .9 and sustainable >= target_km * 1.08:
                progression_action = "accelerate"
                progression_reason = "当前周执行稳定且近期可持续能力高于方案负荷，可生成加快推进提案供确认。"
            elif session_ratio >= .8 and volume_ratio >= .8:
                progression_action = "advance"
                progression_reason = "执行与恢复符合预期，可按已确认的阶段节奏推进。"
            decision = {
                "type": "progression_decision", "decision_id": uuid.uuid4().hex,
                "plan_id": scheme["plan_id"], "plan_version": scheme["version"],
                "week_id": week["week_id"], "action": progression_action,
                "requires_confirmation": progression_action in {"accelerate", "deload"},
                "counts_toward_phase": bool(week.get("counts_toward_phase", True)),
                "rationale": progression_reason,
                "facts_cutoff": capacity.get("facts_cutoff"), "created_at": _now(),
            }
            adaptation_signal = {
                "recommendation": recommendation,
                "rationale": rationale,
                "evidence": [
                    f"完成 {len(completed)}/{len(training_days)} 次训练",
                    f"完成跑量 {completed_km:g}/{target_km:g} km",
                ],
            }

        # 结构判定需要个人阈值：用自然周实际活动构建 AthleteBaseline（仅档案锚定，不猜测）
        from .training_analysis import AthleteBaselineBuilder

        try:
            baseline = AthleteBaselineBuilder().build(
                activities, profile=self._athlete_profile_document(target),
            )
        except Exception:
            baseline = None
        daily_summaries = [
            TrainingDaySummaryBuilder.prepare_daily_analysis(
                self._compact_day_session_summaries(
                TrainingDaySummaryBuilder.build(
                    target=week_start + timedelta(days=index),
                    activities=activities,
                    states=states,
                    facts_cutoff=observed_through,
                ),
                baseline=baseline,
                )
            )
            for index in range(max(0, (observed_through - week_start).days + 1))
        ]
        training_day_summary = TrainingDaySummaryBuilder.aggregate(
            daily_summaries, start=week_start, end=observed_through,
        )
        actual_summary["daily_summaries"] = copy.deepcopy(daily_summaries)
        quality_sessions = copy.deepcopy(
            training_day_summary.get("quality_sessions") or []
        )
        daily_prerequisites = self._weekly_prerequisite_manifest(
            daily_summaries,
            week_start=week_start,
            observed_through=observed_through,
        )
        review_sections["quality_sessions"] = self._quality_session_review(
            quality_sessions, daily_summaries,
        )
        facts = {
            "natural_week_actual": actual_summary,
            "natural_week_trend": trend_summary,
            "training_day_summary": training_day_summary,
            "weekly_review_sections": review_sections,
            "weekly_prerequisite_manifest": daily_prerequisites,
            "quality_sessions": quality_sessions,
            "recovery_snapshot": recovery,
        }
        if scheme and plan_execution_summary:
            facts.update({
                "active_scheme": scheme,
                "natural_week_progress": plan_execution_summary,
                "adaptation_signal": adaptation_signal,
                "capacity_profile": capacity,
                "data_quality": data_quality,
            })
        completed_plan_week = bool(
            scheme and plan_execution_summary and week_end < date.today()
        )
        if include_ai:
            finding, trace, fallback_reason = self._run_read_skill(
                "review-training-plan" if completed_plan_week else "review-training-week",
                facts=facts,
            )
        else:
            finding, trace, fallback_reason = None, [], "在线 AI 解释未请求"
        plan_review = None
        if completed_plan_week and finding is not None:
            # 完整周的单次 AI 输出同时承担事实解释和方案审阅。同步覆盖不足时，
            # 本地只允许收敛为 hold，避免模型把缺失数据解释成未执行。
            if (
                data_quality["status"] != "complete"
                or capacity.get("confidence") == "low"
                or finding.get("status") != "ok"
            ):
                finding["decision"] = "hold"
                finding["status"] = "insufficient_data"
                uncertainties = list(finding.get("uncertainties") or [])
                if "自然周数据覆盖不足，暂不调整方案" not in uncertainties:
                    uncertainties.append("自然周数据覆盖不足，暂不调整方案")
                finding["uncertainties"] = uncertainties
            plan_review = {
                key: copy.deepcopy(finding.get(key))
                for key in (
                    "review_stage", "decision", "scope",
                    "recommended_entry_phase", "confidence", "evidence",
                    "adjustments", "risk_flags", "safety_hold",
                    "user_explanation", "handoff",
                )
            }
            plan_review["handoff"] = {
                "status": "review_pending",
                "requires_user_confirmation": True,
            }
            finding = {
                key: copy.deepcopy(finding.get(key))
                for key in (
                    "skill", "status", "conclusion", "evidence",
                    "uncertainties", "risk_flags", "recommendations",
                )
            }
        if finding is None:
            if adaptation_signal and recommendation and rationale:
                finding = {
                    "skill": "review-training-week",
                    "status": "insufficient_data" if recommendation == "insufficient_data" else "ok",
                    "conclusion": rationale,
                    "evidence": adaptation_signal["evidence"],
                    "uncertainties": ["未使用在线 AI 教练推理"] + (
                        ["运动同步状态未知"] if data_quality["sync_state"] == "unknown" else []
                    ),
                    "risk_flags": [],
                    "recommendations": [{
                        "keep": "保持当前方案，并继续观察恢复与关键课质量",
                        "local_adjustment": "先生成下一周局部调整提案",
                        "scheme_revision": "补充偏离原因后生成方案级重规划提案",
                        "insufficient_data": "先完成运动数据同步再复盘",
                    }[recommendation]],
                }
            else:
                has_complete_data = data_quality["status"] == "complete"
                risk = review_sections["recovery_and_risk"]
                finding = {
                    "skill": "review-training-week",
                    "status": "ok" if has_complete_data else "insufficient_data",
                    "conclusion": " ".join([
                        review_sections["overview"]["headline"],
                        review_sections["trend"]["headline"],
                    ]),
                    "evidence": [
                        *review_sections["overview"]["highlights"],
                        *review_sections["trend"]["evidence"],
                    ],
                    "uncertainties": [
                        "未使用在线 AI 教练推理",
                        *risk["uncertainties"],
                    ],
                    "risk_flags": [item["message"] for item in risk["flags"]],
                    "recommendations": review_sections["next_week"]["actions"],
                }
        if completed_plan_week and plan_review is None:
            plan_review = {
                "review_stage": "execution_review",
                "decision": "hold",
                "scope": "next_week",
                "recommended_entry_phase": None,
                "confidence": 0.0,
                "evidence": copy.deepcopy((adaptation_signal or {}).get("evidence") or []),
                "adjustments": [],
                "risk_flags": copy.deepcopy(finding.get("risk_flags") or []),
                "safety_hold": None,
                "user_explanation": "方案审阅暂不可用，保持当前安排并等待完整事实。",
                "handoff": {
                    "status": "review_pending",
                    "requires_user_confirmation": True,
                },
            }
        if completed_plan_week and plan_review is not None and decision is not None:
            decision.update({
                "action": plan_review["decision"],
                "requires_confirmation": True,
                "rationale": str(
                    plan_review.get("user_explanation")
                    or finding.get("conclusion")
                    or "方案审阅结果需用户确认。"
                ),
            })
        return {
            "type": "training_week_review",
            "week_id": week_start.strftime("%G-W%V"),
            "plan_context": plan_context,
            "plan_id": scheme.get("plan_id") if scheme else None,
            "plan_version": int(scheme.get("version") or 0) if scheme else None,
            "generation_mode": "skill" if fallback_reason is None else "deterministic_fallback",
            "fallback_reason": fallback_reason,
            "actual_summary": actual_summary,
            "training_day_summary": training_day_summary,
            "daily_prerequisites": daily_prerequisites,
            "quality_sessions": quality_sessions,
            "trend_summary": trend_summary,
            "review_sections": review_sections,
            "data_quality": data_quality,
            "plan_execution_summary": plan_execution_summary,
            "execution_summary": plan_execution_summary,
            "adaptation_signal": adaptation_signal,
            "progression_decision": decision,
            "plan_review": plan_review,
            "capacity_profile": capacity,
            "finding": finding,
            "trace": trace,
        }

    def create_weekly_report(self, *, target: date | None = None) -> dict[str, Any]:
        """固化已结束自然周；当前周只返回不持久化的进度检查。"""
        target = target or date.today()
        review = self.review_week(target=target)
        summary = review["actual_summary"]
        if date.fromisoformat(summary["week_end"]) >= date.today():
            checkpoint = {
                "type": "weekly_checkpoint",
                "week_id": review["week_id"],
                "week_start": summary["week_start"],
                "week_end": summary["week_end"],
                "data_as_of": review["capacity_profile"].get("facts_cutoff"),
                "data_quality": copy.deepcopy(review["data_quality"]),
                "actual_summary": copy.deepcopy(summary),
                "training_day_summary": copy.deepcopy(review["training_day_summary"]),
                "daily_prerequisites": copy.deepcopy(review["daily_prerequisites"]),
                "quality_sessions": copy.deepcopy(review["quality_sessions"]),
                "trend_summary": copy.deepcopy(review["trend_summary"]),
                "review_sections": copy.deepcopy(review["review_sections"]),
                "plan_context": copy.deepcopy(review["plan_context"]),
                "plan_id": review["plan_id"],
                "plan_version": review["plan_version"],
                "plan_execution_summary": copy.deepcopy(review["plan_execution_summary"]),
                "execution_summary": copy.deepcopy(review["execution_summary"]),
                "finding": {
                    **copy.deepcopy(review["finding"]),
                    "conclusion": (
                        "本周尚未结束，当前只展示截至今日的进度，不形成推进或减量决定。"
                        if review["plan_context"] else
                        "本周尚未结束，当前只展示截至今日的实际运动与恢复，不评价计划完成率。"
                    ),
                },
                "adaptation_signal": copy.deepcopy(review["adaptation_signal"]),
                "progression_decision": ({
                    **copy.deepcopy(review["progression_decision"]),
                    "action": "not_final", "requires_confirmation": False,
                    "rationale": "自然周尚未结束，等待完整周事实后再进行正式复盘。",
                } if review["progression_decision"] else None),
                "plan_review": copy.deepcopy(review.get("plan_review")),
                "training_url": (
                    f"/training?week={summary['week_start']}"
                    if review["plan_context"] else None
                ),
                "sync_url": f"/sync?date={summary['week_start']}&from=reports&return_to=/reports?tab=weekly%26week={summary['week_start']}#single-sync",
            }
            return checkpoint
        report = {
            "type": "weekly_report",
            "report_id": f"weekly-{summary['week_start']}",
            "week_id": review["week_id"],
            "week_start": summary["week_start"],
            "week_end": summary["week_end"],
            "data_as_of": review["capacity_profile"].get("facts_cutoff"),
            "data_quality": copy.deepcopy(review["data_quality"]),
            "actual_summary": copy.deepcopy(summary),
            "training_day_summary": copy.deepcopy(review["training_day_summary"]),
            "daily_prerequisites": copy.deepcopy(review["daily_prerequisites"]),
            "quality_sessions": copy.deepcopy(review["quality_sessions"]),
            "trend_summary": copy.deepcopy(review["trend_summary"]),
            "review_sections": copy.deepcopy(review["review_sections"]),
            "plan_context": copy.deepcopy(review["plan_context"]),
            "plan_id": review["plan_id"],
            "plan_version": review["plan_version"],
            "plan_execution_summary": copy.deepcopy(review["plan_execution_summary"]),
            "execution_summary": copy.deepcopy(review["execution_summary"]),
            "adaptation_signal": copy.deepcopy(review["adaptation_signal"]),
            "progression_decision": copy.deepcopy(review["progression_decision"]),
            "plan_review": copy.deepcopy(review.get("plan_review")),
            "capacity_profile": copy.deepcopy(review["capacity_profile"]),
            "training_handoff": {
                "status": (
                    "proposal_available"
                    if (
                        review["progression_decision"]
                        and review["progression_decision"].get("action")
                        in {"accelerate", "deload", "replan"}
                    )
                    or (
                        review["adaptation_signal"]
                        and review["adaptation_signal"].get("recommendation")
                        == "scheme_revision"
                    )
                    else "not_required"
                ),
                "report_id": f"weekly-{summary['week_start']}",
                "week_id": review["week_id"],
                "plan_id": review["plan_id"],
                "plan_version": review["plan_version"],
                "progression_decision_id": (
                    review["progression_decision"].get("decision_id")
                    if review["progression_decision"] else None
                ),
            },
            "finding": copy.deepcopy(review["finding"]),
            "generation_mode": review["generation_mode"],
            "fallback_reason": review.get("fallback_reason"),
            "training_url": (
                f"/training?week={summary['week_start']}"
                if review["plan_context"] else None
            ),
            "sync_url": f"/sync?date={summary['week_start']}&from=reports&return_to=/reports?tab=weekly%26week={summary['week_start']}#single-sync",
            "created_at": _now(), "updated_at": _now(),
        }
        self.repository.save_weekly_report(report)
        return report

    def list_weekly_reports(self) -> list[dict[str, Any]]:
        return self.repository.list_weekly_reports()

    def list_adjustment_records(self) -> list[dict[str, Any]]:
        return self.repository.list_adjustment_records()

    def race_strategy(self, race_context: dict[str, Any]) -> dict[str, Any]:
        scheme = self.repository.get_active()
        context = self.repository.get_coaching_context()
        if not scheme or context["coaching_mode"] != "race_preparation":
            raise TrainingError("race_plan_required", "比赛策略需要已确认的赛事备赛方案", status_code=409)
        goal = scheme.get("goal_snapshot") or {}
        target_date = _coerce_date(goal.get("target_date"), field="target_date")
        days_remaining = (target_date - date.today()).days
        if days_remaining < 0 or days_remaining > 21:
            raise TrainingError(
                "race_strategy_not_available",
                "比赛策略在赛前 21 天内开放；当前继续执行训练方案即可。",
                status_code=409,
            )
        setup = self._setup_context()
        normalized_context = {
            "target_date": str(target_date),
            "days_remaining": days_remaining,
            "course": str(race_context.get("course") or "").strip(),
            "weather": str(race_context.get("weather") or "").strip(),
            "fueling_experience": str(race_context.get("fueling_experience") or "").strip(),
        }
        finding, trace, fallback_reason = self._run_read_skill(
            "prepare-race-strategy",
            facts={
                "active_scheme": scheme,
                "race_context": normalized_context,
                "athlete_baseline": setup.get("athlete_profile") or {},
                "recovery_snapshot": setup.get("recovery_snapshot") or {},
            },
        )
        uncertainties = []
        if not normalized_context["course"]:
            uncertainties.append("赛道信息未知")
        if not normalized_context["weather"]:
            uncertainties.append("天气信息未知")
        if not normalized_context["fueling_experience"]:
            uncertainties.append("补给经验未知")
        if finding is None:
            finding = {
                "skill": "prepare-race-strategy",
                "status": "ok",
                "conclusion": "采用保守开局、稳定中段、状态允许再加速的比赛执行框架。",
                "evidence": [
                    f"目标赛事还有 {days_remaining} 天",
                    f"当前阶段：{(scheme.get('current_phase') or {}).get('name', '减量与准备')}",
                ],
                "uncertainties": ["未使用在线 AI 教练推理", *uncertainties],
                "risk_flags": [],
                "recommendations": [
                    "前段以可控体感开局，不追随明显快于自身计划的选手",
                    "沿用训练中验证过的补给和装备，不在比赛日首次尝试新品",
                    "赛前完成轻量热身；高温、肠胃不适或异常状态时主动降级目标",
                ],
            }
        else:
            finding["uncertainties"] = list(dict.fromkeys([
                *finding.get("uncertainties", []), *uncertainties,
            ]))
        return {
            "type": "race_strategy",
            "plan_id": scheme["plan_id"],
            "plan_version": scheme["version"],
            "generation_mode": "skill" if fallback_reason is None else "deterministic_fallback",
            "fallback_reason": fallback_reason,
            "race_context": normalized_context,
            "finding": finding,
            "trace": trace,
        }

    def _materialize_week(
        self, scheme: dict[str, Any], target: date, *, persist: bool = False,
    ) -> dict[str, Any]:
        monday, sunday = natural_week_bounds(target)
        overrides = scheme.get("week_overrides") or {}
        effective_from = _scheme_effective_from(scheme)
        activation = scheme.get("activation") or {}
        is_bridge = bool(
            activation.get("bridge_week", {}).get("required")
            and effective_from and monday <= effective_from <= sunday
        )
        workouts = []
        for pattern in scheme.get("weekly_pattern", []):
            workout_date = monday + timedelta(days=int(pattern["weekday"]))
            item = copy.deepcopy(overrides.get(str(workout_date), pattern))
            safety_feedback = next(
                (
                    feedback for feedback in self.repository.feedback_for_date(workout_date)
                    if feedback.get("feedback_type") == "pain"
                    and feedback.get("same_day_safety_guidance", {}).get("blocks_intensity")
                ),
                None,
            )
            if safety_feedback:
                item.update({
                    "title": "今日安全指引：停止跑步并观察",
                    "type": "rest", "purpose": "疼痛反馈后的当日安全安排",
                    "distance_km": 0, "duration_minutes": 0,
                    "intensity": "不进行跑步训练", "is_key": False,
                    "safety_guidance": copy.deepcopy(
                        safety_feedback.get("same_day_safety_guidance")
                    ),
                })
            if is_bridge and effective_from and workout_date < effective_from:
                item.update({
                    "title": "启用前：不纳入本方案",
                    "type": "rest", "purpose": "方案尚未生效",
                    "distance_km": 0, "duration_minutes": 0,
                    "intensity": "不适用", "is_key": False,
                })
            elif is_bridge and item.get("type") not in {"rest", "easy"}:
                item.update({
                    "title": "衔接周轻松跑", "type": "easy", "is_key": False,
                    "intensity": "能完整对话，结束仍有余量",
                    "purpose": "平稳进入训练节奏，不计入阶段推进",
                    "distance_km": round(float(item.get("distance_km") or 0) * .65, 1),
                })
                item.pop("training_prescription", None)
            item.update({
                "workout_id": f"{scheme['plan_id']}-{workout_date}",
                "date": str(workout_date),
                "weekday_name": WEEKDAY_NAMES[workout_date.weekday()],
                "source_version": scheme["version"],
            })
            workouts.append(ensure_training_prescription(
                item, synthesize_steps=bool(is_bridge and item.get("type") == "easy"),
            ))
        week = {
            "type": "weekly_plan", "plan_id": scheme["plan_id"],
            "scheme_version": scheme["version"], "week_id": monday.strftime("%G-W%V"),
            "week_start": str(monday), "week_end": str(sunday), "status": "active",
            "goal": "按计划完成关键课并保持恢复", "workouts": workouts,
            "week_kind": "bridge" if is_bridge else "standard",
            "counts_toward_phase": not is_bridge,
            "updated_at": _now(),
        }
        if persist:
            self.repository.save_week(week)
        return week

    @staticmethod
    def _pain_safety_guidance(pain: dict[str, Any]) -> dict[str, Any]:
        severity = int(pain.get("severity") or 0)
        affects_daily_life = bool(pain.get("affects_daily_life"))
        stop = severity >= 4 or affects_daily_life
        return {
            "type": "same_day_safety_guidance",
            "status": "stop_and_assess" if stop else "low_intensity_only",
            "source": "pain_feedback_safety_router",
            "read_only": True,
            "guidance": (
                "今天停止跑步，不追赶或补偿训练；若疼痛持续、加重或影响日常活动，请进一步评估。"
                if stop else
                "今天不做高强度；仅在无痛且状态稳定时进行轻松低强度活动，出现加重立即停止。"
            ),
            "blocks_intensity": True,
            "created_at": _now(),
        }

    def submit_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        scheme = self.repository.get_active()
        if not scheme or scheme.get("status") != "active":
            raise TrainingError("active_plan_required", "请先创建并确认训练方案", status_code=409)
        raw_feedback_type = str(payload.get("feedback_type") or "")
        if raw_feedback_type not in FEEDBACK_TYPES:
            raise TrainingError("invalid_feedback_type", "不支持的反馈类型")
        feedback_type = (
            "constraint_change"
            if raw_feedback_type in _CONSTRAINT_FEEDBACK_ALIASES
            else raw_feedback_type
        )
        target = _coerce_date(payload.get("target_date") or date.today(), field="target_date")
        new_available_days = None
        if feedback_type == "constraint_change" and payload.get("new_available_days") is not None:
            new_available_days = _coerce_days(payload.get("new_available_days"))
        affected_dates = payload.get("affected_dates")
        if affected_dates is not None:
            if not isinstance(affected_dates, list) or any(
                _coerce_date(item, field="affected_dates") < date.today()
                for item in affected_dates
            ):
                raise TrainingError("invalid_affected_dates", "affected_dates 必须是今天起的日期数组")
        if feedback_type == "pain":
            pain = payload.get("pain") or {}
            if not pain.get("location") or pain.get("severity") is None:
                raise TrainingError("pain_details_required", "疼痛反馈需要部位和 1–10 严重程度")
            severity = int(pain["severity"])
            if not 1 <= severity <= 10:
                raise TrainingError("invalid_pain_severity", "疼痛程度需在 1–10 之间")
        if feedback_type == "post_workout":
            completion_status = str(payload.get("completion_status") or "")
            if (
                completion_status != "skipped"
                or not payload.get("planned_workout_id")
                or not str(payload.get("idempotency_key") or "").strip()
            ):
                raise TrainingError(
                    "skipped_confirmation_required",
                    "只有用户明确选择“今天没练”并携带 planned_workout_id、idempotency_key 才能记录 skipped",
                )
            if target > date.today():
                raise TrainingError("future_workout_immutable", "不能为未来课次记录训练结果")
            existing = self.repository.feedback_by_idempotency(
                scheme["plan_id"], str(payload["idempotency_key"]).strip(),
            )
            if existing:
                return existing
        feedback = {
            "type": "training_feedback", "feedback_id": uuid.uuid4().hex,
            "plan_id": scheme["plan_id"], "plan_version": scheme["version"],
            "feedback_type": feedback_type, "target_date": str(target),
            "raw_feedback_type": raw_feedback_type,
            "constraint_kind": (
                _CONSTRAINT_FEEDBACK_ALIASES.get(raw_feedback_type)
                or (
                    "time_limited"
                    if feedback_type == "constraint_change"
                    and payload.get("available_minutes") not in (None, "")
                    else None
                )
                or (
                    "schedule_conflict"
                    if feedback_type == "constraint_change" and payload.get("affected_dates")
                    else None
                )
            ),
            "available_minutes": payload.get("available_minutes"),
            "affected_dates": [str(_coerce_date(item, field="affected_dates")) for item in affected_dates or []],
            "new_available_days": new_available_days,
            "pain": payload.get("pain") or {}, "note": str(payload.get("note") or "").strip(),
            "planned_workout_id": payload.get("planned_workout_id"),
            "idempotency_key": str(payload.get("idempotency_key") or "").strip() or None,
            "created_at": _now(),
        }
        if feedback_type == "pain":
            feedback["same_day_safety_guidance"] = self._pain_safety_guidance(feedback["pain"])
        if feedback_type == "post_workout":
            completion_status = str(payload.get("completion_status") or "")
            if completion_status and completion_status != "skipped":
                raise TrainingError(
                    "invalid_completion_status",
                    "训练后只能由用户明确记录“今天没练”，不能手动写入完成或部分完成",
                )
            if completion_status:
                feedback["completion_status"] = "skipped"
        self.repository.save_feedback(feedback)
        return feedback

    def propose(self, feedback_id: str) -> dict[str, Any]:
        with self._lock:
            feedback = self.repository.get_feedback(feedback_id)
            if not feedback:
                raise TrainingError("feedback_not_found", "训练反馈不存在", status_code=404)
            scheme = self.repository.get_active()
            if not scheme or scheme["plan_id"] != feedback.get("plan_id"):
                raise TrainingError("plan_changed", "反馈对应的训练方案已不再生效", status_code=409)
            if (
                feedback.get("feedback_type") == "constraint_change"
                and feedback.get("new_available_days")
            ):
                # 长期星期变化沿用同一反馈/提案主线，但默认下个自然周才生效，
                # 当前版本与本周局部安排保持不变。
                proposal = self.propose_scheme_revision({
                    "trigger": "constraints_change",
                    "reason": feedback.get("note") or "长期可训练日发生变化",
                    "constraints": {
                        "available_days": feedback["new_available_days"],
                    },
                })
                proposal["feedback_id"] = feedback_id
                proposal["target_date"] = feedback["target_date"]
                next_monday = date.today() + timedelta(
                    days=(7 - date.today().weekday()) or 7,
                )
                proposal["effective_from"] = str(next_monday)
                self.repository.save_proposal(proposal)
                return proposal
            target = date.fromisoformat(feedback["target_date"])
            if target < date.today():
                raise TrainingError(
                    "historical_workout_immutable",
                    "历史训练安排不可修改；可以保留训练后反馈，但不能生成追溯调整",
                    status_code=409,
                )
            home = self.home(today=target)
            original = home.get("today")
            if not original:
                raise TrainingError("workout_not_found", "目标日期没有可调整的训练", status_code=404)
            proposed, reason, impact, risk_flags = self._adjust(original, feedback, home["week"])
            recovery = self._setup_context().get("recovery_snapshot") or {}
            coaching_finding, planning_trace, fallback_reason = self._run_read_skill(
                "propose-training-adjustment",
                facts={
                    "active_scheme": scheme,
                    "training_feedback": feedback,
                    "recovery_snapshot": recovery,
                },
            )
            proposal = {
                "type": "adjustment_proposal", "proposal_id": uuid.uuid4().hex,
                "feedback_id": feedback_id, "plan_id": scheme["plan_id"],
                "base_version": scheme["version"], "status": "pending", "scope": "today",
                "target_date": feedback["target_date"], "reason": reason,
                "changes": [{"date": feedback["target_date"], "before": original, "after": proposed}],
                "impact": impact, "risk_flags": risk_flags,
                "data_basis": [f"用户反馈：{feedback['feedback_type']}", f"方案版本：v{scheme['version']}"],
                "generation_mode": "skill" if fallback_reason is None else "deterministic_fallback",
                "fallback_reason": fallback_reason,
                "coaching_finding": coaching_finding,
                "planning_trace": planning_trace,
                "same_day_safety_guidance": copy.deepcopy(
                    feedback.get("same_day_safety_guidance")
                ),
                "created_at": _now(), "expires_at": str(date.today() + timedelta(days=1)),
            }
            # 同一调整范围只保留最新待决策提案，旧 pending 标记 superseded
            self.repository.supersede_pending_proposals(
                str(scheme["plan_id"]), "today",
                target_date=feedback["target_date"],
            )
            self.repository.save_proposal(proposal)
            return proposal

    def propose_scheme_revision(self, payload: dict[str, Any]) -> dict[str, Any]:
        """根据执行偏差或长期约束变化创建方案级重规划提案。"""
        with self._lock:
            scheme = self.repository.get_active()
            context = self.repository.get_coaching_context()
            if not scheme or context["coaching_mode"] != "race_preparation":
                raise TrainingError("race_plan_required", "方案级重规划需要已确认的赛事备赛方案", status_code=409)
            reason = str(payload.get("reason") or "").strip()
            if not reason and not payload.get("source_report"):
                raise TrainingError("revision_reason_required", "请说明为什么需要重规划")
            trigger = str(payload.get("trigger") or "execution_deviation")
            if trigger not in {
                "execution_deviation", "goal_change", "constraints_change",
                "long_break", "race_rescheduled", "review_recommendation",
            }:
                raise TrainingError("invalid_revision_trigger", "不支持的方案重规划原因")

            current_goal = self.goals.get(str(scheme.get("goal_id") or ""))
            goal = current_goal or copy.deepcopy(scheme.get("goal_snapshot") or {})
            if not goal.get("target_date"):
                raise TrainingError("race_date_required", "方案级重规划仍需要明确赛事日期")
            # 赛事延期：重规划请求可携带新赛事日期，候选按新日期生成周期；
            # 不直接改已生效 goal（update_goal 有预览保护），确认提案时才落库。
            new_target_date = payload.get("new_target_date") or (
                payload.get("target_date")
                if trigger == "race_rescheduled" else None
            )
            if new_target_date:
                try:
                    new_date = date.fromisoformat(str(new_target_date)[:10])
                except (TypeError, ValueError):
                    raise TrainingError(
                        "invalid_race_date", "赛事日期必须使用 YYYY-MM-DD",
                    )
                if new_date <= date.today():
                    raise TrainingError(
                        "invalid_race_date", "新赛事日期必须晚于今天",
                    )
                goal = copy.deepcopy(goal)
                goal["target_date"] = str(new_date)
            setup = self._setup_context()
            baseline = copy.deepcopy(setup["baseline"])
            existing_constraints = copy.deepcopy(scheme.get("constraints") or {})
            requested_constraints = payload.get("constraints") or {}
            if requested_constraints:
                if "available_days" in requested_constraints:
                    existing_constraints["available_days"] = _coerce_days(
                        requested_constraints["available_days"],
                    )
                if "max_session_minutes" in requested_constraints:
                    max_minutes = int(requested_constraints["max_session_minutes"])
                    if not 20 <= max_minutes <= 360:
                        raise TrainingError(
                            "invalid_session_duration", "单次最长训练需在 20–360 分钟之间",
                        )
                    existing_constraints["max_session_minutes"] = max_minutes
                for field in (
                    "fixed_unavailable", "preferred_terrain", "medical_limitations",
                ):
                    if field in requested_constraints:
                        existing_constraints[field] = str(requested_constraints[field] or "").strip()
                if "cross_training" in requested_constraints:
                    existing_constraints["cross_training"] = bool(
                        requested_constraints["cross_training"],
                    )
            athlete_profile = self._athlete_profile_with_pace_reference(
                setup, target=date.today(),
            )
            pace_profile = self.pace_calibration_profile(target=date.today())
            facts = PlanningFactPackBuilder.build(
                goal=goal,
                baseline=baseline,
                constraints=existing_constraints,
                athlete_profile=athlete_profile,
                recovery_snapshot=setup.get("recovery_snapshot") or {},
            )
            envelope = TrainingLoadEnvelope.calculate(facts)
            source_report = payload.get("source_report")
            if source_report is not None:
                if not isinstance(source_report, dict):
                    raise TrainingError("invalid_report_reference", "报告引用必须是结构化周复盘")
                if (
                    str(source_report.get("plan_id") or "") != str(scheme.get("plan_id") or "")
                    or int(source_report.get("plan_version") or 0) != int(scheme.get("version") or 0)
                ):
                    raise TrainingError(
                        "report_plan_stale",
                        "该周复盘引用的训练方案已变化，请重新生成报告建议",
                        status_code=409,
                    )
                decision = source_report.get("progression_decision") or {}
                action = str(decision.get("action") or "")
                adaptation = source_report.get("adaptation_signal") or {}
                if action not in {"accelerate", "deload", "replan"} and adaptation.get("recommendation") != "scheme_revision":
                    raise TrainingError(
                        "report_adjustment_not_required",
                        "该报告建议继续当前方案，不需要生成方案级调整提案",
                        status_code=409,
                    )
                execution_summary = copy.deepcopy(
                    source_report.get("plan_execution_summary")
                    or source_report.get("execution_summary")
                    or {},
                )
                source_week_id = str(source_report.get("week_id") or "")
                source_report_id = str(
                    source_report.get("report_id") or f"weekly-{source_week_id}"
                )
                effective_from = _coerce_date(
                    payload.get("effective_from")
                    or (
                        date.fromisoformat(str(source_report.get("week_end"))[:10])
                        + timedelta(days=1)
                    ),
                    field="effective_from",
                )
                while effective_from.weekday() != 0:
                    effective_from += timedelta(days=1)
                reason = reason or str(
                    decision.get("rationale")
                    or adaptation.get("rationale")
                    or "周复盘建议调整��一阶段训练",
                )
            else:
                review = self.review_week(target=date.today(), include_ai=False)
                execution_summary = copy.deepcopy(review["execution_summary"])
                source_week_id = None
                source_report_id = None
                effective_from = _coerce_date(
                    payload.get("effective_from") or date.today(),
                    field="effective_from",
                )
            execution_summary["revision_reason"] = reason
            execution_summary["revision_trigger"] = trigger
            try:
                planning = self.scheme_planner.revise(
                    facts,
                    envelope,
                    execution_summary=execution_summary,
                    active_scheme=_active_scheme_structure(scheme),
                    recovery_snapshot=setup.get("recovery_snapshot") or {},
                )
            except TrainingSchemeCandidateUnavailable as exc:
                raise TrainingError(
                    "scheme_candidate_unavailable",
                    "方案重规划失败，当前方案保持不变；请手动重试",
                    status_code=503,
                ) from exc
            first_week = planning["first_four_weeks"][0]
            weekly_target = float(first_week["target_km"])
            entry_phase = self._entry_phase_recommendation(
                planning.get("periodization") or [], planning.get("entry_review") or {},
            )
            proposed_fields = {
                "goal_id": goal["goal_id"],
                "goal_snapshot": copy.deepcopy(goal),
                "baseline_snapshot": baseline,
                "constraints": existing_constraints,
                "coaching_mode": "race_preparation",
                "generation_mode": planning["generation_mode"],
                "inference_source": planning.get("inference_source"),
                "validation_status": planning.get("validation_status"),
                "recommendation_status": planning.get("recommendation_status"),
                "decision_status": planning.get("decision_status"),
                "adjustments": planning.get("adjustments", []),
                "review": copy.deepcopy(planning.get("review") or {"status": "not_provided", "items": [], "safety_hold": None}),
                "entry_review": copy.deepcopy(planning.get("entry_review") or {}),
                "entry_phase_recommendation": entry_phase,
                "fallback_reason": planning.get("fallback_reason"),
                "audit": copy.deepcopy(planning.get("audit") or {}),
                "feasibility": planning["feasibility"],
                "periodization": planning["periodization"],
                "load_progression": planning["load_progression"],
                "first_four_weeks": planning["first_four_weeks"],
                "planning_trace": planning.get("planning_trace", []),
                "assumptions": planning.get("assumptions", []),
                "uncertainties": planning.get("uncertainties", []),
                "risk_flags": planning.get("risk_flags", []),
                "user_explanation": planning.get("user_explanation", ""),
                "current_phase": {
                    "name": str(planning["periodization"][0].get("name") or "基础期"),
                    "purpose": str(
                        planning["periodization"][0].get("purpose")
                        or "重新建立稳定训练节奏"
                    ),
                },
                "weekly_mileage_target": round(weekly_target, 1),
                "data_basis": [
                    f"方案级重规划原因：{reason}",
                    *planning["feasibility"].get("evidence", []),
                    *planning.get("assumptions", []),
                ],
                "weekly_pattern": self._planning_week_pattern(
                    first_week.get("workouts") or [],
                    existing_constraints.get("available_days") or [],
                    pace_profile=pace_profile,
                    recovery_snapshot=setup.get("recovery_snapshot") or {},
                    constraints=existing_constraints,
                    goal_context=goal,
                ),
            }
            proposal = {
                "type": "adjustment_proposal",
                "proposal_id": uuid.uuid4().hex,
                "plan_id": scheme["plan_id"],
                "base_version": scheme["version"],
                "status": "pending",
                "scope": "scheme",
                "target_date": str(effective_from),
                "trigger": trigger,
                "reason": reason,
                "source_report_id": source_report_id,
                "source_week_id": source_week_id,
                "effective_from": str(effective_from),
                "changes": [
                    {
                        "field": "weekly_mileage_target",
                        "before": scheme.get("weekly_mileage_target"),
                        "after": proposed_fields["weekly_mileage_target"],
                    },
                    {
                        "field": "current_phase",
                        "before": (scheme.get("current_phase") or {}).get("name"),
                        "after": proposed_fields["current_phase"]["name"],
                    },
                ],
                "proposed_scheme": proposed_fields,
                "execution_summary": execution_summary,
                "impact": {
                    "weekly_distance_change_km": round(
                        proposed_fields["weekly_mileage_target"]
                        - float(scheme.get("weekly_mileage_target") or 0),
                        1,
                    ),
                    "key_workout_changed": True,
                    "summary": "确认后将生成新的完整方案版本，历史版本继续保留",
                },
                "risk_flags": proposed_fields["risk_flags"],
                "data_basis": proposed_fields["data_basis"],
                "generation_mode": proposed_fields["generation_mode"],
                "inference_source": proposed_fields["inference_source"],
                "validation_status": proposed_fields["validation_status"],
                "recommendation_status": proposed_fields["recommendation_status"],
                "decision_status": proposed_fields["decision_status"],
                "adjustments": proposed_fields["adjustments"],
                "fallback_reason": proposed_fields["fallback_reason"],
                "created_at": _now(),
                "expires_at": str(date.today() + timedelta(days=7)),
            }
            # 同一调整范围只保留最新待决策提案，旧 pending 标记 superseded
            self.repository.supersede_pending_proposals(
                str(scheme["plan_id"]), "scheme",
            )
            self.repository.save_proposal(proposal)
            return proposal

    def propose_from_weekly_report(
        self,
        week_id: str,
        *,
        effective_from: date | None = None,
    ) -> dict[str, Any]:
        """将已固化周复盘的建议转成训练域待确认提案。

        该入口只读取报告并创建 pending proposal；不会确认、启用或修改当前方案。
        """
        with self._lock:
            report = self.repository.get_weekly_report(week_id)
            if not report:
                raise TrainingError("weekly_report_not_found", "周复盘不存在", status_code=404)
            if report.get("type") != "weekly_report":
                raise TrainingError("weekly_report_not_final", "只有已归档周复盘可以生成训练调整提案", status_code=409)
            decision = report.get("progression_decision") or {}
            action = str(decision.get("action") or "")
            adaptation = report.get("adaptation_signal") or {}
            if action not in {"accelerate", "deload", "replan"} and adaptation.get("recommendation") != "scheme_revision":
                raise TrainingError(
                    "report_adjustment_not_required",
                    "该报告建议继续当前方案，不需要生成方案级调整提案",
                    status_code=409,
                )
            payload: dict[str, Any] = {
                "trigger": "review_recommendation",
                "reason": str(
                    decision.get("rationale")
                    or adaptation.get("rationale")
                    or "周复盘建议调整下一阶段训练"
                ),
                "source_report": report,
            }
            if effective_from is not None:
                payload["effective_from"] = str(effective_from)
            return self.propose_scheme_revision(payload)

    @staticmethod
    def _adjust(
        workout: dict[str, Any], feedback: dict[str, Any], week: dict[str, Any],
    ) -> tuple[dict[str, Any], str, dict[str, Any], list[str]]:
        adjusted = {key: copy.deepcopy(value) for key, value in workout.items() if key not in {"actual_activities", "execution_status"}}
        kind = feedback.get("constraint_kind") or feedback["feedback_type"]
        old_distance = float(workout.get("distance_km") or 0)
        old_duration = int(workout.get("duration_minutes") or 0)
        risk_flags: list[str] = []
        if kind == "time_limited":
            available = max(10, int(feedback.get("available_minutes") or 30))
            adjusted["duration_minutes"] = min(old_duration or available, available)
            ratio = adjusted["duration_minutes"] / old_duration if old_duration else 0
            adjusted["distance_km"] = round(old_distance * ratio, 1)
            adjusted["title"] = f"缩短版 · {workout['title']}"
            adjusted["reason"] = f"可用时间只有 {available} 分钟，保留主要训练目的"
            reason = adjusted["reason"]
        elif kind == "fatigue":
            adjusted.update({
                "title": "恢复跑或休息", "type": "recovery",
                "duration_minutes": min(30, max(20, round(old_duration * 0.6))),
                "distance_km": round(old_distance * 0.55, 1),
                "intensity": "RPE 1–3，全程轻松", "is_key": False,
                "reason": "主观疲劳升高，降低本次强度与总量",
            })
            reason = adjusted["reason"]
        elif kind == "pain":
            pain = feedback.get("pain") or {}
            adjusted.update({
                "title": "暂停跑步并观察", "type": "rest", "duration_minutes": 0,
                "distance_km": 0, "intensity": "不进行跑步训练", "is_key": False,
                "reason": f"收到{pain.get('location')}疼痛 {pain.get('severity')}/10 反馈，安全优先",
            })
            reason = adjusted["reason"]
            risk_flags = ["pain", "no_intensity_increase", "consider_professional_assessment"]
        elif kind == "schedule_conflict":
            adjusted.update({
                "title": "休息（本次日程冲突）", "type": "rest", "duration_minutes": 0,
                "distance_km": 0, "intensity": "休息", "is_key": False,
                "reason": "日程冲突，本次训练取消；关键课不会自动挪到未知时段",
            })
            reason = adjusted["reason"]
        else:
            adjusted.update({
                "title": "保守调整 · " + workout["title"],
                "duration_minutes": round(old_duration * 0.8),
                "distance_km": round(old_distance * 0.8, 1),
                "reason": "根据当前约束保守减少训练量",
            })
            reason = adjusted["reason"]
        old_prescription = workout.get("training_prescription")
        old_prescription = (
            old_prescription if isinstance(old_prescription, dict) else {}
        )
        if adjusted.get("type") == "rest":
            adjusted.pop("training_prescription", None)
        else:
            fatigue_downgrade = kind == "fatigue"
            adjusted["training_prescription"] = {
                "primary_completion": str(
                    old_prescription.get("primary_completion") or "time"
                ),
                "intensity_zone": (
                    1 if fatigue_downgrade
                    else old_prescription.get("intensity_zone")
                ),
                "stimuli": (
                    ["recovery"] if fatigue_downgrade
                    else copy.deepcopy(old_prescription.get("stimuli") or [])
                ),
                "targets": {
                    "feel": (
                        {} if fatigue_downgrade else copy.deepcopy(
                            (old_prescription.get("targets") or {}).get("feel") or {}
                        )
                    ),
                },
                "adjustment_rules": copy.deepcopy(
                    old_prescription.get("adjustment_rules") or []
                ),
                "method_basis": copy.deepcopy(
                    old_prescription.get("method_basis") or {}
                ),
            }
            adjusted = ensure_training_prescription(
                adjusted, synthesize_steps=True,
            )
        new_distance = float(adjusted.get("distance_km") or 0)
        impact = {
            "weekly_distance_change_km": round(new_distance - old_distance, 1),
            "key_workout_changed": bool(workout.get("is_key")),
            "summary": "本周总量下降，后续课次保持不变；不补偿性加量",
        }
        return adjusted, reason, impact, risk_flags

    def approve(self, proposal_id: str, *, base_version: int, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise TrainingError("idempotency_key_required", "确认调整必须提供 idempotency_key")
        with self._lock:
            proposal = self.repository.get_proposal(proposal_id)
            if not proposal:
                raise TrainingError("proposal_not_found", "调整提案不存在", status_code=404)
            if proposal.get("status") == "approved":
                return {"proposal": proposal, "scheme": self.repository.get_active(), "idempotent": True}
            if proposal.get("status") != "pending":
                raise TrainingError("proposal_not_pending", "该提案已不能确认", status_code=409)
            if date.today() > date.fromisoformat(str(proposal["expires_at"])[:10]):
                proposal["status"] = "expired"
                proposal["expired_at"] = _now()
                self.repository.save_proposal(proposal)
                raise TrainingError("proposal_expired", "提案已过期，请重新提交反馈", status_code=409)
            scheme = self.repository.get_active()
            expected = int(proposal["base_version"])
            if (
                scheme
                and int(scheme.get("version", 0)) == expected + 1
                and (scheme.get("last_adjustment") or {}).get("proposal_id") == proposal_id
            ):
                self._materialize_week(
                    scheme, date.fromisoformat(proposal["target_date"]), persist=True,
                )
                proposal["status"] = "approved"
                proposal["approved_at"] = (
                    scheme.get("last_adjustment") or {}
                ).get("approved_at") or _now()
                proposal["applied_version"] = scheme["version"]
                proposal["idempotency_key"] = idempotency_key
                self.repository.save_proposal(proposal)
                return {"proposal": proposal, "scheme": scheme, "idempotent": True}
            if not scheme or int(base_version) != expected or int(scheme["version"]) != expected:
                raise TrainingError("version_conflict", "方案已更新，请刷新后重新生成提案", status_code=409)
            if proposal.get("scope") == "scheme":
                proposed_fields = proposal.get("proposed_scheme")
                if not isinstance(proposed_fields, dict):
                    raise TrainingError("invalid_scheme_revision", "方案级提案缺少候选方案", status_code=409)
                updated = copy.deepcopy(scheme)
                updated.update(copy.deepcopy(proposed_fields))
                updated["version"] = expected + 1
                effective_from = _coerce_date(
                    proposal.get("effective_from") or date.today(),
                    field="effective_from",
                )
                is_scheduled = effective_from > date.today()
                updated["status"] = "scheduled" if is_scheduled else "active"
                activated_at = _now()
                updated["updated_at"] = activated_at
                updated["activated_at"] = activated_at
                updated["effective_from"] = str(effective_from)
                updated["effective_to"] = None
                updated["last_adjustment"] = {
                    "proposal_id": proposal_id,
                    "scope": "scheme",
                    "reason": proposal.get("reason"),
                    "approved_at": _now(),
                }
                if is_scheduled:
                    self.repository.save_scheduled(updated)
                else:
                    self.repository.save_scheme(updated)
                    self._materialize_week(updated, date.today(), persist=True)
                # 重规划确认后同步目标记录（如赛事延期的新日期），
                # 避免下次重规划读到旧 target_date。
                proposed_goal = proposed_fields.get("goal_snapshot") or {}
                if proposed_goal and str(updated.get("goal_id") or ""):
                    try:
                        self.goals.update(
                            str(updated["goal_id"]),
                            {
                                "name": proposed_goal.get("name"),
                                "distance": proposed_goal.get("distance"),
                                "goal_intent": proposed_goal.get("goal_intent"),
                                "target_time": proposed_goal.get("target_time"),
                                "target_date": proposed_goal.get("target_date"),
                            },
                        )
                    except Exception:
                        # 目标记录同步失败不阻塞方案确认；下次读取以方案 goal_snapshot 为准。
                        pass
                proposal["status"] = "approved"
                proposal["approved_at"] = _now()
                proposal["applied_version"] = updated["version"]
                proposal["idempotency_key"] = idempotency_key
                proposal["effective_from"] = str(effective_from)
                self.repository.save_proposal(proposal)
                return {
                    "proposal": proposal,
                    "scheme": updated,
                    "idempotent": False,
                    "scheduled": is_scheduled,
                }
            updated = copy.deepcopy(scheme)
            updated["version"] = expected + 1
            activated_at = _now()
            updated["updated_at"] = activated_at
            updated["activated_at"] = activated_at
            updated["effective_from"] = str(date.today())
            updated["effective_to"] = None
            overrides = copy.deepcopy(updated.get("week_overrides") or {})
            for change in proposal.get("changes", []):
                overrides[change["date"]] = {
                    key: value for key, value in change["after"].items()
                    if key not in {"date", "weekday_name", "workout_id", "source_version"}
                }
            updated["week_overrides"] = overrides
            updated["last_adjustment"] = {
                "proposal_id": proposal_id, "reason": proposal.get("reason"),
                "approved_at": _now(),
            }
            self.repository.save_scheme(updated)
            self._materialize_week(
                updated, date.fromisoformat(proposal["target_date"]), persist=True,
            )
            proposal["status"] = "approved"
            proposal["approved_at"] = _now()
            proposal["applied_version"] = updated["version"]
            proposal["idempotency_key"] = idempotency_key
            self.repository.save_proposal(proposal)
            return {"proposal": proposal, "scheme": updated, "idempotent": False}

    def reject(self, proposal_id: str, *, reason: str = "") -> dict[str, Any]:
        with self._lock:
            proposal = self.repository.get_proposal(proposal_id)
            if not proposal:
                raise TrainingError("proposal_not_found", "调整提案不存在", status_code=404)
            if proposal.get("status") == "rejected":
                return proposal
            if proposal.get("status") != "pending":
                raise TrainingError("proposal_not_pending", "该提案已不能拒绝", status_code=409)
            proposal["status"] = "rejected"
            proposal["rejected_at"] = _now()
            proposal["rejection_reason"] = reason.strip()
            self.repository.save_proposal(proposal)
            return proposal
