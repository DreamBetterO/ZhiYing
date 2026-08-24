from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class VisualNeedLevel(str, Enum):
    NONE = "none"
    SUPPORTIVE = "supportive"
    REQUIRED = "required"


_HARD_DECISIONS = {
    "permission_decision", "budget_decision", "upload_policy",
    "cache_decision", "retry_limit", "degradation", "artifact_commit",
}


@dataclass(frozen=True)
class AdvisorAdmission:
    task_whitelist: Mapping[str, set[str]]

    def allowed(self, model: str, task: str) -> bool:
        normalized = str(task).strip()
        return normalized not in _HARD_DECISIONS and normalized in self.task_whitelist.get(str(model), set())


class LocalDecisionPolicy:
    """Deterministic baseline for routing; never requires a language model."""

    _FORMULA_TERMS = ("公式", "方程", "分母", "分子", "等于", "积分", "导数", "矩阵")

    @staticmethod
    def visual_need(unit: Any) -> VisualNeedLevel:
        contract = getattr(unit, "visual_need", None)
        if bool(getattr(contract, "required", False)):
            return VisualNeedLevel.REQUIRED
        if getattr(unit, "visual_questions", None) or bool(getattr(unit, "needs_visual", False)):
            return VisualNeedLevel.SUPPORTIVE
        return VisualNeedLevel.NONE

    def uncertain_asr_spans(
        self,
        segments: Iterable[Mapping[str, Any]],
        *,
        confidence_threshold: float = 0.6,
        max_spans: int = 12,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment.get("text", ""))
            confidence = float(segment.get("confidence", 1.0) or 0.0)
            reasons = []
            if confidence < confidence_threshold:
                reasons.append("low_confidence")
            if any(term in text for term in self._FORMULA_TERMS):
                reasons.append("formula_or_term")
            if reasons:
                rows.append({**dict(segment), "review_reasons": reasons})
            if len(rows) >= max(0, int(max_spans)):
                break
        return rows
