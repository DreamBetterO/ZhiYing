"""V6.1 DocumentBlueprint v2：Schema、validator 与序列化。

Blueprint 是 LLM/本地策略生成的文档设计合同（目标合同 document_blueprint_v2）：
- 不含旧 source_section；只引用输入中真实存在的 unit/source/visual ID；
- 组件/布局/回退受 CapabilityManifest 限制；
- required/forbidden 约束可验证（constraint_mapping）；
- 不得包含 Markdown 正文、Word XML、任意坐标或本地绝对路径。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

BLUEPRINT_SCHEMA_VERSION = 2

BLUEPRINT_MODES = frozenset({"concept", "procedure", "case", "comparison", "mixed"})

ALLOWED_LAYOUT_HINTS = frozenset({"full_width", "image_text", "two_column", "compact_table"})

_FORBIDDEN_TOKEN_MARKERS = ("docx_xml", "<w:", "xmlns:w", "\\u0000")

_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]|^/{1,2}[^/]")

_LEGACY_WRAPPER_MARKERS = ("source_section",)


@dataclass
class BlueprintChapter:
    """蓝图中的一章：引用真实 unit，声明组件意图与布局。"""

    chapter_id: str = ""
    title: str = ""
    mode: str = "mixed"
    unit_refs: list[str] = field(default_factory=list)
    component_intents: list[str] = field(default_factory=list)
    layout_hint: str = "full_width"
    depth: str = "standard"
    target_chars: int = 260

    def __post_init__(self) -> None:
        if self.mode not in BLUEPRINT_MODES:
            self.mode = "mixed"
        if self.layout_hint not in ALLOWED_LAYOUT_HINTS:
            self.layout_hint = "full_width"
        self.unit_refs = [str(ref) for ref in self.unit_refs if str(ref).strip()]
        self.component_intents = [str(item) for item in self.component_intents if str(item).strip()]
        self.target_chars = max(80, int(self.target_chars))

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "title": self.title,
            "mode": self.mode,
            "unit_refs": list(self.unit_refs),
            "component_intents": list(self.component_intents),
            "layout_hint": self.layout_hint,
            "depth": self.depth,
            "target_chars": self.target_chars,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BlueprintChapter":
        return cls(
            chapter_id=str(data.get("chapter_id", "")),
            title=str(data.get("title", "")),
            mode=str(data.get("mode", "mixed")),
            unit_refs=list(data.get("unit_refs", [])),
            component_intents=list(data.get("component_intents", [])),
            layout_hint=str(data.get("layout_hint", "full_width")),
            depth=str(data.get("depth", "standard")),
            target_chars=int(data.get("target_chars", 260)),
        )


@dataclass
class DocumentBlueprint:
    """DocumentBlueprint v2。"""

    blueprint_id: str = ""
    schema_version: int = BLUEPRINT_SCHEMA_VERSION
    policy_version: int = 1
    evidence_version: int = 1
    capability_version: str = ""
    document_type: str = "course_notes"
    audience: str = ""
    purpose: str = ""
    density: str = ""
    design_tokens: list[str] = field(default_factory=list)
    front_matter_policy: dict[str, Any] = field(default_factory=dict)
    navigation_policy: dict[str, Any] = field(default_factory=dict)
    chapters: list[BlueprintChapter] = field(default_factory=list)
    component_intents: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    visual_roles: list[str] = field(default_factory=list)
    formula_evidence_policy: dict[str, Any] = field(default_factory=dict)
    layout_hints: list[str] = field(default_factory=list)
    constraint_mapping: dict[str, str] = field(default_factory=dict)
    component_fallbacks: dict[str, Any] = field(default_factory=dict)
    unresolved_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "evidence_version": self.evidence_version,
            "capability_version": self.capability_version,
            "document_type": self.document_type,
            "audience": self.audience,
            "purpose": self.purpose,
            "density": self.density,
            "design_tokens": list(self.design_tokens),
            "front_matter_policy": dict(self.front_matter_policy),
            "navigation_policy": dict(self.navigation_policy),
            "chapters": [chapter.to_dict() for chapter in self.chapters],
            "component_intents": list(self.component_intents),
            "source_refs": list(self.source_refs),
            "visual_roles": list(self.visual_roles),
            "formula_evidence_policy": dict(self.formula_evidence_policy),
            "layout_hints": list(self.layout_hints),
            "constraint_mapping": dict(self.constraint_mapping),
            "component_fallbacks": dict(self.component_fallbacks),
            "unresolved_questions": list(self.unresolved_questions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DocumentBlueprint":
        return cls(
            blueprint_id=str(data.get("blueprint_id", "")),
            schema_version=int(data.get("schema_version", BLUEPRINT_SCHEMA_VERSION)),
            policy_version=int(data.get("policy_version", 1)),
            evidence_version=int(data.get("evidence_version", 1)),
            capability_version=str(data.get("capability_version", "")),
            document_type=str(data.get("document_type", "course_notes")),
            audience=str(data.get("audience", "")),
            purpose=str(data.get("purpose", "")),
            density=str(data.get("density", "")),
            design_tokens=list(data.get("design_tokens", [])),
            front_matter_policy=dict(data.get("front_matter_policy", {})),
            navigation_policy=dict(data.get("navigation_policy", {})),
            chapters=[BlueprintChapter.from_dict(row) for row in data.get("chapters", []) if isinstance(row, dict)],
            component_intents=list(data.get("component_intents", [])),
            source_refs=list(data.get("source_refs", [])),
            visual_roles=list(data.get("visual_roles", [])),
            formula_evidence_policy=dict(data.get("formula_evidence_policy", {})),
            layout_hints=list(data.get("layout_hints", [])),
            constraint_mapping=dict(data.get("constraint_mapping", {})),
            component_fallbacks=dict(data.get("component_fallbacks", {})),
            unresolved_questions=list(data.get("unresolved_questions", [])),
        )


def validate_blueprint(blueprint: DocumentBlueprint, *, known_unit_ids: Iterable[str]) -> None:
    """校验 Blueprint 合同；不通过抛 ValueError。"""
    if int(blueprint.schema_version) != BLUEPRINT_SCHEMA_VERSION:
        raise ValueError(f"Blueprint schema_version 必须为 {BLUEPRINT_SCHEMA_VERSION}")
    if not blueprint.blueprint_id.strip():
        raise ValueError("blueprint_id 不能为空")
    ids = [chapter.chapter_id for chapter in blueprint.chapters]
    if not ids or any(not item for item in ids):
        raise ValueError("chapter_id 必须稳定且唯一")
    if len(ids) != len(set(ids)):
        raise ValueError("chapter_id 重复")

    known = set(known_unit_ids)
    for chapter in blueprint.chapters:
        unknown = [ref for ref in chapter.unit_refs if ref not in known]
        if unknown:
            raise ValueError(f"引用不存在的 unit：{', '.join(unknown)}")
        for intent in chapter.component_intents:
            if any(marker in intent for marker in _LEGACY_WRAPPER_MARKERS):
                raise ValueError("Blueprint 不得包含 source_section 旧包装")

    for hint in blueprint.layout_hints:
        if hint not in ALLOWED_LAYOUT_HINTS:
            raise ValueError(f"布局提示超出能力清单：{hint}")

    all_tokens = [*blueprint.design_tokens, *blueprint.component_intents]
    for token in all_tokens:
        lowered = str(token).lower()
        if any(marker in lowered for marker in _FORBIDDEN_TOKEN_MARKERS):
            raise ValueError(f"Blueprint 含禁止的渲染私有内容：{token}")

    for ref in blueprint.source_refs:
        if _ABSOLUTE_PATH_PATTERN.match(str(ref)):
            raise ValueError(f"Blueprint 不得含本地绝对路径：{ref}")
