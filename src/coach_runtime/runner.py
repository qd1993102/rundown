"""受限 Coach Skill runner 与 OpenAI-compatible JSON 客户端。"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import get_ai_config
from .context import CoachRunContext
from .registry import SkillRegistry, SkillSpec
from .schemas import SkillSchemaError, validate_output

logger = logging.getLogger(__name__)


def _debug_ai_prompts_enabled() -> bool:
    """Keep full prompt logging an explicit local-only opt-in."""
    return os.getenv("NEURUN_AI_DEBUG_PROMPTS", "").lower() in {"1", "true", "yes"}


_PROVIDER_ERROR_LABELS = {
    400: "请求格式被拒绝",
    401: "鉴权失败",
    402: "余额不足",
    422: "请求参数无效",
    429: "请求过于频繁",
    500: "服务内部错误",
    503: "服务繁忙",
}


def _byte_and_token_estimate(value: str) -> tuple[int, int]:
    """Return a stable, content-free size summary for operational logs."""
    byte_count = len(value.encode("utf-8"))
    return byte_count, math.ceil(byte_count / 4)


def _payload_section_sizes(payload: dict[str, Any]) -> str:
    """Log only top-level input section names and lengths, never their content."""
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    sections: list[str] = []
    for name, value in facts.items():
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            encoded = "null"
        byte_count, estimate = _byte_and_token_estimate(encoded)
        sections.append(f"{str(name)[:64]}:{byte_count}B/{estimate}t")
    return ",".join(sections) or "none"

def _training_scheme_output_example() -> str:
    easy_step = {
        "step_id": "main", "order": 1, "kind": "work", "role": "main",
        "dose": {"metric": "duration", "value": 60, "unit": "minute"},
        "intensity_intent": {
            "zone": 2, "preferred_metric": "pace",
            "fallback_feel": "conversational",
        },
        "transition": {"type": "dose_complete"},
    }
    quality_steps = [
        {
            "step_id": "warmup", "order": 1, "kind": "work", "role": "warmup",
            "dose": {"metric": "duration", "value": 10, "unit": "minute"},
            "intensity_intent": {
                "zone": 2, "preferred_metric": "pace",
                "fallback_feel": "conversational",
            },
            "transition": {"type": "dose_complete"},
        },
        {
            "step_id": "main-repeat", "order": 2, "kind": "repeat",
            "repeat_count": 5,
            "children": [
                {
                    "step_id": "work", "order": 1, "kind": "work", "role": "work",
                    "dose": {"metric": "duration", "value": 3, "unit": "minute"},
                    "intensity_intent": {
                        "zone": 5, "preferred_metric": "pace",
                        "calibration_context": {
                            "segment_kind": "work", "duration_seconds": 180,
                        },
                        "fallback_feel": "hard but controlled",
                    },
                    "transition": {"type": "dose_complete"},
                },
                {
                    "step_id": "recovery", "order": 2, "kind": "recovery",
                    "role": "recovery",
                    "dose": {"metric": "duration", "value": 2, "unit": "minute"},
                    "intensity_intent": {
                        "zone": 1, "preferred_metric": "feel",
                        "fallback_feel": "easy until breathing recovers",
                    },
                    "transition": {
                        "type": "dose_and_readiness",
                        "readiness": "can speak a full short sentence",
                    },
                },
            ],
        },
        {
            "step_id": "cooldown", "order": 3, "kind": "work", "role": "cooldown",
            "dose": {"metric": "duration", "value": 10, "unit": "minute"},
            "intensity_intent": {
                "zone": 1, "preferred_metric": "feel",
                "fallback_feel": "easy jog or walk",
            },
            "transition": {"type": "dose_complete"},
        },
    ]

    def workout(week: int) -> dict[str, Any]:
        is_quality = week == 1
        return {
            "weekday": 0, "title": "变速跑" if is_quality else "轻松跑",
            "type": "quality" if is_quality else "easy", "purpose": "建立有氧基础",
            "duration_minutes": 45 if is_quality else 60,
            "distance_km": 8.0 if is_quality else 10.0,
            "intensity": "controlled", "alternatives": [],
            "adjustment_triggers": [], "is_key": is_quality,
            "stimuli": ["pace_change" if is_quality else "aerobic_endurance"],
            "intensity_zone": 5 if is_quality else 2,
            "primary_completion": "time",
            "intensity_intent": {
                "zone": 5 if is_quality else 2,
                "preferred_metric": "pace",
                "fallback_feel": "controlled effort with room to finish",
            },
        }

    weeks = [
        {
            "week": week, "target_km": 40 if week < 4 else 36, "target_load": 180,
            "focus": "建立有氧基础", "recovery_week": week == 4,
            "workouts": [workout(week)],
        }
        for week in range(1, 3)
    ]
    return json.dumps({
        "feasibility": {
            "level": "feasible", "confidence": 0.5, "summary": "当前目标可以在现有训练基础上逐步完成",
            "evidence": [], "gaps": [], "routes": [],
        },
        "periodization": [{"name": "基础期", "weeks": 4, "purpose": "建立稳定有氧基础"}],
        "load_progression": [
            {"week": item["week"], "target_km": item["target_km"],
             "target_load": item["target_load"],
             "recovery_week": item["recovery_week"]}
            for item in weeks
        ],
        "first_two_weeks": weeks,
        "assumptions": [], "uncertainties": [], "risk_flags": [],
        "user_explanation": "先保持连续训练，再根据完整周的执行和恢复决定是否推进。",
        "review": {"status": "ok", "items": [], "safety_hold": None},
        "entry_review": {
            "recommended_entry_phase": "phase_1", "decision": "hold",
            "confidence": 0.5, "evidence": [], "adjustments": [],
            "applied": False, "applied_adjustments": [],
            "handoff": {"status": "review_pending", "requires_user_confirmation": True},
        },
    }, ensure_ascii=False, separators=(",", ":"))


_OUTPUT_JSON_EXAMPLES = {
    "CoachInsight": (
        '{"plan_execution":{"today_planned":"summary","today_actual":"summary",'
        '"today_match":"status","comparison_status":"applicable",'
        '"week_completion":"summary","on_track":true,"deviation_note":""},'
        '"conclusion":"summary","observations":[],"recommendations":[],'
        '"warnings":[],"share_card":{"headline":"","sessions":[],"takeaway":"",'
        '"conclusion":""},"plan_adjusted":false,"session_summary":"",'
        '"session_characteristics":[],"training_effect":"","recovery_response":"",'
        '"capability_signals":[],"next_day_constraints":[],"evidence":[],"gaps":[],"confidence":0.5}'
    ),
    "SkillFinding": (
        '{"skill":"skill-name","status":"ok","conclusion":"summary",'
        '"evidence":[],"uncertainties":[],"risk_flags":[],"recommendations":[]}'
    ),
    "TrainingPlanReview": (
        '{"skill":"review-training-plan","status":"ok","conclusion":"summary",'
        '"evidence":[],"uncertainties":[],"risk_flags":[],"recommendations":[],'
        '"review_stage":"execution_review","decision":"hold","scope":"next_week",'
        '"recommended_entry_phase":null,"confidence":0.5,"adjustments":[],'
        '"safety_hold":null,"user_explanation":"summary",'
        '"handoff":{"status":"review_pending","requires_user_confirmation":true}}'
    ),
    "TrainingFramework": (
        '{"feasibility":{"level":"challenging","confidence":0.7,"summary":"需要逐步补足专项耐力",'
        '"evidence":[],"gaps":[]},"ability_summary":{},"goal_demand_summary":{},"ability_gap":[],'
        '"recommended_entry_phase":"基础期","entry_rationale":"先稳定吸收训练",'
        '"periodization":[{"name":"基础期","weeks":4,"purpose":"建立稳定有氧基础"}],'
        '"load_progression":[{"week":1,"target_km":40,"target_load":180,"recovery_week":false}],'
        '"weekly_principles":{},"method_basis":[],'
        '"assumptions":[],"uncertainties":[],"risk_flags":[],"user_explanation":""}'
    ),
    "NearTermSchedule": (
        '{"first_two_weeks":[],"review":{"status":"ok","items":[],"safety_hold":null},'
        '"entry_review":{"recommended_entry_phase":"基础期","decision":"hold","confidence":0.5,'
        '"evidence":[],"adjustments":[],"applied":false,"applied_adjustments":[],'
        '"handoff":{"status":"review_pending","requires_user_confirmation":true}},'
        '"assumptions":[],"uncertainties":[],"risk_flags":[],"data_gaps":[],"user_explanation":""}'
    ),
    "TrainingSchemeCandidate": _training_scheme_output_example(),
    "SchemeAudit": (
        '{"verdict":"pass","summary":"课表可执行",'
        '"issues":[{"code":"weekday_session_too_long","severity":"warning",'
        '"message":"周三 20km 约 100 分钟","recommendation":"留意时间"}]}'
    ),
}

_OUTPUT_JSON_RULES = {
    "TrainingSchemeCandidate": (
        " first_two_weeks must contain exactly two week objects representing the current "
        "and next week. "
        "weekday must be an integer using Monday=0 through Sunday=6. "
        "Every non-rest workout must include a compact course skeleton with type, purpose, "
        "duration or distance, intensity and stimuli. Do not emit full training_prescription "
        "steps; the runtime deterministically materializes Workout Steps v2. If a full v2 "
        "prescription is included for compatibility, repeated quality work must use a repeat "
        "step with repeat_count plus explicit work and recovery children. Keep all strings "
        "short: no prose explanations inside workouts, at most one alternative and one "
        "adjustment trigger per workout, and only zone/preferred_metric/fallback_feel in "
        "intensity_intent. All user-facing natural-language values must be Simplified Chinese; "
        "keep machine enums, field names, Z1-Z5 and units unchanged. Include target_load when "
        "facts support it and return entry_review for the recommended starting phase."
    ),
    "TrainingPlanReview": (
        " decision must be exactly one of hold, advance, adjust, deload or replan. "
        "If data coverage is incomplete, sync state is unknown or facts are insufficient, "
        "use decision=hold. handoff.requires_user_confirmation must always be true. "
        "All user-facing natural-language values must be Simplified Chinese."
    ),
    "TrainingFramework": (
        " Do not output daily workouts or Workout Steps. Keep the framework evidence-based; "
        "the previous complete natural week is reference only, not a copy target. "
        "All user-facing natural-language values must be Simplified Chinese."
    ),
    "NearTermSchedule": (
        " first_two_weeks must contain exactly two week objects. Each non-rest workout must "
        "include a session structure and pace/feel guidance when facts support it. "
        "Non-rest workouts may include an optional pace_intent string "
        "(easy/long_run/marathon_pace/threshold/interval/repetition/tempo/recovery/race_pace) "
        "as a course-type anchor only; never invent numeric paces. "
        "All user-facing natural-language values must be Simplified Chinese."
    ),
    "SchemeAudit": (
        " verdict must be pass or fail. fail only when at least one critical issue exists. "
        "Every issue needs code, severity (critical|warning), message and recommendation. "
        "Only evaluate executability; do not rewrite the schedule or invent paces. "
        "All user-facing natural-language values must be Simplified Chinese."
    ),
}


class SkillRunError(RuntimeError):
    """Skill 无法安全完成。"""


def _provider_error(status: int, body: Any) -> str:
    """只保留可操作的上游错误类别，不传播响应正文或用户事实。"""
    error_type = ""
    try:
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            raw_type = str(error.get("type") or error.get("code") or "")
            if raw_type and all(char.isalnum() or char in "._-" for char in raw_type):
                error_type = raw_type[:64]
    except Exception:
        pass
    label = _PROVIDER_ERROR_LABELS.get(status, f"请求失败（HTTP {status}）")
    suffix = f"（{error_type}）" if error_type else ""
    return f"AI 服务{label}{suffix}"


def _try_decode_json(chunks: list[bytes]) -> Any | None:
    """Decode a complete provider envelope without exposing its contents."""
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _has_complete_choice_content(body: Any) -> bool:
    """Only finish early after a non-empty, complete JSON object is present.

    推理类模型（如 deepseek-v4-flash）流式输出期间会先发送 content 为空串、
    只含 reasoning_content 的中间态 envelope；只有 content 非空且本身是合法
    JSON 对象时才视为最终响应，避免误判提前 break 后 json.loads("") 失败。
    """
    try:
        content = body["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            return False
        return isinstance(json.loads(content), dict)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False


class SkillModel(Protocol):
    def generate(
        self, spec: SkillSpec, prompt: str, payload: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass
class OpenAICompatibleSkillModel:
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    timeout_seconds: float = 300.0

    def generate(
        self, spec: SkillSpec, prompt: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        import httpx

        ai_config = get_ai_config(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
        )
        if not ai_config.api_key:
            raise SkillRunError("未配置 NEURUN_AI_API_KEY")
        example = _OUTPUT_JSON_EXAMPLES.get(spec.output_model, '{"result":"value"}')
        contract = (
            "Provider response contract: return exactly one valid json object, "
            f"compatible with the {spec.output_model} output model. "
            "Do not use Markdown fences or add text outside the object. "
            f"Use these exact json keys and value types: {example}"
        )
        output_rules = _OUTPUT_JSON_RULES.get(spec.output_model, '')
        structured_prompt = (
            f"{prompt}\n\n"
            f"{contract}{output_rules}"
        )
        long_skills = {
            "draft-training-scheme", "revise-training-scheme",
            "build-training-framework", "build-near-term-schedule",
        }
        total_budget = (
            min(self.timeout_seconds, 25.0)
            if spec.name in {"review-training-week", "review-training-plan"}
            else self.timeout_seconds
        )
        read_timeout = min(
            total_budget,
            # 官方 API 对推理模型（如 deepseek-v4-pro）先返回 headers、随后静默推理
            # 数十秒再一次性发送 body，单次读取上限需覆盖整个静默期；
            # 中转网关（headers 晚到但随后立即流式）不受影响。
            120.0 if spec.name == "build-training-framework" else
            45.0 if spec.name in long_skills else 20.0,
        )
        request_body = {
            "model": ai_config.model,
            "messages": [
                {"role": "system", "content": structured_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            # The framework is a strategic object with two non-empty arrays and
            # several evidence/risk fields; 1300 frequently truncated valid JSON.
            # 官方 API 的草稿长输出（含近期课表细节）可达 2500+ tokens，
            # 框架/课表两阶段放宽上限，避免 finish_reason=length 截断 JSON。
            # 方案重规划输出完整候选（阶段 + 首四周课表 + 逐段处方），
            # 实测 7.4KB（≈2000+ tokens）会被 1800 截断导致 JSON 解析失败（503），同样放宽。
            "max_tokens": (
                4000 if spec.name in {
                    "build-training-framework", "build-near-term-schedule",
                    "revise-training-scheme",
                }
                else 1800
            ),
        }
        prompt_bytes = len(structured_prompt.encode("utf-8"))
        payload_bytes = len(request_body["messages"][1]["content"].encode("utf-8"))
        prompt_parts = {
            "skill_instruction": _byte_and_token_estimate(prompt),
            "response_contract": _byte_and_token_estimate(contract),
            "output_rules": _byte_and_token_estimate(output_rules),
            "structured_total": _byte_and_token_estimate(structured_prompt),
        }
        request_meta = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        chain_id = str(
            request_meta.get("generation_id") or request_meta.get("framework_id") or "none"
        )[:80]
        call_index = str(request_meta.get("ai_call_index") or "unknown")[:16]
        logger.info(
            "AI draft call: chain_id=%s skill=%s output_model=%s prompt_parts="
            "skill_instruction:%sB/%st,response_contract:%sB/%st,output_rules:%sB/%st,structured_total:%sB/%st "
            "input_sections=%s payload=%sB/%st max_tokens=%s deadline=%.1fs",
            chain_id, spec.name, spec.output_model,
            *prompt_parts["skill_instruction"], *prompt_parts["response_contract"],
            *prompt_parts["output_rules"], *prompt_parts["structured_total"],
            _payload_section_sizes(payload), payload_bytes, math.ceil(payload_bytes / 4),
            request_body["max_tokens"], total_budget,
        )
        if _debug_ai_prompts_enabled() and spec.name in {
            "build-training-framework", "build-near-term-schedule",
        }:
            logger.warning(
                "AI draft debug prompt: chain_id=%s call=%s skill=%s system_prompt_begin\n%s\nsystem_prompt_end\nuser_payload_begin\n%s\nuser_payload_end",
                chain_id, call_index, spec.name, structured_prompt,
                request_body["messages"][1]["content"],
            )
        for attempt in (1, 2):
            started = time.monotonic()
            response_status = 0
            response_body: Any = None
            try:
                timeout = httpx.Timeout(
                    connect=min(10.0, read_timeout), read=read_timeout,
                    write=min(20.0, total_budget), pool=min(10.0, read_timeout),
                )
                with httpx.Client(timeout=timeout) as client:
                    with client.stream(
                        "POST",
                        ai_config.chat_completions_url,
                        headers={
                            "Authorization": f"Bearer {ai_config.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    ) as response:
                        response_status = response.status_code
                        headers_elapsed = time.monotonic() - started
                        logger.info(
                            "AI provider timing: skill=%s phase=response_headers status=%s elapsed=%.2fs",
                            spec.name, response_status, headers_elapsed,
                        )
                        chunks: list[bytes] = []
                        response_size = 0
                        first_byte_logged = False
                        for chunk in response.iter_bytes():
                            response_size += len(chunk)
                            if response_size > 4 * 1024 * 1024:
                                raise SkillRunError("AI 服务响应过大")
                            chunks.append(chunk)
                            elapsed = time.monotonic() - started
                            if not first_byte_logged:
                                first_byte_logged = True
                                logger.info(
                                    "AI provider timing: skill=%s phase=first_byte status=%s bytes=%s elapsed=%.2fs",
                                    spec.name, response_status, response_size, elapsed,
                                )
                            # Some gateways keep a successful connection open after the
                            # complete JSON envelope has arrived.  The envelope is the
                            # contract; waiting for TCP close only adds tail latency.
                            if response_status == 200 and response_body is None:
                                candidate = _try_decode_json(chunks)
                                if candidate is not None and _has_complete_choice_content(candidate):
                                    response_body = candidate
                                    logger.info(
                                        "AI provider timing: skill=%s phase=complete_json status=%s bytes=%s elapsed=%.2fs",
                                        spec.name, response_status, response_size, elapsed,
                                    )
                                    break
                            if elapsed > total_budget:
                                logger.warning(
                                    "AI provider response timeout: skill=%s status=%s bytes=%s prefix_hex=%s elapsed=%.2fs",
                                    spec.name, response_status, response_size,
                                    b"".join(chunks)[:16].hex(),
                                    elapsed,
                                )
                                raise SkillRunError("AI 服务响应超时")
                        elapsed = time.monotonic() - started
                        if response_body is None and elapsed > total_budget:
                            logger.warning(
                                "AI provider response timeout: skill=%s status=%s bytes=%s prefix_hex=%s elapsed=%.2fs",
                                spec.name, response_status, response_size,
                                b"".join(chunks)[:16].hex(),
                                elapsed,
                            )
                            raise SkillRunError("AI 服务响应超时")
                        if response_body is None:
                            response_body = _try_decode_json(chunks)
                        if response_body is None:
                            raise SkillRunError("AI 服务响应格式无效")
                    stream_closed_elapsed = time.monotonic() - started
                    logger.info(
                        "AI provider timing: skill=%s phase=stream_closed status=%s bytes=%s elapsed=%.2fs",
                        spec.name, response_status, response_size, stream_closed_elapsed,
                    )
                    logger.info(
                        "AI provider response complete: skill=%s status=%s bytes=%s elapsed=%.2fs",
                        spec.name, response_status, response_size,
                        stream_closed_elapsed,
                    )
                if response_status != 200:
                    raise SkillRunError(_provider_error(response_status, response_body))
                try:
                    content = response_body["choices"][0]["message"].get("content") or ""
                except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                    raise SkillRunError("AI 服务响应格式无效") from exc
                try:
                    decode_started = time.monotonic()
                    result = json.loads(content)
                    logger.info(
                        "AI provider timing: skill=%s phase=json_decoded content_bytes=%s elapsed=%.2fs",
                        spec.name, len(content.encode("utf-8")), time.monotonic() - decode_started,
                    )
                    return result
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "AI output rejected: skill=%s reason=json_decode content_bytes=%s attempt=%s/2",
                        spec.name, len(content.encode("utf-8")), attempt,
                    )
                    if attempt == 1:
                        # 推理类 provider 偶发返回 content 为空/非法 JSON 的完整 envelope
                        # （HTTP 200 但 content=""，仅含 reasoning_content），重试一次通常
                        # 可得到完整结果；超时/400 等请求级错误不在此重试。
                        logger.info(
                            "AI provider empty/invalid content, retrying: skill=%s attempt=1/2",
                            spec.name,
                        )
                        continue
                    raise SkillRunError("Skill 模型未返回有效 JSON") from exc
            except httpx.TimeoutException as exc:
                raise SkillRunError("AI 服务响应超时") from exc
            except httpx.RequestError as exc:
                logger.warning(
                    "AI provider request error: skill=%s error_type=%s",
                    spec.name, type(exc).__name__,
                )
                raise SkillRunError("AI 服务请求失败") from exc

class CoachSkillRunner:
    """每次只加载并执行一个显式入口 Skill。"""

    def __init__(
        self,
        registry: SkillRegistry,
        model: SkillModel,
        *,
        core_prompt: str = "你是 neurun 跑步教练。事实优先，保留不确定性，不绕过用户确认。",
    ):
        self.registry = registry
        self.model = model
        self.core_prompt = core_prompt

    def run(
        self,
        skill_name: str,
        context: CoachRunContext,
    ) -> tuple[dict[str, Any], CoachRunContext]:
        primary = self.registry.get(skill_name)
        mode = str(context.policy.get("coaching_mode") or "continuous_running")
        if mode not in primary.supported_modes:
            raise SkillRunError(f"{skill_name} 不支持 {mode} 模式")
        missing = [name for name in primary.required_facts if name not in context.facts]
        if missing:
            raise SkillRunError(f"{skill_name} 缺少事实: {', '.join(missing)}")

        raw = self.model.generate(
            primary, self._prompt(primary), context.to_model_payload(primary.name),
        )
        try:
            result = validate_output(primary.output_model, raw)
        except SkillSchemaError as exc:
            # Log only the deterministic schema path/reason; never log provider
            # content, prompts, or user facts.
            logger.warning(
                "AI output rejected: skill=%s output_model=%s reason=%s",
                skill_name, primary.output_model, str(exc)[:240],
            )
            raise SkillRunError(f"{skill_name} 输出无效: {exc}") from exc
        current = context.with_trace({
            "skill": skill_name, "version": primary.version, "status": "completed",
        })
        return result, current

    def _prompt(self, spec: SkillSpec) -> str:
        prompt = (
            f"{self.core_prompt}\n\n"
            f"你正在执行 Skill `{spec.name}`，输出模型必须是 {spec.output_model}。\n\n"
            f"{self.registry.load_prompt(spec.name)}"
        )
        if spec.name == "draft-training-scheme":
            prompt += (
                "\n\n复用 `review-training-plan` 的 entry_review 约束（"
                "不得发起第二次模型调用或输出独立 TrainingPlanReview）："
                "按能力→当下状态→目标小幅拔高→恢复安全→ACWR 次级校验判断起始阶段；"
                "advance/adjust/deload 直接应用并记录 applied/applied_adjustments；"
                "replan 或 safety_hold 仅保留待确认 handoff。"
            )
        return prompt
