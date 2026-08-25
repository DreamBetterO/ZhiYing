"""V6.1 生产文档编辑步骤（CP61-5 切换后的唯一 active 文档链）。

knowledge.units 之后：editorial.policy → evidence.reconcile → document.blueprint
→ document.write → document.assemble → document.validate → render(v3.1 原生)。
本地确定性为默认完成路径；云授权后 blueprint/write 进入 EditorialAgentSubgraph
（tool_native / structured_only）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from ...knowledge.editorial import EditorialBrief
from ...knowledge.schema import LessonPlan
from ..artifacts import (
    CHAPTER_V31,
    DOCUMENT_BLUEPRINT,
    DOCUMENT_V3,
    DOCUMENT_VALIDATION,
    EDITORIAL_POLICY,
    EDITORIAL_SESSION,
    EVIDENCE_CORRECTIONS,
    KNOWLEDGE_PLAN,
    KNOWLEDGE_SELFCHECK,
    KNOWLEDGE_UNITS,
    SOURCE_MANIFEST,
    TRANSCRIPT_NORMALIZED,
    VISUAL_EVIDENCE,
    ArtifactId,
    ArtifactRef,
)
from ..context import ProcessingContext
from ..contracts import FingerprintMaterial, StepOutcome, StepSpec, StepStatus
from .knowledge import _brief, _input, _material, _read, _write


def run_editorial_session(
    context,
    *,
    plan: Mapping[str, Any],
    units: list[dict[str, Any]],
    overlay: Mapping[str, Any],
    visual_evidence: list[dict[str, Any]],
    policy: Mapping[str, Any],
    transcript: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """在生产边界运行受控 EditorialAgentSubgraph；离线时不访问 CloudPort。"""
    from ...documents.v3 import CAPABILITY_MANIFEST
    from ...editorial.agent import _initial_state, build_editorial_agent
    from ...editorial.tools import EditorialToolContext
    from ..tool_calling import (
        CAPABILITY_LOCAL,
        CAPABILITY_STRUCTURED_ONLY,
        CAPABILITY_TOOL_NATIVE,
        ModelCapabilityRegistry,
        build_stage_budget,
    )

    lesson_plan = LessonPlan.from_dict(dict(plan))
    known_unit_ids = {
        unit.plan_id for chapter in lesson_plan.chapters for unit in chapter.unit_plans
    }
    capability = CAPABILITY_LOCAL
    tool_port = None
    json_port = None
    if context.policy.cloud_authorized:
        models = tuple(getattr(context.services.credentials, "models", ()) or ())
        configured = dict(context.options.knowledge.get("model_capabilities", {}) or {})
        registry = ModelCapabilityRegistry.from_dict(configured)
        capability = registry.capability(models[0] if models else "")
        if capability == CAPABILITY_TOOL_NATIVE:
            tool_port = context.services.port("cloud_tool")
            # tool_native 失败时子图仍可进入 structured_only。
            json_port = context.services.port("cloud")
        else:
            capability = CAPABILITY_STRUCTURED_ONLY
            json_port = context.services.port("cloud")

    segments = {
        str(row.get("segment_id", "")): row for row in transcript.get("segments", [])
    }
    evidence_index: dict[str, list[dict[str, Any]]] = {}
    visual_facts: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        unit_id = str(unit.get("plan_id") or unit.get("unit_id") or "")
        ids = {
            str(segment_id)
            for ref in unit.get("evidence_refs", [])
            for segment_id in ref.get("segment_ids", [])
        }
        refs = unit.get("source_refs", {})
        ids.update(str(item) for item in refs.get("segment_ids", []))
        evidence_index[unit_id] = [
            {
                "segment_id": segment_id,
                "timestamp": float(segments[segment_id].get("start_seconds", 0) or 0),
                "text": str(segments[segment_id].get("text", "")),
                "confidence": float(segments[segment_id].get("confidence", 1.0) or 1.0),
                "correction_status": "overlay_available",
            }
            for segment_id in ids if segment_id in segments
        ]
    for row in visual_evidence:
        unit_id = str(row.get("primary_unit_id") or row.get("matched_knowledge_id") or "")
        visual_facts.setdefault(unit_id, []).append({
            "visual_id": str(row.get("evidence_id", "")),
            "source_timestamp": float(row.get("source_timestamp", 0) or 0),
            "visible_objects": list(row.get("visible_evidence", [])),
            "visible_text": str(row.get("ocr_text", "")),
            "formula_candidates": [],
            "confidence": float(row.get("confidence", 0) or 0),
            "purpose": str(row.get("visual_role", "explain")),
        })
    tools_ctx = EditorialToolContext(
        evidence_index=evidence_index,
        visual_facts=visual_facts,
        renderer_capabilities=dict(CAPABILITY_MANIFEST),
    )
    budget_cfg = dict(context.options.knowledge.get("editorial_budget", {}) or {})
    allowed_budget_keys = {
        "max_total_calls", "max_total_input_tokens", "max_total_output_tokens",
        "max_stage_calls", "max_stage_tokens", "max_tool_turns",
        "max_tool_result_chars", "max_batch_units", "repair_reserve",
    }
    budget = build_stage_budget(**{
        key: int(value) for key, value in budget_cfg.items() if key in allowed_budget_keys
    })
    graph = build_editorial_agent(
        capability=capability,
        tool_port=tool_port,
        json_port=json_port,
        tools_ctx=tools_ctx,
        known_unit_ids=known_unit_ids,
        budget=budget,
        max_tool_turns=budget.max_tool_turns,
        cancel_check=context.services.cancelled,
    )
    duration = float(manifest.get("duration_seconds", 0) or 0)
    state = _initial_state(
        capability=capability,
        policy=dict(policy),
        plan=lesson_plan.to_dict(),
        plan_units=units,
        evidence_overlay=dict(overlay),
        visual_evidence=visual_evidence,
        metadata={
            "video_id": context.source.video_id,
            "document_title": context.source.display_title or context.source.video_id,
            "title": context.source.display_title or context.source.video_id,
            "duration_label": f"{int(duration) // 3600:02d}:{int(duration) % 3600 // 60:02d}:{int(duration) % 60:02d}",
            "source_video": str(manifest.get("filename", "")),
        },
        transcript_digest=str(overlay.get("transcript_digest", "")),
    )
    if capability == CAPABILITY_TOOL_NATIVE:
        from langchain_core.messages import HumanMessage, SystemMessage
        state["messages"] = [
            SystemMessage(content=(
                "你是受控 EditorialAgent。先规划 DocumentBlueprint v2；可按需调用只读工具，"
                "最终必须调用 submit_blueprint。禁止请求文件、Shell、网络或图片数据。"
            )),
            HumanMessage(content=json.dumps({
                "policy": dict(policy),
                "chapters": [
                    {
                        "chapter_id": chapter.chapter_id, "title": chapter.title,
                        "unit_ids": [unit.plan_id for unit in chapter.unit_plans],
                    }
                    for chapter in lesson_plan.chapters
                ],
            }, ensure_ascii=False, separators=(",", ":"))),
        ]
    result = dict(graph.invoke(state))
    result["requested_capability"] = capability
    result["capability"] = str(result.get("effective_capability", capability))
    result["budget"] = budget.snapshot()
    return result


def _enrich_units_with_sources(context, plan: LessonPlan, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把真实 KnowledgeUnit.evidence_refs 投影为 Writer 使用的稳定来源合同。"""
    enriched = [dict(unit) for unit in units]
    plan_units = {
        unit.plan_id: unit for chapter in plan.chapters for unit in chapter.unit_plans
    }
    for unit in enriched:
        plan_unit = plan_units.get(str(unit.get("plan_id", "")))
        if plan_unit is None:
            continue
        spans = list(plan_unit.evidence_spans)
        segment_ids = [
            segment_id for span in spans for segment_id in span.segment_ids
        ] or [
            segment_id for ref in unit.get("evidence_refs", [])
            for segment_id in ref.get("segment_ids", [])
        ]
        start = min((span.start_seconds for span in spans), default=0.0)
        end = max((span.end_seconds for span in spans), default=start)
        unit["source_refs"] = {
            "segment_ids": segment_ids, "start_seconds": start,
            "end_seconds": end, "label": f"{int(start) // 60:02d}:{int(start) % 60:02d}",
            "url": f"video-study://play/{quote(context.source.video_id, safe='')}?t={int(start)}",
        }
        unit["source_links"] = [{
            "label": unit["source_refs"]["label"], "url": unit["source_refs"]["url"],
        }]
    return enriched


@dataclass
class EditorialPolicyStep:
    """intent.compile：把用户编辑意图编译为 EditorialPolicy v1。"""

    spec = StepSpec(
        "editorial.policy", 1,
        dependencies=(), inputs=(), outputs=(EDITORIAL_POLICY,),
        owner="zhiying.execution.steps.editorial_steps",
        tests=("tests/test_v61_editorial_policy.py",),
        error_code_prefix="EDITORIAL_POLICY", contract_version="editorial-policy-v1",
        capabilities=("offline",),
    )

    def fingerprint(self, context, inputs):
        brief = _brief(context)
        return FingerprintMaterial({
            "brief_sha256": brief.sha256,
            "brief_is_default": brief.is_default,
            "implementation": 1,
        })

    def execute(self, context, inputs, staging):
        from ...editorial.intent import compile_editorial_policy
        policy = compile_editorial_policy(_brief(context))
        output = _write(staging, EDITORIAL_POLICY, policy.to_dict())
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(EDITORIAL_POLICY, output),))

    def validate(self, _context, outcome):
        _read(outcome.artifacts[0])


@dataclass
class EvidenceReconcileStep:
    """evidence.reconcile：不可变 transcript 上的纠错覆盖层（本地规则）。"""

    spec = StepSpec(
        "evidence.reconcile", 1,
        dependencies=("transcript.normalize",), inputs=(TRANSCRIPT_NORMALIZED,),
        outputs=(EVIDENCE_CORRECTIONS,),
        owner="zhiying.execution.steps.editorial_steps",
        tests=("tests/test_v61_evidence_overlay.py",),
        error_code_prefix="EVIDENCE_RECONCILE", contract_version="evidence-overlay-v1",
        capabilities=("offline",),
    )

    def fingerprint(self, _context, inputs):
        return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        from ...editorial.evidence import build_evidence_overlay
        transcript = _read(_input(inputs, TRANSCRIPT_NORMALIZED))
        overlay = build_evidence_overlay(transcript)
        output = _write(staging, EVIDENCE_CORRECTIONS, overlay.to_dict())
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(EVIDENCE_CORRECTIONS, output),))

    def validate(self, _context, outcome):
        _read(outcome.artifacts[0])


@dataclass
class DocumentBlueprintStep:
    """blueprint：EditorialAgentSubgraph 入口（当前默认 local_deterministic）。

    云授权后经 EditorialAgentSubgraph 的 tool_native/structured 路径生成；
    本实现保持同一 DocumentBlueprint v2 输出合同。
    """

    spec = StepSpec(
        "document.blueprint", 4,
        dependencies=(
            "knowledge.plan", "knowledge.selfcheck", "knowledge.units", "visual.evidence",
            "editorial.policy", "evidence.reconcile", "transcript.normalize", "source.probe",
        ),
        inputs=(
            KNOWLEDGE_PLAN, KNOWLEDGE_SELFCHECK, KNOWLEDGE_UNITS, VISUAL_EVIDENCE,
            EDITORIAL_POLICY, EVIDENCE_CORRECTIONS, TRANSCRIPT_NORMALIZED, SOURCE_MANIFEST,
        ),
        outputs=(DOCUMENT_BLUEPRINT, EDITORIAL_SESSION),
        owner="zhiying.execution.steps.editorial_steps",
        tests=("tests/test_v61_local_editor.py", "tests/test_v61_editorial_graph.py"),
        error_code_prefix="DOCUMENT_BLUEPRINT", contract_version="document-blueprint-v2",
        capabilities=("offline", "cloud"), degradation_policy="offline",
    )

    def fingerprint(self, context, inputs):
        brief = _brief(context)
        return _material(inputs, content_level=context.policy.content_level, brief_sha256=brief.sha256, implementation=4)

    def execute(self, context, inputs, staging):
        from ...editorial.blueprint import validate_blueprint
        from ...editorial.policy import EditorialPolicy
        plan = LessonPlan.from_dict(_read(_input(inputs, KNOWLEDGE_PLAN)).get("plan", {}))
        policy = EditorialPolicy.from_dict(_read(_input(inputs, EDITORIAL_POLICY)))
        units = _enrich_units_with_sources(
            context, plan, list(_read(_input(inputs, KNOWLEDGE_UNITS)).get("units", [])),
        )
        session = run_editorial_session(
            context,
            plan=plan.to_dict(),
            units=units,
            overlay=_read(_input(inputs, EVIDENCE_CORRECTIONS)),
            visual_evidence=list(_read(_input(inputs, VISUAL_EVIDENCE)).get("visual_evidence", [])),
            policy=policy.to_dict(),
            transcript=_read(_input(inputs, TRANSCRIPT_NORMALIZED)),
            manifest=_read(_input(inputs, SOURCE_MANIFEST)),
        )
        from ...editorial.blueprint import DocumentBlueprint
        blueprint = DocumentBlueprint.from_dict(session["accepted_blueprint"])
        known_unit_ids = {unit.plan_id for chapter in plan.chapters for unit in chapter.unit_plans}
        validate_blueprint(blueprint, known_unit_ids=known_unit_ids)
        output = _write(staging, DOCUMENT_BLUEPRINT, blueprint.to_dict())
        session_output = _write(staging, EDITORIAL_SESSION, {
            key: session.get(key) for key in (
                "capability", "requested_capability", "effective_capability",
                "terminal_status", "degradation_reasons", "accepted_blueprint", "document_candidate",
                "error_codes",
                "provenance", "model_chain", "usage", "page_report", "quality_report", "budget", "tool_turns",
                "revision_cycles_used", "document_revision",
            )
        })
        status = StepStatus.SUCCEEDED if session.get("terminal_status") == "succeeded" else StepStatus.DEGRADED
        return StepOutcome(
            self.spec.step_id, context.run_id, status, capability=session["capability"],
            artifacts=(ArtifactRef(DOCUMENT_BLUEPRINT, output), ArtifactRef(EDITORIAL_SESSION, session_output)),
            diagnostics={
                "editorial_capability": session["capability"],
                "tool_turns": int(session.get("tool_turns", 0) or 0),
                "budget": dict(session.get("budget", {})),
            },
        )

    def validate(self, _context, outcome):
        from ...editorial.blueprint import DocumentBlueprint, validate_blueprint
        blueprint = DocumentBlueprint.from_dict(_read(outcome.artifacts[0]))
        # 步骤级校验只做结构自洽（unit 引用存在性在 execute 内以真实 plan 校验）
        self_known = {ref for chapter in blueprint.chapters for ref in chapter.unit_refs}
        validate_blueprint(blueprint, known_unit_ids=self_known)


@dataclass
class DocumentWriteStep:
    """chapter.write：按 Blueprint 强制结构化写作（分批 + 章指纹缓存）。"""

    spec = StepSpec(
        "document.write", 3,
        dependencies=("document.blueprint", "knowledge.plan", "knowledge.units", "evidence.reconcile", "visual.evidence"),
        inputs=(DOCUMENT_BLUEPRINT, EDITORIAL_SESSION, KNOWLEDGE_PLAN, KNOWLEDGE_UNITS, EVIDENCE_CORRECTIONS, VISUAL_EVIDENCE),
        outputs=(CHAPTER_V31,),
        owner="zhiying.execution.steps.editorial_steps",
        tests=("tests/test_v61_local_editor.py", "tests/test_v61_editorial_graph.py"),
        error_code_prefix="DOCUMENT_WRITE", contract_version="chapter-components-v3.1",
        capabilities=("offline", "cloud"), degradation_policy="offline",
    )

    def fingerprint(self, context, inputs):
        return _material(inputs, implementation=2)

    def execute(self, context, inputs, staging):
        from ...editorial.blueprint import DocumentBlueprint
        from ...editorial.evidence import EvidenceCorrectionOverlay
        from ...editorial.writer import write_chapters_in_batches
        blueprint = DocumentBlueprint.from_dict(_read(_input(inputs, DOCUMENT_BLUEPRINT)))
        units = list(_read(_input(inputs, KNOWLEDGE_UNITS)).get("units", []))
        plan = LessonPlan.from_dict(_read(_input(inputs, KNOWLEDGE_PLAN)).get("plan", {}))
        units = _enrich_units_with_sources(context, plan, units)
        overlay = EvidenceCorrectionOverlay.from_dict(_read(_input(inputs, EVIDENCE_CORRECTIONS)))
        evidence = _read(_input(inputs, VISUAL_EVIDENCE)).get("visual_evidence", [])
        session = _read(_input(inputs, EDITORIAL_SESSION))
        candidate = session.get("document_candidate", {})
        chapters = list(candidate.get("components", []))
        if not chapters:
            chapters, fingerprints = write_chapters_in_batches(
                blueprint, units, overlay, evidence, max_batch_units=6,
            )
        else:
            fingerprints = {}
        output = _write(staging, CHAPTER_V31, {
            "version": 1, "contract_version": "document-v3.1",
            "chapters": chapters, "fingerprints": fingerprints,
            "provenance": dict(session.get("provenance", {})),
            "editorial_capability": str(session.get("capability", "local_deterministic")),
            "editorial_terminal_status": str(session.get("terminal_status", "degraded")),
        })
        status = StepStatus.SUCCEEDED if session.get("terminal_status") == "succeeded" else StepStatus.DEGRADED
        return StepOutcome(
            self.spec.step_id, context.run_id, status,
            capability=str(session.get("capability", "local_deterministic")),
            artifacts=(ArtifactRef(CHAPTER_V31, output),),
        )

    def validate(self, _context, outcome):
        _read(outcome.artifacts[0])


@dataclass
class DocumentAssembleStep:
    """document.compile（v3.1 版本）：组装 Document v3.1（组件树为唯一权威）。"""

    spec = StepSpec(
        "document.assemble", 2,
        dependencies=("document.write", "source.probe", "transcript.normalize"),
        inputs=(CHAPTER_V31, SOURCE_MANIFEST, TRANSCRIPT_NORMALIZED),
        outputs=(DOCUMENT_V3,),
        owner="zhiying.execution.steps.editorial_steps",
        tests=("tests/test_v61_local_editor.py",),
        error_code_prefix="DOCUMENT_ASSEMBLE", contract_version="document-v3.1",
    )

    def fingerprint(self, _context, inputs):
        return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        from ...editorial.document import build_v31_document
        chapter_payload = _read(_input(inputs, CHAPTER_V31))
        chapters = chapter_payload.get("chapters", [])
        manifest = _read(_input(inputs, SOURCE_MANIFEST))
        transcript = _read(_input(inputs, TRANSCRIPT_NORMALIZED))
        has_points = any(
            child.get("semantic_role") == "knowledge_point"
            for chapter in chapters for child in chapter.get("children", [])
        )
        fallback_used = False
        if not has_points and transcript.get("segments"):
            fallback_used = True
            segment = transcript["segments"][0]
            start = float(segment.get("start_seconds", 0) or 0)
            end = float(segment.get("end_seconds", start) or start)
            refs = {
                "segment_ids": [str(segment.get("segment_id", ""))],
                "start_seconds": start, "end_seconds": end,
                "label": f"{int(start) // 60:02d}:{int(start) % 60:02d}",
                "url": f"video-study://play/{quote(context.source.video_id, safe='')}?t={int(start)}",
            }
            point = {
                "type": "container", "component_id": "fallback.point.001",
                "semantic_role": "knowledge_point", "title": "课程片段",
                "children": [
                    {"type": "paragraph", "component_id": "fallback.point.001.body", "semantic_role": "paragraph", "text": str(segment.get("text", "")), "source_refs": refs, "origin": "local_deterministic", "confidence": 0.4},
                    {"type": "source_reference", "component_id": "fallback.point.001.src", "semantic_role": "source_reference", "source_refs": refs, "links": [{"label": refs["label"], "url": refs["url"]}], "origin": "local_deterministic", "confidence": 1.0},
                ],
                "source_refs": refs, "origin": "local_deterministic", "confidence": 0.4,
            }
            if chapters:
                chapters[0].setdefault("children", []).append(point)
            else:
                chapters = [{
                    "type": "container", "component_id": "fallback.chapter.001",
                    "semantic_role": "chapter", "title": "课程内容",
                    "children": [point], "source_refs": {},
                    "origin": "local_deterministic", "confidence": 0.4,
                }]
        duration = float(manifest.get("duration_seconds", 0.0) or 0.0)
        hours, remainder = divmod(int(duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        document = build_v31_document(
            metadata={
                "video_id": context.source.video_id,
                "document_title": context.source.display_title or context.source.video_id,
                "title": context.source.display_title or context.source.video_id,
                "duration_label": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
                "source_video": str(manifest.get("filename", "")),
            },
            components=chapters,
            provenance=dict(chapter_payload.get("provenance", {})) or {
                "intent": "local_deterministic",
                "evidence_reconcile": "local_rules",
                "blueprint": "local_deterministic",
                "chapter_writing": {
                    str(chapter.get("component_id", "")): "local_deterministic" for chapter in chapters
                },
                "page_audit": "local_rules",
                "final_repair": "local_deterministic",
            },
        )
        output = _write(staging, DOCUMENT_V3, document)
        return StepOutcome(
            self.spec.step_id, context.run_id,
            StepStatus.DEGRADED if fallback_used else StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(DOCUMENT_V3, output),),
            diagnostics={"degraded_reason": "empty_plan_transcript_fallback"} if fallback_used else {},
        )

    def validate(self, _context, outcome):
        from ...editorial.document import validate_document_v31
        validate_document_v31(_read(outcome.artifacts[0]))


@dataclass
class DocumentValidateStep:
    """document.validate（v3.1）：合同校验 + QualityReport v2（含意图门）。"""

    spec = StepSpec(
        "document.validate", 3,
        dependencies=("document.assemble", "editorial.policy"),
        inputs=(DOCUMENT_V3, EDITORIAL_POLICY),
        outputs=(DOCUMENT_VALIDATION,),
        owner="zhiying.execution.steps.editorial_steps",
        tests=("tests/test_v61_quality.py", "tests/test_v61_local_editor.py"),
        error_code_prefix="DOCUMENT_VALIDATE", contract_version="document-v3.1-validation-v1",
    )

    def fingerprint(self, _context, inputs):
        return _material(inputs, implementation=3)

    def execute(self, context, inputs, staging):
        from ...editorial.document import validate_document_v31
        from ...editorial.policy import EditorialPolicy
        from ...editorial.quality import audit_document_v31
        document = _read(_input(inputs, DOCUMENT_V3))
        validate_document_v31(document)
        policy = EditorialPolicy.from_dict(_read(_input(inputs, EDITORIAL_POLICY)))
        report = audit_document_v31(document, policy=policy)
        valid = report["status"] == "valid"
        output = _write(staging, DOCUMENT_VALIDATION, {
            "version": 2, "contract_version": "document-v3.1", "valid": valid,
            "issues": report["issues"], "quality_report": report,
        })
        status = StepStatus.SUCCEEDED if valid else StepStatus.DEGRADED
        return StepOutcome(
            self.spec.step_id, context.run_id, status, artifacts=(ArtifactRef(DOCUMENT_VALIDATION, output),),
            diagnostics={"quality_status": report["status"]},
        )

    def validate(self, _context, outcome):
        # 质量门结果记录在 payload（quality_report）；产物解析成功即通过步骤校验，
        # 质量不通过由终态聚合为 degraded，而不是把步骤标记 failed。
        value = _read(outcome.artifacts[0])
        if not isinstance(value.get("quality_report"), dict):
            raise ValueError("Document v3.1 validation Artifact 缺少 quality_report")


def build_editorial_steps():
    return (
        EditorialPolicyStep(), EvidenceReconcileStep(), DocumentBlueprintStep(),
        DocumentWriteStep(), DocumentAssembleStep(), DocumentValidateStep(),
    )
