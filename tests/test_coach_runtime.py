"""Coach Skill registry、单 Skill runner 与输出合同测试。"""

from __future__ import annotations

import logging

import pytest

from src.coach_runtime import (
    CoachRunContext,
    CoachSkillRunner,
    OpenAICompatibleSkillModel,
    SkillRegistry,
)
from src.coach_runtime.runner import SkillRunError


class FakeModel:
    def __init__(self):
        self.calls = []

    def generate(self, spec, prompt, payload):
        self.calls.append((spec.name, prompt, payload))
        prescription = {
            "structure_version": 2,
            "primary_completion": "time",
            "intensity_zone": 2,
            "stimuli": ["aerobic_endurance"],
            "steps": [{
                "step_id": "main", "order": 1, "kind": "work", "role": "main",
                "dose": {"metric": "duration", "value": 40, "unit": "minute"},
                "intensity_intent": {
                    "zone": 2, "preferred_metric": "pace",
                    "fallback_feel": "能完整对话",
                },
                "transition": {"type": "dose_complete"},
            }],
            "targets": {
                "pace": {"status": "unavailable", "range": None, "basis": "数据不足"},
                "heart_rate": {"status": "unavailable", "range": None, "basis": "数据不足"},
                "feel": {"label": "能完整对话", "rpe": "2–3"},
            },
            "completion_criteria": {
                "minimum_completed_repetitions": None,
                "quality_rule": "始终可控",
                "classification": ["completed", "reduced", "substituted", "stopped"],
            },
            "adjustment_rules": [],
            "method_basis": {"principle": "有氧连续性", "stage_relation": "当前周"},
        }
        workout = {
            "weekday": 0, "title": "轻松跑", "type": "easy",
            "purpose": "建立连续性", "duration_minutes": 40,
            "distance_km": 10, "intensity": "能完整对话",
            "alternatives": [], "adjustment_triggers": [], "is_key": False,
            "training_prescription": prescription,
        }
        return {
            "feasibility": {
                "level": "challenging", "confidence": 0.72,
                "summary": "需要逐步提高训练容量",
                "evidence": ["当前周跑量 40 km"], "gaps": ["长距离不足"],
                "routes": [{"id": "balanced", "label": "平衡路线"}],
            },
            "periodization": [{"name": "基础期", "weeks": 4}],
            "load_progression": [{"week": 1, "target_km": 40}],
            "first_four_weeks": [
                {"week": week, "target_km": 40, "workouts": [workout]}
                for week in range(1, 5)
            ],
            "assumptions": [], "uncertainties": [], "risk_flags": [],
            "user_explanation": "先建立稳定训练连续性。",
            "review": {
                "status": "attention",
                "items": [{"code": "LOAD_PROGRESS", "severity": "low", "reason": "推进需观察", "suggestion": "按体感调整"}],
                "safety_hold": None,
            },
            "entry_review": {
                "recommended_entry_phase": "base",
                "decision": "hold",
                "confidence": 0.8,
                "evidence": ["当前连续性仍需观察"],
                "adjustments": [],
            },
        }


def test_default_registry_loads_all_internal_skill_packages():
    registry = SkillRegistry.default()

    assert {spec.name for spec in registry.list()} == {
        "review-daily-training", "summarize-training-day", "draft-training-scheme",
        "build-training-framework", "build-near-term-schedule", "audit-training-scheme",
        "review-training-week", "review-training-plan", "propose-training-adjustment",
        "revise-training-scheme", "prepare-race-strategy",
    }
    assert registry.get("review-daily-training").output_model == "CoachInsight"
    assert registry.get("summarize-training-day").output_model == "TrainingDaySemanticSummary"
    assert registry.get("review-training-week").required_facts == (
        "natural_week_actual",
    )
    assert registry.get("draft-training-scheme").version == "1.5.0"
    assert registry.get("review-training-plan").output_model == "TrainingPlanReview"
    assert "不得把基础期重新排到后面" in registry.load_prompt("review-training-plan")
    assert registry.get("revise-training-scheme").version == "1.5.0"


def test_runner_loads_only_one_explicit_skill():
    registry = SkillRegistry.default()
    model = FakeModel()
    runner = CoachSkillRunner(registry, model)
    context = CoachRunContext(
        request={"intent": "draft_training_scheme"},
        facts={"planning_fact_pack": {}, "training_load_envelope": {}},
        policy={"coaching_mode": "race_preparation"},
    )

    result, final_context = runner.run("draft-training-scheme", context)

    assert [call[0] for call in model.calls] == ["draft-training-scheme"]
    assert "运行时会延展首四周、解析个人配速" in model.calls[0][1]
    assert "当前周和下一周的课程骨架" in model.calls[0][1]
    assert "当前可持续能力与近期表现" in model.calls[0][1]
    assert "ACWR 次级风险校验" in model.calls[0][1]
    assert "previous_week_km" in model.calls[0][1]
    assert "不能原样复制跑量或课表" in model.calls[0][1]
    assert "缩短适应周期" in model.calls[0][1]
    assert "不要输出 ISO `date`" in model.calls[0][1]
    assert "review-training-plan" in model.calls[0][1]
    assert "不得发起第二次模型调用" in model.calls[0][1]
    assert "user_supplement" in model.calls[0][1]
    assert "未验证" in model.calls[0][1]
    assert "structure_classification" in model.calls[0][1]
    assert "输出首四周每日课程骨架" not in model.calls[0][1]
    assert "每周所有 `workouts.distance_km` 之和必须等于" not in model.calls[0][1]
    assert len(model.calls[0][1]) < 3400
    assert result["feasibility"]["level"] == "challenging"
    assert result["review"]["items"][0]["code"] == "LOAD_PROGRESS"
    assert result["entry_review"]["decision"] == "hold"
    assert result["entry_review"]["applied"] is False
    assert result["entry_review"]["handoff"]["requires_user_confirmation"] is True
    assert [item["skill"] for item in final_context.trace] == ["draft-training-scheme"]


def test_runner_accepts_compact_candidate_without_workout_steps_v2():
    class MissingStepsModel(FakeModel):
        def generate(self, spec, prompt, payload):
            result = super().generate(spec, prompt, payload)
            if spec.output_model == "TrainingSchemeCandidate":
                result["first_four_weeks"][0]["workouts"][0].pop("training_prescription")
            return result

    runner = CoachSkillRunner(SkillRegistry.default(), MissingStepsModel())
    context = CoachRunContext(
        request={"intent": "draft_training_scheme"},
        facts={"planning_fact_pack": {}, "training_load_envelope": {}},
        policy={"coaching_mode": "race_preparation"},
    )

    result, _ = runner.run("draft-training-scheme", context)
    assert "training_prescription" not in result["first_four_weeks"][0]["workouts"][0]


def test_runner_accepts_two_week_compact_candidate():
    class TwoWeekModel(FakeModel):
        def generate(self, spec, prompt, payload):
            result = super().generate(spec, prompt, payload)
            if spec.output_model == "TrainingSchemeCandidate":
                result["first_two_weeks"] = result.pop("first_four_weeks")[:2]
                for week in result["first_two_weeks"]:
                    week["workouts"][0].pop("training_prescription", None)
            return result

    runner = CoachSkillRunner(SkillRegistry.default(), TwoWeekModel())
    context = CoachRunContext(
        request={"intent": "draft_training_scheme"},
        facts={"planning_fact_pack": {}, "training_load_envelope": {}},
        policy={"coaching_mode": "race_preparation"},
    )

    result, _ = runner.run("draft-training-scheme", context)
    assert len(result["first_four_weeks"]) == 2
    assert "training_prescription" not in result["first_four_weeks"][0]["workouts"][0]


def test_registry_exposes_framework_and_near_term_contracts():
    registry = SkillRegistry.default()

    assert registry.get("build-training-framework").output_model == "TrainingFramework"
    assert registry.get("build-near-term-schedule").output_model == "NearTermSchedule"
    assert "不要输出当前周/下一周课程" in registry.load_prompt("build-training-framework")
    assert "最近两天若完成长距离或质量课" in registry.load_prompt("build-near-term-schedule")


def test_dag_skills_use_compact_stage_specific_prompts():
    class DAGModel:
        def __init__(self):
            self.calls = []

        def generate(self, spec, prompt, payload):
            self.calls.append((spec.name, prompt, payload))
            if spec.output_model == "TrainingFramework":
                return {
                    "feasibility": {"level": "challenging", "confidence": 0.7, "summary": "需要专项耐力", "evidence": [], "gaps": []},
                    "ability_summary": {"weekly_km": 100, "quality_sessions": ["节奏跑"]},
                    "goal_demand_summary": {"target": "230", "marathon_pace": "3:33/km"},
                    "ability_gap": ["后程耐力"], "recommended_entry_phase": "专项期", "entry_rationale": "有稳定基础",
                    "periodization": [{"name": "基础期", "weeks": 3, "purpose": "吸收"}, {"name": "专项期", "weeks": 8, "purpose": "马拉松专项"}, {"name": "减量", "weeks": 2, "purpose": "恢复"}],
                    "load_progression": [{"week": 1, "target_km": 100, "target_load": 300, "recovery_week": False}],
                    "weekly_principles": {"long_run": "周末优先", "intensity": "两次关键课"},
                    "method_basis": ["五级强度模型"], "assumptions": [], "uncertainties": [], "risk_flags": [], "user_explanation": "先专项后减量",
                }
            return {
                "first_two_weeks": [{"week": 1, "target_km": 100, "target_load": 300, "focus": "专项", "recovery_week": False, "workouts": [{"weekday": "2026-08-08", "title": "长距离跑", "type": "long", "purpose": "耐力", "distance_km": 26, "duration_minutes": 130, "intensity": "可控", "stimuli": ["aerobic_endurance"], "is_key": True}]} , {"week": 2, "target_km": 98, "target_load": 290, "focus": "专项", "recovery_week": False, "workouts": [{"weekday": 5, "title": "节奏跑", "type": "quality", "purpose": "专项", "distance_km": 20, "duration_minutes": 100, "intensity": "可控", "stimuli": ["threshold_development"], "intensity_intent": {"zone": 4, "preferred_metric": "pace", "fallback_feel": "稳定"}, "is_key": True}]}],
                "review": {"status": "ok", "items": [], "safety_hold": None},
                "entry_review": {"recommended_entry_phase": "专项期", "decision": "advance", "confidence": 0.7, "evidence": ["稳定周量"], "adjustments": [], "applied": False, "applied_adjustments": [], "handoff": {"status": "review_pending", "requires_user_confirmation": True}},
                "assumptions": [], "uncertainties": [], "risk_flags": [], "data_gaps": [], "user_explanation": "",
            }

    model = DAGModel()
    registry = SkillRegistry.default()
    runner = CoachSkillRunner(registry, model)
    context = CoachRunContext(
        request={"intent": "draft"},
        facts={"framework_context": {}, "training_load_envelope": {}},
        policy={"coaching_mode": "race_preparation"},
    )
    framework, framework_ctx = runner.run("build-training-framework", context)
    schedule, _ = runner.run("build-near-term-schedule", CoachRunContext(
        request={"intent": "schedule"},
        facts={"training_framework": framework, "near_term_context": {}, "training_load_envelope": {}},
        policy={"coaching_mode": "race_preparation"}, trace=framework_ctx.trace,
    ))
    assert [name for name, _, _ in model.calls] == ["build-training-framework", "build-near-term-schedule"]
    assert len(model.calls[0][1]) < 5000
    assert len(schedule["first_two_weeks"]) == 2
    assert schedule["first_two_weeks"][0]["workouts"][0]["weekday"] == 5
    repaired_intent = schedule["first_two_weeks"][0]["workouts"][0]["intensity_intent"]
    assert repaired_intent["zone"] == 2
    assert repaired_intent["preferred_metric"] == "feel"
    assert repaired_intent["fallback_feel"] == "可控"


def test_dag_registry_requires_compact_stage_contexts():
    registry = SkillRegistry.default()

    assert registry.get("build-training-framework").required_facts == (
        "framework_context", "training_load_envelope",
    )
    assert registry.get("build-near-term-schedule").required_facts == (
        "training_framework", "near_term_context", "training_load_envelope",
    )


def test_draft_stage_model_payload_excludes_runtime_metadata():
    context = CoachRunContext(
        request={"generation_id": "debug-run", "ai_call_index": 1},
        facts={"framework_context": {"goal": {"distance": "marathon"}}},
        uncertainties=("sleep_missing",),
        policy={"coaching_mode": "race_preparation"},
        trace=({"skill": "previous"},),
    )

    payload = context.to_model_payload("build-training-framework")

    assert payload == {"facts": {"framework_context": {"goal": {"distance": "marathon"}}}}


def test_non_draft_skill_keeps_full_model_payload():
    context = CoachRunContext(request={"intent": "review"}, facts={"daily_facts": {}})

    assert context.to_model_payload("review-daily-training") == context.to_payload()


def test_runner_rejects_race_skill_in_continuous_mode():
    runner = CoachSkillRunner(SkillRegistry.default(), FakeModel())
    context = CoachRunContext(
        request={},
        facts={"planning_fact_pack": {}, "training_load_envelope": {}},
        policy={"coaching_mode": "continuous_running"},
    )

    with pytest.raises(SkillRunError, match="不支持"):
        runner.run("draft-training-scheme", context)


def test_openai_compatible_model_uses_generic_config_and_json_protocol(monkeypatch, caplog):
    captured = {}

    class Response:
        status_code = 200
        body = b'{"choices":[{"message":{"content":"{\\"status\\":\\"ok\\"}"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield self.body

    class Client:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    monkeypatch.setenv("NEURUN_AI_API_KEY", "generic-key")
    monkeypatch.setenv("NEURUN_AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("NEURUN_AI_MODEL", "example-model")
    spec = SkillRegistry.default().get("review-training-week")

    with caplog.at_level(logging.INFO, logger="src.coach_runtime.runner"):
        result = OpenAICompatibleSkillModel().generate(
            spec,
            "评估目标是否可行。",
            {"facts": {"goal": "synthetic"}},
        )

    system_prompt = captured["json"]["messages"][0]["content"]
    assert "json" in system_prompt
    assert "SkillFinding" in system_prompt
    assert '"status":"ok"' in system_prompt
    assert '"recommendations":[]' in system_prompt
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer generic-key"
    assert captured["json"]["model"] == "example-model"
    assert captured["json"]["max_tokens"] == 1800
    assert captured["method"] == "POST"
    assert result == {"status": "ok"}
    assert "AI draft call: chain_id=none skill=review-training-week" in caplog.text
    assert "prompt_parts=skill_instruction:" in caplog.text
    assert "input_sections=goal:" in caplog.text
    assert "synthetic" not in caplog.text

    main_spec = SkillRegistry.default().get("draft-training-scheme")
    with caplog.at_level(logging.INFO, logger="src.coach_runtime.runner"):
        OpenAICompatibleSkillModel().generate(main_spec, "制定方案。", {})
    main_prompt = captured["json"]["messages"][0]["content"]
    assert '"weekday":0' in main_prompt
    assert '"week":2' in main_prompt
    assert '"week":4' not in main_prompt
    assert '"stimuli":["pace_change"]' in main_prompt
    assert '"step_id":"main-repeat"' not in main_prompt
    assert "Monday=0" in main_prompt
    assert "skill=draft-training-scheme phase=response_headers" in caplog.text
    assert "skill=draft-training-scheme phase=first_byte" in caplog.text
    assert "skill=draft-training-scheme phase=complete_json" in caplog.text
    assert "skill=draft-training-scheme phase=stream_closed" in caplog.text
    assert "skill=draft-training-scheme phase=json_decoded" in caplog.text

    framework_spec = SkillRegistry.default().get("build-training-framework")
    with caplog.at_level(logging.INFO, logger="src.coach_runtime.runner"):
        OpenAICompatibleSkillModel().generate(framework_spec, "生成框架。", {})
    assert captured["json"]["max_tokens"] == 4000

    # 方案重规划输出完整候选（阶段 + 首四周 + 逐段处方），
    # 必须与框架/课表一致放宽到 4000，避免 1800 截断 JSON 导致 503
    revise_spec = SkillRegistry.default().get("revise-training-scheme")
    with caplog.at_level(logging.INFO, logger="src.coach_runtime.runner"):
        OpenAICompatibleSkillModel().generate(revise_spec, "重规划。", {})
    assert captured["json"]["max_tokens"] == 4000

    monkeypatch.setenv("NEURUN_AI_DEBUG_PROMPTS", "true")
    with caplog.at_level(logging.WARNING, logger="src.coach_runtime.runner"):
        OpenAICompatibleSkillModel().generate(
            framework_spec, "调试框架。",
            {"request": {"generation_id": "test-run", "ai_call_index": 1}, "facts": {"goal": "debug-fact"}},
        )
    assert "AI draft debug prompt: chain_id=test-run call=1 skill=build-training-framework" in caplog.text
    assert "调试框架。" in caplog.text
    assert "debug-fact" in caplog.text


def test_runner_logs_schema_reason_without_provider_payload(caplog):
    class InvalidFrameworkModel:
        def generate(self, spec, prompt, payload):
            return {
                "feasibility": {"level": "feasible", "confidence": 0.5},
                "periodization": [{"name": "基础期", "weeks": 3}],
                "load_progression": [{"week": 1, "target_km": 40}],
            }

    runner = CoachSkillRunner(SkillRegistry.default(), InvalidFrameworkModel())
    context = CoachRunContext(
        request={}, facts={"framework_context": {}, "training_load_envelope": {}},
        policy={"coaching_mode": "race_preparation"},
    )
    with caplog.at_level(logging.WARNING, logger="src.coach_runtime.runner"):
        with pytest.raises(SkillRunError, match="输出无效"):
            runner.run("build-training-framework", context)
    assert "AI output rejected: skill=build-training-framework" in caplog.text
    assert "periodization[0].purpose 不能为空" in caplog.text
    assert "provider_payload" not in caplog.text


def test_openai_compatible_model_returns_safe_actionable_error(monkeypatch):
    class Response:
        status_code = 400
        body = (
            b'{"error":{"type":"invalid_request_error",'
            b'"message":"bad request contained sk-private-secret and athlete facts"}}'
        )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield self.body

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    spec = SkillRegistry.default().get("review-training-week")

    with pytest.raises(SkillRunError) as caught:
        OpenAICompatibleSkillModel(api_key="test-key").generate(spec, "prompt", {})

    message = str(caught.value)
    assert message == "AI 服务请求格式被拒绝（invalid_request_error）"
    assert "sk-private-secret" not in message
    assert "athlete facts" not in message


def test_has_complete_choice_content_requires_nonempty_valid_json():
    """完整判定要求 content 非空且本身是合法 JSON 对象。"""
    from src.coach_runtime.runner import _has_complete_choice_content

    # 推理模型流式中间态：content 为空串（只有 reasoning_content），不应判定完整
    assert not _has_complete_choice_content(
        {"choices": [{"message": {"content": "", "reasoning_content": "..."}}]}
    )
    assert not _has_complete_choice_content(
        {"choices": [{"message": {"content": " "}}]}
    )
    # 截断/非法 JSON 不应判定完整
    assert not _has_complete_choice_content(
        {"choices": [{"message": {"content": "{"}}]}
    )
    assert not _has_complete_choice_content(
        {"choices": [{"message": {"content": "null"}}]}
    )
    assert not _has_complete_choice_content(
        {"choices": [{"message": {"content": "[1,2]"}}]}
    )
    # 结构缺失
    assert not _has_complete_choice_content({"choices": [{"message": {}}]})
    assert not _has_complete_choice_content({"choices": []})
    # 完整 JSON 对象才判定完成
    assert _has_complete_choice_content(
        {"choices": [{"message": {"content": "{\"status\":\"ok\"}"}}]}
    )


def test_reasoning_intermediate_empty_content_envelope_not_complete(monkeypatch, caplog):
    """content 为空串的 envelope 到达时不得提前 break，最终应报 JSON 解析错误。"""
    consumed = []

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield (
                b'{"choices":[{"message":{"content":"",'
                b'"reasoning_content":"long reasoning..."},"finish_reason":null}]}'
            )
            consumed.append("tail")

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    spec = SkillRegistry.default().get("review-training-week")

    with pytest.raises(SkillRunError, match="Skill 模型未返回有效 JSON"):
        OpenAICompatibleSkillModel(api_key="test-key").generate(spec, "prompt", {})

    # 修复前会误判 complete_json 提前 break；修复后必须读完整流再报错，
    # 两次 attempt 各消费一次流
    assert "phase=complete_json" not in caplog.text
    assert consumed == ["tail", "tail"]


def test_openai_compatible_model_retries_once_on_empty_content(monkeypatch, caplog):
    """provider 首次返回 content 为空的 envelope 时重试一次并成功。"""
    calls = []

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield self.body

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return Response(
                    b'{"choices":[{"message":{"content":"",'
                    b'"reasoning_content":"x"},"finish_reason":null}]}'
                )
            return Response(
                b'{"choices":[{"message":{"content":"{\\"status\\":\\"ok\\"}"}}]}'
            )

    monkeypatch.setattr("httpx.Client", Client)
    spec = SkillRegistry.default().get("review-training-week")

    result = OpenAICompatibleSkillModel(api_key="test-key").generate(spec, "prompt", {})

    assert result == {"status": "ok"}
    assert calls == [1, 2]
    # caplog 默认 WARNING 级别：重试尝试标记在 AI output rejected 日志中
    assert "attempt=1/2" in caplog.text


def test_openai_compatible_model_retries_then_fails_on_empty_content(monkeypatch):
    """连续两次空 content 时重试后仍报 JSON 解析错误，最多两次请求。"""
    calls = []

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield (
                b'{"choices":[{"message":{"content":"",'
                b'"reasoning_content":"x"},"finish_reason":null}]}'
            )

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            calls.append(len(calls) + 1)
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    spec = SkillRegistry.default().get("review-training-week")

    with pytest.raises(SkillRunError, match="Skill 模型未返回有效 JSON"):
        OpenAICompatibleSkillModel(api_key="test-key").generate(spec, "prompt", {})

    assert calls == [1, 2]


def test_openai_compatible_model_stops_after_complete_json_envelope(monkeypatch):
    consumed = []

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield b'{"choices":[{"message":{"content":"{\\"status\\":\\"ok\\"}"}}]}'
            consumed.append("tail")
            yield b"tail that would never be part of a valid response"

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("httpx.Client", Client)
    spec = SkillRegistry.default().get("review-training-week")

    result = OpenAICompatibleSkillModel(api_key="test-key").generate(spec, "prompt", {})

    assert result == {"status": "ok"}
    assert consumed == []


def test_weekly_review_model_stops_slow_stream_at_total_budget(monkeypatch):
    closed = []

    class SlowResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)
            return False

        def iter_bytes(self):
            yield b'{"choices":[]}'

    class Client:
        def __init__(self, *, timeout):
            assert timeout.read == 20

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return SlowResponse()

    ticks = iter((0.0, 0.0, 26.0))
    monkeypatch.setattr("httpx.Client", Client)
    monkeypatch.setattr(
        "src.coach_runtime.runner.time.monotonic", lambda: next(ticks),
    )
    spec = SkillRegistry.default().get("review-training-week")

    with pytest.raises(SkillRunError, match="AI 服务响应超时"):
        OpenAICompatibleSkillModel(api_key="test-key").generate(spec, "prompt", {})

    assert closed == [True]


def test_scheme_revision_model_uses_complex_read_budget_and_extended_total_deadline(monkeypatch):
    closed = []

    class SlowResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)
            return False

        def iter_bytes(self):
            yield b'{"choices":[]}'

    class Client:
        def __init__(self, *, timeout):
            assert timeout.read == 45

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return SlowResponse()

    ticks = iter((0.0, 0.0, 301.0))
    monkeypatch.setattr("httpx.Client", Client)
    monkeypatch.setattr(
        "src.coach_runtime.runner.time.monotonic", lambda: next(ticks),
    )
    spec = SkillRegistry.default().get("revise-training-scheme")

    with pytest.raises(SkillRunError, match="AI 服务响应超时"):
        OpenAICompatibleSkillModel(api_key="test-key").generate(spec, "prompt", {})

    assert closed == [True]


def test_near_term_schedule_accepts_and_normalizes_pace_intent():
    """NearTermSchedule 校验接受可选 pace_intent 并归一化为字符串。"""
    from src.coach_runtime.schemas import validate_output

    def workout(pace_intent):
        return {
            "weekday": 1, "title": "长距离", "type": "long_run",
            "purpose": "打有氧基础", "distance_km": 16.0,
            "intensity": "Z2", "stimuli": ["aerobic"],
            "intensity_intent": {"zone": 2, "preferred_metric": "feel",
                                 "fallback_feel": "能完整对话，结束仍有余量"},
            "is_key": True, "pace_intent": pace_intent,
        }

    weeks = [
        {"week": 1, "target_km": 40, "target_load": 180, "focus": "基础期",
         "recovery_week": False, "workouts": [workout("long_run"), workout("")]},
        {"week": 2, "target_km": 42, "target_load": 190, "focus": "基础期",
         "recovery_week": False, "workouts": [workout(" threshold ")],
         "pace_intent_missing": True},
    ]
    weeks[1]["workouts"][0].pop("pace_intent")
    result = validate_output("NearTermSchedule", {
        "first_two_weeks": weeks,
        "review": {"status": "ok", "items": [], "safety_hold": None},
        "entry_review": {"recommended_entry_phase": "基础期", "decision": "hold",
                         "confidence": 0.5, "evidence": [], "adjustments": [],
                         "applied": False, "applied_adjustments": [],
                         "handoff": {"status": "review_pending",
                                     "requires_user_confirmation": True}},
        "assumptions": [], "uncertainties": [], "risk_flags": [],
        "data_gaps": [], "user_explanation": "",
    })

    first = result["first_two_weeks"][0]["workouts"]
    assert first[0]["pace_intent"] == "long_run"
    # 空白 pace_intent 归一化为 None，不阻塞草稿
    assert first[1]["pace_intent"] is None
    # 缺失 pace_intent 保持可选，不影响校验
    assert "pace_intent" not in result["first_two_weeks"][1]["workouts"][0]
