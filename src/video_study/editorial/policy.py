"""V6.1 EditorialPolicy v1：可审计的编辑约束与机器可验证谓词。

约束体系来自 V6.1 目标合同 editorial_policy：
- required / preferred / forbidden 三类；
- content_focus / structure_preference / visual_preference / formula_preference；
- hard_contract_conflicts 与逐条 compliance predicate。

本地 compiler（editorial.intent）覆盖批准的最小自然语言集合；
无法本地解析的复杂意图由 intent compiler 标记 partially_satisfied。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

POLICY_VERSION = 1

# 稳定约束代码（机器可验证；与目标合同 local_minimum_intents 一一对应）
CONSTRAINT_CODES = frozenset({
    "overview",
    "learning_objectives",
    "review",
    "fixed_numbering",
    "case_solution",
    "concept_properties",
    "procedure",
    "comparison",
    "concise",
    "recommended",
    "rich",
    "image_less",
    "image_more",
    "image_forbidden",
    "formula_priority",
    "table_priority",
    "source_display",
})

LEGACY_ROLE_MAP: Mapping[str, tuple[str, ...]] = {
    "overview": ("overview",),
    "learning_objectives": ("learning_objectives", "learning_objective", "objectives"),
    "review": ("review", "knowledge_thread", "rule_checklist", "open_questions"),
}

_FIXED_NUMBERING_PATTERN = re.compile(r"^\d{1,2}\s*[·.、．]\s*\S|^第\s*\d+\s*[章节讲]")


def constraint_code(constraint: "EditorialConstraint | Mapping[str, Any]") -> str:
    """返回约束的稳定代码。"""
    if isinstance(constraint, EditorialConstraint):
        return constraint.code
    return str(constraint.get("code", ""))


@dataclass(frozen=True)
class EditorialConstraint:
    """单条编辑约束。"""

    kind: str  # required | preferred | forbidden
    code: str
    source_span: str = ""
    predicate: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"required", "preferred", "forbidden"}:
            object.__setattr__(self, "kind", "preferred")
        if self.code not in CONSTRAINT_CODES:
            raise ValueError(f"未知约束代码：{self.code}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "code": self.code,
            "source_span": self.source_span,
            "predicate": self.predicate,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EditorialConstraint":
        return cls(
            kind=str(data.get("kind", "preferred")),
            code=str(data.get("code", "")),
            source_span=str(data.get("source_span", "")),
            predicate=str(data.get("predicate", "")),
            reason=str(data.get("reason", "")),
        )


def _walk_components(components: Iterable[Mapping[str, Any]]) -> Iterable[dict[str, Any]]:
    for component in components:
        yield dict(component)
        yield from _walk_components(component.get("children", []))


def _role_of(component: Mapping[str, Any]) -> str:
    return str(component.get("semantic_role", ""))


def _title_of(component: Mapping[str, Any]) -> str:
    return str(component.get("title", "") or component.get("text", ""))


@dataclass
class EditorialPolicy:
    """课程级编辑政策：required/preferred/forbidden 与偏好档位。"""

    version: int = POLICY_VERSION
    required: list[EditorialConstraint] = field(default_factory=list)
    preferred: list[EditorialConstraint] = field(default_factory=list)
    forbidden: list[EditorialConstraint] = field(default_factory=list)
    content_focus: str = ""
    structure_preference: str = ""
    visual_preference: str = ""
    formula_preference: str = ""
    table_preference: str = ""
    source_display: str = ""
    density: str = ""
    hard_contract_conflicts: list[str] = field(default_factory=list)
    unmatched_hints: list[str] = field(default_factory=list)
    status: str = "satisfied"  # satisfied | partially_satisfied

    def __post_init__(self) -> None:
        self.required = [row for row in self.required if isinstance(row, EditorialConstraint)]
        self.preferred = [row for row in self.preferred if isinstance(row, EditorialConstraint)]
        self.forbidden = [row for row in self.forbidden if isinstance(row, EditorialConstraint)]
        if self.status not in {"satisfied", "partially_satisfied"}:
            self.status = "partially_satisfied" if self.unmatched_hints else "satisfied"

    # -- 机器可验证谓词 -----------------------------------------------------

    def forbidden_hits(self, components: Iterable[Mapping[str, Any]]) -> list[str]:
        """返回组件树中命中 forbidden 的约束代码（命中即违禁）。"""
        hits: list[str] = []
        rows = [row for row in self.forbidden]
        if any(row.code == "fixed_numbering" for row in rows):
            for component in _walk_components(components):
                if _FIXED_NUMBERING_PATTERN.match(_title_of(component)):
                    hits.append("fixed_numbering")
                    break
        for row in rows:
            if row.code in LEGACY_ROLE_MAP and row.code not in hits:
                roles = LEGACY_ROLE_MAP[row.code]
                if any(_role_of(component) in roles for component in _walk_components(components)):
                    hits.append(row.code)
        return hits

    def required_missing(self, components: Iterable[Mapping[str, Any]]) -> list[str]:
        """返回组件树中缺失的 required 约束代码。"""
        missing: list[str] = []
        rows = [row for row in self.required if row.code in LEGACY_ROLE_MAP]
        roles = {_role_of(component) for component in _walk_components(components)}
        for row in rows:
            if not any(role in roles for role in LEGACY_ROLE_MAP[row.code]):
                missing.append(row.code)
        return missing

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "required": [row.to_dict() for row in self.required],
            "preferred": [row.to_dict() for row in self.preferred],
            "forbidden": [row.to_dict() for row in self.forbidden],
            "content_focus": self.content_focus,
            "structure_preference": self.structure_preference,
            "visual_preference": self.visual_preference,
            "formula_preference": self.formula_preference,
            "table_preference": self.table_preference,
            "source_display": self.source_display,
            "density": self.density,
            "hard_contract_conflicts": list(self.hard_contract_conflicts),
            "unmatched_hints": list(self.unmatched_hints),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EditorialPolicy":
        return cls(
            version=int(data.get("version", POLICY_VERSION)),
            required=[EditorialConstraint.from_dict(row) for row in data.get("required", []) if isinstance(row, dict)],
            preferred=[EditorialConstraint.from_dict(row) for row in data.get("preferred", []) if isinstance(row, dict)],
            forbidden=[EditorialConstraint.from_dict(row) for row in data.get("forbidden", []) if isinstance(row, dict)],
            content_focus=str(data.get("content_focus", "")),
            structure_preference=str(data.get("structure_preference", "")),
            visual_preference=str(data.get("visual_preference", "")),
            formula_preference=str(data.get("formula_preference", "")),
            table_preference=str(data.get("table_preference", "")),
            source_display=str(data.get("source_display", "")),
            density=str(data.get("density", "")),
            hard_contract_conflicts=list(data.get("hard_contract_conflicts", [])),
            unmatched_hints=list(data.get("unmatched_hints", [])),
            status=str(data.get("status", "satisfied")),
        )
