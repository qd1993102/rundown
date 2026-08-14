"""Skill 运行期间只增不改的共享上下文。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class CoachRunContext:
    """保存请求、事实、策略与 trace 的不可变快照。"""

    request: dict[str, Any]
    facts: dict[str, Any]
    uncertainties: tuple[str, ...] = ()
    policy: dict[str, Any] = field(default_factory=dict)
    trace: tuple[dict[str, Any], ...] = ()

    def with_trace(self, item: dict[str, Any]) -> "CoachRunContext":
        return replace(self, trace=(*self.trace, copy.deepcopy(item)))

    def to_payload(self) -> dict[str, Any]:
        return {
            "request": copy.deepcopy(self.request),
            "facts": copy.deepcopy(self.facts),
            "uncertainties": list(self.uncertainties),
            "policy": copy.deepcopy(self.policy),
            "trace": copy.deepcopy(list(self.trace)),
        }

    def to_model_payload(self, skill_name: str) -> dict[str, Any]:
        """Return the smallest fact contract needed by a model invocation.

        Request ids and trace entries are useful for local audit logs, but are
        neither training evidence nor model instructions.  The two-stage draft
        chain also carries its data gaps inside its explicit fact contracts, so
        repeating the generic context envelope only wastes prompt budget.
        """
        if skill_name in {"build-training-framework", "build-near-term-schedule"}:
            return {"facts": copy.deepcopy(self.facts)}
        return self.to_payload()
