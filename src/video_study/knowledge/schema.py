"""知识整理升级的核心数据结构。

定义 LessonPlan、FrameSemantic、VisualBinding、ContentBlock、
KnowledgeUnit 等中间结构，以及它们使用的枚举常量和序列化函数。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 枚举常量
# ---------------------------------------------------------------------------

ROLE_TAGS = frozenset({
    "core",
    "supporting",
    "boundary",
    "navigation",
    "time_sensitive_tangent",
    "noise",
    "uncertain",
})

KNOWLEDGE_TYPES = frozenset({
    "concept",
    "rule",
    "procedure",
    "mechanism",
    "comparison",
    "case",
    "boundary_case",
    "visual_or_formula",
    "conclusion",
})

KEEP_MODES = frozenset({
    "expand",
    "concise",
    "index_only",
    "omit",
})

IMPORTANCE_LEVELS = frozenset({
    "core",
    "supporting",
    "peripheral",
})

COURSE_FORMS = frozenset({
    "rule_teaching",
    "concept_lecture",
    "case_review",
    "software_demo",
    "meeting_discussion",
    "general",
})

VISUAL_COURSE_FORMS = frozenset({
    "speech_dominant",
    "slide_dominant",
    "screen_demo",
    "chart_analysis",
    "talking_head",
    "mixed",
})

VISUAL_DEPENDENCY_LEVELS = frozenset({"low", "medium", "high"})

VISUAL_TEACHING_LEVELS = frozenset({"auto", "minimal", "balanced", "enhanced"})

VISUAL_ROLES = frozenset({
    "locate",
    "explain",
    "procedure",
    "compare",
    "evidence",
    "recap",
})

VISUAL_SEQUENCE_MODES = frozenset({"single", "comparison_pair", "progression_grid"})

VISUAL_EXPLANATION_DEPTHS = frozenset({"caption", "brief_note", "teaching_note"})

DETAIL_LEVELS = frozenset({
    "mention",
    "brief",
    "standard",
    "deep",
})

SUPPLEMENT_POLICIES = frozenset({
    "derived_and_short_tip",
    "derived_only",
    "index_only",
})

VISUAL_TYPES = frozenset({
    "chart",
    "screenshot",
    "diagram",
    "photo",
    "other",
})

BINDING_DECISIONS = frozenset({
    "bind",
    "none",
})

ORIGIN_TYPES = frozenset({
    "source_backed",
    "derived_explanation",
    "audio_backed",
    "visual_backed",
    "cross_modal_derived",
    "constructed_example",
    "model_aid",
    "external_fact",
})

BLOCK_TYPES = frozenset({
    "paragraph",
    "rule_list",
    "steps",
    "example",
    "pitfall",
    "visual_lead_in",
    "figure",
    "figure_caption",
    "visual_takeaway",
    "visual_group",
    "understanding_tip",
    "source_links",
})

CLAIM_KINDS = frozenset({
    "conclusion", "definition", "explanation", "mechanism", "condition",
    "step", "example", "pitfall", "visual_fact", "model_aid",
})

CLAIM_ORIGINS = frozenset({"audio_backed", "visual_backed", "model_aid"})


# ---------------------------------------------------------------------------
# CourseProfile
# ---------------------------------------------------------------------------

@dataclass
class CourseProfile:
    """课程画像：领域、形态、主要知识类型和核心主线。"""

    domain: str = ""
    sub_domain: str = ""
    course_form: str = "general"
    primary_knowledge_types: list[str] = field(default_factory=list)
    core_thread: str = ""
    side_topics: list[str] = field(default_factory=list)
    confidence: float = 0.0
    terminology_hints: list[str] = field(default_factory=list)
    basis: str = ""

    def __post_init__(self) -> None:
        if self.course_form not in COURSE_FORMS:
            self.course_form = "general"
        self.primary_knowledge_types = [
            t for t in self.primary_knowledge_types if t in KNOWLEDGE_TYPES
        ]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CourseProfile:
        return cls(
            domain=str(data.get("domain", "")),
            sub_domain=str(data.get("sub_domain", "")),
            course_form=str(data.get("course_form", "general")),
            primary_knowledge_types=list(data.get("primary_knowledge_types", [])),
            core_thread=str(data.get("core_thread", "")),
            side_topics=list(data.get("side_topics", [])),
            confidence=float(data.get("confidence", 0.0)),
            terminology_hints=list(data.get("terminology_hints", [])),
            basis=str(data.get("basis", "")),
        )


# ---------------------------------------------------------------------------
# ContentDecision
# ---------------------------------------------------------------------------

@dataclass
class ContentDecision:
    """单段内容的角色、知识类型和保留策略。"""

    decision_id: str = ""
    source_segment_ids: list[str] = field(default_factory=list)
    role_tags: list[str] = field(default_factory=list)
    knowledge_types: list[str] = field(default_factory=list)
    importance: str = "supporting"
    keep_mode: str = "concise"
    confidence: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        self.role_tags = [t for t in self.role_tags if t in ROLE_TAGS]
        self.knowledge_types = [t for t in self.knowledge_types if t in KNOWLEDGE_TYPES]
        if self.importance not in IMPORTANCE_LEVELS:
            self.importance = "supporting"
        if self.keep_mode not in KEEP_MODES:
            self.keep_mode = "concise"
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentDecision:
        return cls(
            decision_id=str(data.get("decision_id", "")),
            source_segment_ids=list(data.get("source_segment_ids", [])),
            role_tags=list(data.get("role_tags", [])),
            knowledge_types=list(data.get("knowledge_types", [])),
            importance=str(data.get("importance", "supporting")),
            keep_mode=str(data.get("keep_mode", "concise")),
            confidence=float(data.get("confidence", 0.0)),
            reason=str(data.get("reason", "")),
        )


# ---------------------------------------------------------------------------
# LessonPlan 系列（合并画像 + 选择 + 详略计划）
# ---------------------------------------------------------------------------

@dataclass
class VisualProfile:
    """课程级视觉画像，只决定默认倾向，不直接强制知识点配图。"""

    course_form: str = "speech_dominant"
    visual_dependency: str = "low"
    dominant_visuals: list[str] = field(default_factory=list)
    recommended_level: str = "minimal"
    signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.course_form not in VISUAL_COURSE_FORMS:
            self.course_form = "speech_dominant"
        if self.visual_dependency not in VISUAL_DEPENDENCY_LEVELS:
            self.visual_dependency = "low"
        if self.recommended_level not in VISUAL_TEACHING_LEVELS - {"auto"}:
            self.recommended_level = "minimal"
        self.dominant_visuals = [str(item) for item in self.dominant_visuals if str(item)]
        self.signals = [str(item) for item in self.signals if str(item)]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualProfile:
        return cls(
            course_form=str(data.get("course_form", "speech_dominant")),
            visual_dependency=str(data.get("visual_dependency", "low")),
            dominant_visuals=list(data.get("dominant_visuals", [])),
            recommended_level=str(data.get("recommended_level", "minimal")),
            signals=list(data.get("signals", [])),
        )


@dataclass
class VisualNeed:
    """知识点的视觉需求。"""

    required: bool = False
    question: str = ""
    role: str = "explain"
    target_count: int = 0
    max_count: int = 1
    sequence_mode: str = "single"
    explanation_depth: str = "brief_note"
    success_criteria: list[str] = field(default_factory=list)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.role not in VISUAL_ROLES:
            self.role = "explain"
        if self.sequence_mode not in VISUAL_SEQUENCE_MODES:
            self.sequence_mode = "single"
        if self.explanation_depth not in VISUAL_EXPLANATION_DEPTHS:
            self.explanation_depth = "brief_note"
        self.max_count = max(1, min(3, int(self.max_count)))
        self.target_count = max(0, min(self.max_count, int(self.target_count)))
        if self.required and self.target_count == 0:
            self.target_count = 1
        if not self.required:
            self.target_count = 0
        self.success_criteria = [str(item) for item in self.success_criteria if str(item)][:4]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualNeed:
        return cls(
            required=bool(data.get("required", False)),
            question=str(data.get("question", "")),
            role=str(data.get("role", "explain")),
            target_count=int(data.get("target_count", 1 if data.get("required") else 0)),
            max_count=int(data.get("max_count", 1)),
            sequence_mode=str(data.get("sequence_mode", "single")),
            explanation_depth=str(data.get("explanation_depth", "brief_note")),
            success_criteria=list(data.get("success_criteria", [])),
            reason=str(data.get("reason", "")),
        )


@dataclass
class DepthFactors:
    """课程级详略分配的评分因子，0-3 四档。"""

    dependency: int = 1
    difficulty: int = 1
    error_risk: int = 1
    transfer_value: int = 1
    source_capacity: int = 1
    reason: str = ""

    def __post_init__(self) -> None:
        for name in ("dependency", "difficulty", "error_risk", "transfer_value", "source_capacity"):
            value = int(getattr(self, name, 1))
            setattr(self, name, max(0, min(3, value)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DepthFactors:
        return cls(
            dependency=int(data.get("dependency", 1)),
            difficulty=int(data.get("difficulty", 1)),
            error_risk=int(data.get("error_risk", 1)),
            transfer_value=int(data.get("transfer_value", 1)),
            source_capacity=int(data.get("source_capacity", 1)),
            reason=str(data.get("reason", "")),
        )


@dataclass
class EvidenceSpan:
    """知识点对应的精确课堂证据时间段。"""

    start_seconds: float = 0.0
    end_seconds: float = 0.0
    segment_ids: list[str] = field(default_factory=list)
    purpose: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceSpan:
        return cls(
            start_seconds=float(data.get("start_seconds", 0.0)),
            end_seconds=float(data.get("end_seconds", 0.0)),
            segment_ids=list(data.get("segment_ids", [])),
            purpose=str(data.get("purpose", "")),
        )


@dataclass
class VisualQuestion:
    """由知识点生成的像素可回答问题。"""

    question_id: str = ""
    unit_id: str = ""
    question: str = ""
    answerable_from_pixels: bool = True
    expected_entities: list[str] = field(default_factory=list)
    expected_relation: str = ""
    preferred_visual_role: str = "worked_example"
    negative_cues: list[str] = field(default_factory=list)
    anchor_spans: list[EvidenceSpan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "unit_id": self.unit_id,
            "question": self.question,
            "answerable_from_pixels": self.answerable_from_pixels,
            "expected_entities": self.expected_entities,
            "expected_relation": self.expected_relation,
            "preferred_visual_role": self.preferred_visual_role,
            "negative_cues": self.negative_cues,
            "anchor_spans": [span.to_dict() for span in self.anchor_spans],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualQuestion:
        return cls(
            question_id=str(data.get("question_id", "")),
            unit_id=str(data.get("unit_id", data.get("plan_id", ""))),
            question=str(data.get("question", "")),
            answerable_from_pixels=bool(data.get("answerable_from_pixels", True)),
            expected_entities=list(data.get("expected_entities", [])),
            expected_relation=str(data.get("expected_relation", "")),
            preferred_visual_role=str(data.get("preferred_visual_role", "worked_example")),
            negative_cues=list(data.get("negative_cues", [])),
            anchor_spans=[
                EvidenceSpan.from_dict(span)
                for span in data.get("anchor_spans", [])
                if isinstance(span, dict)
            ],
        )


@dataclass
class VisualJob:
    """A bounded visual inference unit. Detail jobs are submitted dynamically."""

    job_id: str = ""
    kind: str = "compare"
    question: VisualQuestion = field(default_factory=VisualQuestion)
    contract: VisualNeed = field(default_factory=VisualNeed)
    max_candidates: int = 4

    def __post_init__(self) -> None:
        if self.kind not in {"compare", "detail"}:
            self.kind = "compare"
        self.max_candidates = max(1, min(4, int(self.max_candidates)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "question": self.question.to_dict(),
            "contract": self.contract.to_dict(),
            "max_candidates": self.max_candidates,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualJob:
        return cls(
            job_id=str(data.get("job_id", "")),
            kind=str(data.get("kind", "compare")),
            question=VisualQuestion.from_dict(dict(data.get("question", {}))),
            contract=VisualNeed.from_dict(dict(data.get("contract", {}))),
            max_candidates=int(data.get("max_candidates", 4)),
        )


@dataclass
class VisualEvidence:
    """图片进入正文前的结构化视觉证据。"""

    evidence_id: str = ""
    question_id: str = ""
    image_path: str = ""
    timestamp: float = 0.0
    ocr_text: str = ""
    visual_summary: str = ""
    matched_knowledge_point_id: str = ""
    matched_knowledge_id: str = ""
    matched_knowledge_ids: list[str] = field(default_factory=list)
    primary_unit_id: str = ""
    relevance_score: float = 0.0
    why_useful: str = ""
    match_reason: str = ""
    suggested_caption: str = ""
    explanation_for_reader: str = ""
    frame_id: str = ""
    source_timestamp: float = 0.0
    dedup_group_id: str = ""
    scene_cluster_id: str = ""
    image_sha256: str = ""
    perceptual_hash: str = ""
    visible_evidence: list[str] = field(default_factory=list)
    visual_role: str = "explain"
    criteria_met: list[str] = field(default_factory=list)
    criteria_missing: list[str] = field(default_factory=list)
    visual_answer: str = ""
    needs_detail_pass: bool = False
    sequence_mode: str = "single"
    visual_group_id: str = ""
    capability_warning: str = ""
    decision: str = "no_match"
    confidence: float = 0.0
    source: str = "fallback"
    candidate_count: int = 0

    def __post_init__(self) -> None:
        if not self.matched_knowledge_point_id:
            self.matched_knowledge_point_id = self.matched_knowledge_id
        if not self.matched_knowledge_id:
            self.matched_knowledge_id = self.matched_knowledge_point_id
        if not self.matched_knowledge_ids and self.matched_knowledge_id:
            self.matched_knowledge_ids = [self.matched_knowledge_id]
        if not self.primary_unit_id:
            self.primary_unit_id = self.matched_knowledge_id
        if not self.source_timestamp and self.timestamp:
            self.source_timestamp = self.timestamp
        if not self.timestamp and self.source_timestamp:
            self.timestamp = self.source_timestamp
        if not self.scene_cluster_id:
            self.scene_cluster_id = self.dedup_group_id
        if not self.dedup_group_id:
            self.dedup_group_id = self.scene_cluster_id
        if not self.match_reason:
            self.match_reason = self.why_useful
        if self.visual_role not in VISUAL_ROLES:
            self.visual_role = "explain"
        if self.sequence_mode not in VISUAL_SEQUENCE_MODES:
            self.sequence_mode = "single"
        self.visible_evidence = [str(item) for item in self.visible_evidence if str(item)]
        self.criteria_met = [str(item) for item in self.criteria_met if str(item)]
        self.criteria_missing = [str(item) for item in self.criteria_missing if str(item)]
        self.relevance_score = max(0.0, min(1.0, float(self.relevance_score)))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if self.decision not in {"select", "no_match"}:
            self.decision = "no_match"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualEvidence:
        image_path = str(data.get("image_path", data.get("path", "")))
        matched_id = str(data.get(
            "matched_knowledge_id",
            data.get("matched_knowledge_point_id", data.get("unit_id", "")),
        ))
        timestamp = float(data.get("source_timestamp", data.get("timestamp", data.get("timestamp_seconds", 0.0))))
        return cls(
            evidence_id=str(data.get("evidence_id", "")),
            question_id=str(data.get("question_id", "")),
            image_path=image_path,
            timestamp=timestamp,
            ocr_text=str(data.get("ocr_text", "")),
            visual_summary=str(data.get("visual_summary", "")),
            matched_knowledge_point_id=str(data.get("matched_knowledge_point_id", matched_id)),
            matched_knowledge_id=matched_id,
            matched_knowledge_ids=[str(item) for item in data.get("matched_knowledge_ids", []) if str(item)],
            primary_unit_id=str(data.get("primary_unit_id", matched_id)),
            relevance_score=float(data.get("relevance_score", data.get("confidence", 0.0))),
            why_useful=str(data.get("why_useful", "")),
            match_reason=str(data.get("match_reason", data.get("why_useful", ""))),
            suggested_caption=str(data.get("suggested_caption", "")),
            explanation_for_reader=str(data.get("explanation_for_reader", "")),
            frame_id=str(data.get("frame_id", data.get("image_id", ""))),
            source_timestamp=timestamp,
            dedup_group_id=str(data.get("dedup_group_id", data.get("scene_cluster_id", ""))),
            scene_cluster_id=str(data.get("scene_cluster_id", data.get("dedup_group_id", ""))),
            image_sha256=str(data.get("image_sha256", "")),
            perceptual_hash=str(data.get("perceptual_hash", "")),
            visible_evidence=[str(item) for item in data.get("visible_evidence", []) if str(item)],
            visual_role=str(data.get("visual_role", "explain")),
            criteria_met=[str(item) for item in data.get("criteria_met", []) if str(item)],
            criteria_missing=[str(item) for item in data.get("criteria_missing", []) if str(item)],
            visual_answer=str(data.get("visual_answer", "")),
            needs_detail_pass=bool(data.get("needs_detail_pass", False)),
            sequence_mode=str(data.get("sequence_mode", "single")),
            visual_group_id=str(data.get("visual_group_id", "")),
            capability_warning=str(data.get("capability_warning", "")),
            decision=str(data.get("decision", "select" if image_path else "no_match")),
            confidence=float(data.get("confidence", data.get("relevance_score", 0.0))),
            source=str(data.get("source", "fallback")),
            candidate_count=int(data.get("candidate_count", 0)),
        )


@dataclass
class UnitPlan:
    """单个知识点的写作计划。"""

    plan_id: str = ""
    title: str = ""
    role: str = "supporting"
    knowledge_types: list[str] = field(default_factory=list)
    detail_level: str = "standard"
    detail_reason: str = ""
    required_facets: list[str] = field(default_factory=list)
    source_segment_ids: list[str] = field(default_factory=list)
    visual_need: VisualNeed = field(default_factory=VisualNeed)
    supplement_policy: str = "derived_and_short_tip"
    learner_question: str = ""
    depth_factors: DepthFactors = field(default_factory=DepthFactors)
    target_chars: int = 260
    evidence_spans: list[EvidenceSpan] = field(default_factory=list)
    needs_visual: bool = False
    visual_questions: list[VisualQuestion] = field(default_factory=list)
    expansion_allowed: bool = True
    classroom_evidence: list[str] = field(default_factory=list)
    assistant_supplement: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.detail_level not in DETAIL_LEVELS:
            self.detail_level = "standard"
        if self.supplement_policy not in SUPPLEMENT_POLICIES:
            self.supplement_policy = "derived_and_short_tip"
        if self.role not in IMPORTANCE_LEVELS:
            self.role = "supporting"
        self.knowledge_types = [
            t for t in self.knowledge_types if t in KNOWLEDGE_TYPES
        ]
        self.target_chars = max(80, int(self.target_chars))
        self.needs_visual = bool(self.needs_visual or self.visual_need.required or self.visual_questions)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["importance"] = self.role
        d["difficulty"] = self.depth_factors.difficulty
        d["depth_level"] = self.detail_level
        d["source_timestamps"] = [
            {
                "start_seconds": span.start_seconds,
                "end_seconds": span.end_seconds,
                "segment_ids": span.segment_ids,
            }
            for span in self.evidence_spans
        ]
        d["visual_question_ids"] = [q.question_id for q in self.visual_questions]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnitPlan:
        vn = data.get("visual_need", {})
        spans_data = data.get("evidence_spans") or data.get("source_timestamps") or []
        visual_questions = [
            VisualQuestion.from_dict(item)
            for item in data.get("visual_questions", [])
            if isinstance(item, dict)
        ]
        return cls(
            plan_id=str(data.get("plan_id", "")),
            title=str(data.get("title", "")),
            role=str(data.get("role", data.get("importance", "supporting"))),
            knowledge_types=list(data.get("knowledge_types", [])),
            detail_level=str(data.get("detail_level", data.get("depth_level", "standard"))),
            detail_reason=str(data.get("detail_reason", "")),
            required_facets=list(data.get("required_facets", [])),
            source_segment_ids=list(data.get("source_segment_ids", [])),
            visual_need=VisualNeed.from_dict(vn) if isinstance(vn, dict) else VisualNeed(),
            supplement_policy=str(data.get("supplement_policy", "derived_and_short_tip")),
            learner_question=str(data.get("learner_question", "")),
            depth_factors=DepthFactors.from_dict(data.get("depth_factors", {})) if isinstance(data.get("depth_factors", {}), dict) else DepthFactors(),
            target_chars=int(data.get("target_chars", 260)),
            evidence_spans=[
                EvidenceSpan.from_dict(item)
                for item in spans_data
                if isinstance(item, dict)
            ],
            needs_visual=bool(data.get("needs_visual", False)),
            visual_questions=visual_questions,
            expansion_allowed=bool(data.get("expansion_allowed", True)),
            classroom_evidence=list(data.get("classroom_evidence", [])),
            assistant_supplement=list(data.get("assistant_supplement", [])),
        )


@dataclass
class ChapterPlan:
    """语义章节计划。"""

    chapter_id: str = ""
    title: str = ""
    source_segment_ids: list[str] = field(default_factory=list)
    unit_plans: list[UnitPlan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "title": self.title,
            "source_segment_ids": self.source_segment_ids,
            "unit_plans": [up.to_dict() for up in self.unit_plans],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChapterPlan:
        return cls(
            chapter_id=str(data.get("chapter_id", "")),
            title=str(data.get("title", "")),
            source_segment_ids=list(data.get("source_segment_ids", [])),
            unit_plans=[UnitPlan.from_dict(up) for up in data.get("unit_plans", [])],
        )


@dataclass
class SideTopic:
    """旁支主题。"""

    title: str = ""
    keep_mode: str = "index_only"
    source_segment_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.keep_mode not in KEEP_MODES:
            self.keep_mode = "index_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SideTopic:
        return cls(
            title=str(data.get("title", "")),
            keep_mode=str(data.get("keep_mode", "index_only")),
            source_segment_ids=list(data.get("source_segment_ids", [])),
        )


@dataclass
class LessonPlan:
    """课程级写作计划：合并画像、内容选择和逐知识点详略。"""

    schema_version: int = 1
    domain: str = ""
    course_form: str = "general"
    core_thread: str = ""
    terminology: list[str] = field(default_factory=list)
    visual_profile: VisualProfile = field(default_factory=VisualProfile)
    chapters: list[ChapterPlan] = field(default_factory=list)
    side_topics: list[SideTopic] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.course_form not in COURSE_FORMS:
            self.course_form = "general"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "domain": self.domain,
            "course_form": self.course_form,
            "core_thread": self.core_thread,
            "terminology": self.terminology,
            "visual_profile": self.visual_profile.to_dict(),
            "chapters": [ch.to_dict() for ch in self.chapters],
            "side_topics": [st.to_dict() for st in self.side_topics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LessonPlan:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            domain=str(data.get("domain", "")),
            course_form=str(data.get("course_form", "general")),
            core_thread=str(data.get("core_thread", "")),
            terminology=list(data.get("terminology", [])),
            visual_profile=(
                VisualProfile.from_dict(data.get("visual_profile", {}))
                if isinstance(data.get("visual_profile", {}), dict)
                else VisualProfile()
            ),
            chapters=[ChapterPlan.from_dict(ch) for ch in data.get("chapters", [])],
            side_topics=[SideTopic.from_dict(st) for st in data.get("side_topics", [])],
        )

    @property
    def all_unit_plans(self) -> list[UnitPlan]:
        return [up for ch in self.chapters for up in ch.unit_plans]


# ---------------------------------------------------------------------------
# CourseIR（内部权威合同）
# ---------------------------------------------------------------------------

@dataclass
class SourceBlock:
    source_id: str = ""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    text: str = ""
    segment_ids: list[str] = field(default_factory=list)
    repeat_group_id: str = ""
    canonical_source_id: str = ""
    adds_new_information: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceBlock:
        return cls(
            source_id=str(data.get("source_id", data.get("id", ""))),
            start_seconds=float(data.get("start_seconds", 0.0)),
            end_seconds=float(data.get("end_seconds", 0.0)),
            text=str(data.get("text", "")),
            segment_ids=list(data.get("segment_ids", [])),
            repeat_group_id=str(data.get("repeat_group_id", "")),
            canonical_source_id=str(data.get("canonical_source_id", "")),
            adds_new_information=bool(data.get("adds_new_information", True)),
        )


@dataclass
class Claim:
    claim_id: str = ""
    unit_id: str = ""
    kind: str = "explanation"
    text: str = ""
    source_ids: list[str] = field(default_factory=list)
    origin: str = "audio_backed"
    fingerprint: str = ""
    display_block: str = "paragraph"

    def __post_init__(self) -> None:
        if self.kind not in CLAIM_KINDS:
            self.kind = "explanation"
        if self.origin not in CLAIM_ORIGINS:
            self.origin = "audio_backed"
        if self.display_block not in BLOCK_TYPES:
            self.display_block = "paragraph"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Claim:
        return cls(
            claim_id=str(data.get("claim_id", data.get("id", ""))),
            unit_id=str(data.get("unit_id", "")),
            kind=str(data.get("kind", "explanation")),
            text=str(data.get("text", "")),
            source_ids=list(data.get("source_ids", [])),
            origin=str(data.get("origin", "audio_backed")),
            fingerprint=str(data.get("fingerprint", "")),
            display_block=str(data.get("display_block", "paragraph")),
        )


@dataclass
class CourseUnit:
    unit_id: str = ""
    chapter_id: str = ""
    title: str = ""
    type: str = "concept"
    importance: str = "supporting"
    depth: str = "standard"
    source_ids: list[str] = field(default_factory=list)
    source_segment_ids: list[str] = field(default_factory=list)
    required_facets: list[str] = field(default_factory=list)
    expansion_allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CourseUnit:
        return cls(
            unit_id=str(data.get("unit_id", data.get("id", ""))),
            chapter_id=str(data.get("chapter_id", "")),
            title=str(data.get("title", "")),
            type=str(data.get("type", "concept")),
            importance=str(data.get("importance", "supporting")),
            depth=str(data.get("depth", "standard")),
            source_ids=list(data.get("source_ids", [])),
            source_segment_ids=list(data.get("source_segment_ids", [])),
            required_facets=list(data.get("required_facets", [])),
            expansion_allowed=bool(data.get("expansion_allowed", True)),
        )


@dataclass
class CourseIR:
    schema_version: int = 1
    course: dict[str, Any] = field(default_factory=dict)
    sources: list[SourceBlock] = field(default_factory=list)
    units: list[CourseUnit] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    visuals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "course": dict(self.course),
            "sources": [item.to_dict() for item in self.sources],
            "units": [item.to_dict() for item in self.units],
            "claims": [item.to_dict() for item in self.claims],
            "visuals": [dict(item) for item in self.visuals],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CourseIR:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            course=dict(data.get("course", {})),
            sources=[SourceBlock.from_dict(item) for item in data.get("sources", [])],
            units=[CourseUnit.from_dict(item) for item in data.get("units", [])],
            claims=[Claim.from_dict(item) for item in data.get("claims", [])],
            visuals=[dict(item) for item in data.get("visuals", []) if isinstance(item, dict)],
        )


# ---------------------------------------------------------------------------
# FrameSemantic / VisualBinding / ContentBlock
# ---------------------------------------------------------------------------

@dataclass
class FrameSemantic:
    """候选帧的语义信息。"""

    frame_id: str = ""
    timestamp_seconds: float = 0.0
    path: str = ""
    ocr_text: str = ""
    nearby_transcript: str = ""
    visual_description: str = ""
    visual_type: str = "other"
    semantic_source: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.visual_type not in VISUAL_TYPES:
            self.visual_type = "other"
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrameSemantic:
        return cls(
            frame_id=str(data.get("frame_id", "")),
            timestamp_seconds=float(data.get("timestamp_seconds", 0.0)),
            path=str(data.get("path", "")),
            ocr_text=str(data.get("ocr_text", "")),
            nearby_transcript=str(data.get("nearby_transcript", "")),
            visual_description=str(data.get("visual_description", "")),
            visual_type=str(data.get("visual_type", "other")),
            semantic_source=list(data.get("semantic_source", [])),
            confidence=float(data.get("confidence", 0.0)),
        )


@dataclass
class VisualBinding:
    """图片到知识点的显式绑定。"""

    frame_id: str = ""
    unit_id: str = ""
    relation: str = ""
    target_block_id: str = ""
    reader_focus: str = ""
    confidence: float = 0.0
    basis: list[str] = field(default_factory=list)
    decision: str = "bind"

    def __post_init__(self) -> None:
        if self.decision not in BINDING_DECISIONS:
            self.decision = "bind"
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisualBinding:
        return cls(
            frame_id=str(data.get("frame_id", "")),
            unit_id=str(data.get("unit_id", "")),
            relation=str(data.get("relation", "")),
            target_block_id=str(data.get("target_block_id", "")),
            reader_focus=str(data.get("reader_focus", "")),
            confidence=float(data.get("confidence", 0.0)),
            basis=list(data.get("basis", [])),
            decision=str(data.get("decision", "bind")),
        )


@dataclass
class ContentBlock:
    """知识点内的块级内容。"""

    block_id: str = ""
    type: str = "paragraph"
    origin: str = "source_backed"
    text: str = ""
    items: list[str] = field(default_factory=list)
    binding_id: str = ""
    layout: str = "full_width"
    claim_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.type not in BLOCK_TYPES:
            self.type = "paragraph"
        if self.origin not in ORIGIN_TYPES:
            self.origin = "source_backed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentBlock:
        return cls(
            block_id=str(data.get("block_id", "")),
            type=str(data.get("type", "paragraph")),
            origin=str(data.get("origin", "source_backed")),
            text=str(data.get("text", "")),
            items=list(data.get("items", [])),
            binding_id=str(data.get("binding_id", "")),
            layout=str(data.get("layout", "full_width")),
            claim_ids=list(data.get("claim_ids", [])),
            source_ids=list(data.get("source_ids", [])),
        )


# ---------------------------------------------------------------------------
# KnowledgeUnit（扩展 plan_id / detail_level / facet_status / content_blocks / visual_bindings）
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeUnit:
    """按知识类型专业化整理后的知识单元。"""

    unit_id: str = ""
    type: str = "concept"
    title: str = ""
    importance: str = "supporting"
    definition_or_conclusion: str = ""
    prerequisites: list[str] = field(default_factory=list)
    branches: list[dict[str, Any]] = field(default_factory=list)
    procedure: list[str] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    positive_examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    # V2 新增字段
    plan_id: str = ""
    detail_level: str = ""
    facet_status: dict[str, str] = field(default_factory=dict)
    content_blocks: list[dict[str, Any]] = field(default_factory=list)
    visual_bindings: list[dict[str, Any]] = field(default_factory=list)
    visual_evidence: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.type not in KNOWLEDGE_TYPES:
            self.type = "concept"
        if self.importance not in IMPORTANCE_LEVELS:
            self.importance = "supporting"
        if self.detail_level and self.detail_level not in DETAIL_LEVELS:
            self.detail_level = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeUnit:
        return cls(
            unit_id=str(data.get("unit_id", "")),
            type=str(data.get("type", "concept")),
            title=str(data.get("title", "")),
            importance=str(data.get("importance", "supporting")),
            definition_or_conclusion=str(data.get("definition_or_conclusion", "")),
            prerequisites=list(data.get("prerequisites", [])),
            branches=list(data.get("branches", [])),
            procedure=list(data.get("procedure", [])),
            rules=list(data.get("rules", [])),
            exceptions=list(data.get("exceptions", [])),
            positive_examples=list(data.get("positive_examples", [])),
            negative_examples=list(data.get("negative_examples", [])),
            pitfalls=list(data.get("pitfalls", [])),
            unresolved=list(data.get("unresolved", [])),
            evidence_refs=list(data.get("evidence_refs", [])),
            plan_id=str(data.get("plan_id", "")),
            detail_level=str(data.get("detail_level", "")),
            facet_status=dict(data.get("facet_status", {})),
            content_blocks=list(data.get("content_blocks", [])),
            visual_bindings=list(data.get("visual_bindings", [])),
            visual_evidence=list(data.get("visual_evidence", [])),
        )


# ---------------------------------------------------------------------------
# EvidenceRef
# ---------------------------------------------------------------------------

@dataclass
class EvidenceRef:
    """知识单元的证据引用：segment 和/或 frame。"""

    segment_ids: list[str] = field(default_factory=list)
    frame_ids: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRef:
        return cls(
            segment_ids=list(data.get("segment_ids", [])),
            frame_ids=list(data.get("frame_ids", [])),
            note=str(data.get("note", "")),
        )


# ---------------------------------------------------------------------------
# 批量序列化辅助
# ---------------------------------------------------------------------------

def decisions_to_list(decisions: list[ContentDecision]) -> list[dict[str, Any]]:
    return [d.to_dict() for d in decisions]


def decisions_from_list(data: list[dict[str, Any]]) -> list[ContentDecision]:
    return [ContentDecision.from_dict(item) for item in data]


def units_to_list(units: list[KnowledgeUnit]) -> list[dict[str, Any]]:
    return [u.to_dict() for u in units]


def units_from_list(data: list[dict[str, Any]]) -> list[KnowledgeUnit]:
    return [KnowledgeUnit.from_dict(item) for item in data]


def bindings_to_list(bindings: list[VisualBinding]) -> list[dict[str, Any]]:
    return [b.to_dict() for b in bindings]


def bindings_from_list(data: list[dict[str, Any]]) -> list[VisualBinding]:
    return [VisualBinding.from_dict(item) for item in data]


def frame_semantics_to_list(semantics: list[FrameSemantic]) -> list[dict[str, Any]]:
    return [s.to_dict() for s in semantics]


def frame_semantics_from_list(data: list[dict[str, Any]]) -> list[FrameSemantic]:
    return [FrameSemantic.from_dict(item) for item in data]
