"""Coach Skill 注册表和静态合同校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    prompt_path: Path
    required_facts: tuple[str, ...]
    output_model: str
    supported_modes: tuple[str, ...] = ("continuous_running", "race_preparation")


class SkillRegistry:
    """保存机器可执行的 Skill 元数据，SKILL.md 只维护工作流文本。"""

    def __init__(self, specs: list[SkillSpec]):
        self._specs = {spec.name: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("Skill 名称重复")

    def get(self, name: str) -> SkillSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"未知 Coach Skill: {name}") from exc

    def list(self) -> list[SkillSpec]:
        return sorted(self._specs.values(), key=lambda item: item.name)

    def load_prompt(self, name: str) -> str:
        spec = self.get(name)
        raw = spec.prompt_path.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        if len(parts) != 3:
            raise ValueError(f"{name} 缺少 YAML Front Matter")
        front_matter = yaml.safe_load(parts[1]) or {}
        if set(front_matter) != {"name", "description"}:
            raise ValueError(f"{name} Front Matter 只允许 name 和 description")
        if front_matter.get("name") != name:
            raise ValueError(f"{name} 的 Front Matter 名称不一致")
        return parts[2].strip()

    def validate(self) -> None:
        for spec in self.list():
            self.load_prompt(spec.name)

    @classmethod
    def default(cls, project_root: str | Path | None = None) -> "SkillRegistry":
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        prompt_root = root / "prompts" / "skills"
        versions = {
            "review-daily-training": "1.2.0",
            "summarize-training-day": "1.0.0",
            "build-training-framework": "1.0.0",
            "build-near-term-schedule": "1.0.0",
            "audit-training-scheme": "1.0.0",
            "draft-training-scheme": "1.5.0",
            "review-training-week": "1.3.0",
            "review-training-plan": "1.0.0",
            "revise-training-scheme": "1.5.0",
        }

        def spec(
            name: str,
            *,
            facts: tuple[str, ...] = (),
            output: str = "SkillFinding",
            modes: tuple[str, ...] = ("continuous_running", "race_preparation"),
        ) -> SkillSpec:
            return SkillSpec(
                name=name,
                version=versions.get(name, "1.0.0"),
                prompt_path=prompt_root / name / "SKILL.md",
                required_facts=facts,
                output_model=output,
                supported_modes=modes,
            )

        registry = cls([
            spec("summarize-training-day", facts=("training_day_fact",), output="TrainingDaySemanticSummary"),
            spec("review-daily-training", facts=("daily_facts", "active_scheme"), output="CoachInsight"),
            spec("build-training-framework", facts=("framework_context", "training_load_envelope"), output="TrainingFramework", modes=("race_preparation",)),
            spec("build-near-term-schedule", facts=("training_framework", "near_term_context", "training_load_envelope"), output="NearTermSchedule", modes=("race_preparation",)),
            spec("audit-training-scheme", facts=("scheme_candidate", "audit_context"), output="SchemeAudit", modes=("race_preparation",)),
            spec("draft-training-scheme", facts=("planning_fact_pack", "training_load_envelope"), output="TrainingSchemeCandidate", modes=("race_preparation",)),
            spec("review-training-week", facts=("natural_week_actual",)),
            spec(
                "review-training-plan",
                facts=("natural_week_actual", "active_scheme", "natural_week_progress"),
                output="TrainingPlanReview",
                modes=("race_preparation",),
            ),
            spec("propose-training-adjustment", facts=("active_scheme", "training_feedback")),
            spec("revise-training-scheme", facts=("planning_fact_pack", "training_load_envelope", "execution_summary", "active_scheme"), output="TrainingSchemeCandidate", modes=("race_preparation",)),
            spec("prepare-race-strategy", facts=("active_scheme", "race_context", "athlete_baseline"), modes=("race_preparation",)),
        ])
        registry.validate()
        return registry
