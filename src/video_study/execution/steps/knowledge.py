from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ...knowledge.adapter import units_to_document
from ...knowledge.course_ir import build_course_ir
from ...knowledge.editorial import EditorialBrief, brief_from_text, load_brief
from ...knowledge.organizer import build_units
from ...knowledge.cloud_info import merge_cloud_info
from ...knowledge.planning import build_lesson_plan, collect_visual_jobs
from ...knowledge.schema import FrameSemantic, KnowledgeUnit, LessonPlan, VisualEvidence
from ...knowledge.selfcheck import run_selfcheck
from ...knowledge.visual_retrieval import build_visual_evidence
from ...knowledge.visuals import build_frame_semantics
from ...utils import TaskCancelled
from ..artifacts import (
    CHAPTER_DRAFTS,
    CHAPTER_REPAIRED,
    CHAPTER_VALIDATED,
    DOCUMENT_PLAN,
    DOCUMENT_V3,
    DOCUMENT_VALIDATION,
    FRAMES_CANDIDATES,
    FRAMES_SELECTED,
    FRAMES_SEMANTICS,
    KNOWLEDGE_COURSE_IR,
    KNOWLEDGE_PLAN,
    KNOWLEDGE_SELFCHECK,
    KNOWLEDGE_UNITS,
    SOURCE_MANIFEST,
    TRANSCRIPT_NORMALIZED,
    VISUAL_EVIDENCE,
    VISUAL_JOBS,
    ArtifactId,
    ArtifactRef,
)
from ..context import ProcessingContext
from ..contracts import ExecutionCancelled, FingerprintMaterial, RemoteCost, StepOutcome, StepSpec, StepStatus
from ..decision_policy import LocalDecisionPolicy, VisualNeedLevel
from ..resource_leases import ResourceLeaseManager
from ..task_groups import FileTaskGroupCache


def _read(ref: ArtifactRef) -> dict[str, Any]:
    value = json.loads(ref.path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Artifact 顶层必须是对象：{ref.artifact_id.name}")
    return value


def _input(inputs: Mapping[ArtifactId, ArtifactRef], artifact_id: ArtifactId) -> ArtifactRef:
    try:
        return inputs[artifact_id]
    except KeyError as exc:
        raise ValueError(f"缺少知识域输入：{artifact_id.name}") from exc


def _write(staging: Path, artifact_id: ArtifactId, value: Mapping[str, Any]) -> Path:
    path = staging / artifact_id.relative_paths[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _material(inputs: Mapping[ArtifactId, ArtifactRef], **components: Any) -> FingerprintMaterial:
    return FingerprintMaterial({
        **{f"upstream.{item.name}": ref.digest for item, ref in inputs.items()},
        **components,
    })


def _visual_plan_digest(plan_ref: ArtifactRef | None) -> str:
    """从 plan artifact 中提取视觉相关部分计算专用摘要。

    避免 editorial_decision 等非视觉字段变化导致视觉缓存误判失效。
    """
    import hashlib
    if plan_ref is None or not plan_ref.path.is_file():
        return ""
    payload = json.loads(plan_ref.path.read_text(encoding="utf-8"))
    plan = payload.get("plan", {}) if isinstance(payload, dict) else {}
    visual_parts = {
        "visual_profile": plan.get("visual_profile", {}),
        "visual_questions": [
            {
                "plan_id": up.get("plan_id", ""),
                "needs_visual": up.get("needs_visual", False),
                "visual_need": up.get("visual_need", {}),
                "visual_questions": up.get("visual_questions", []),
            }
            for ch in plan.get("chapters", [])
            for up in ch.get("unit_plans", [])
        ],
    }
    return hashlib.sha256(
        json.dumps(visual_parts, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _settings(context: ProcessingContext) -> dict[str, Any]:
    settings = dict(context.options.knowledge)
    settings["visual_teaching"] = dict(context.options.visual).get("visual_teaching", {})
    settings["visual_evidence"] = dict(context.options.visual).get("visual_evidence", {})
    settings["_config_root"] = str(context.services.port("project_root"))
    return settings


def _brief_path(context: ProcessingContext) -> Path:
    from ...knowledge.editorial import BRIEF_FILENAME
    root = context.services.port("project_root")
    if root is not None:
        return Path(root) / BRIEF_FILENAME
    return Path(BRIEF_FILENAME)


def _brief(context: ProcessingContext) -> EditorialBrief:
    runtime_text = str(context.options.knowledge.get("_runtime_editorial_brief", "") or "").strip()
    if runtime_text:
        return brief_from_text(runtime_text)
    return load_brief(_brief_path(context))


def _cancel(exc: BaseException) -> None:
    if isinstance(exc, (TaskCancelled, ExecutionCancelled)):
        raise ExecutionCancelled(str(exc)) from exc


@dataclass
class KnowledgePlanStep:
    spec = StepSpec(
        "knowledge.plan", 3, dependencies=("transcript.normalize",),
        inputs=(TRANSCRIPT_NORMALIZED,), outputs=(KNOWLEDGE_PLAN,),
        config_keys=("knowledge.content_level", "policy.cloud"), remote_cost=RemoteCost.CLOUD,
        capabilities=("offline", "cloud"), degradation_policy="offline",
        owner="video_study.execution.steps.knowledge", tests=("tests/test_knowledge_planning.py",),
        error_code_prefix="KNOWLEDGE_PLAN", contract_version="lesson-plan-v2",
    )

    def fingerprint(self, context, inputs):
        brief = _brief(context)
        return _material(
            inputs,
            content_level=context.policy.content_level,
            cloud=context.policy.cloud_authorized,
            brief_sha256=brief.sha256,
        )

    def execute(self, context, inputs, staging):
        transcript = _read(_input(inputs, TRANSCRIPT_NORMALIZED))
        brief = _brief(context)
        try:
            plan, cloud_info = build_lesson_plan(
                transcript, context.policy.content_level, _settings(context),
                cloud=context.policy.cloud_authorized,
                cloud_port=context.services.port("cloud") if context.policy.cloud_authorized else None,
                cancel_check=context.services.cancelled,
                event_sink=context.services.event_sink,
                brief=brief,
            )
        except BaseException as exc:
            _cancel(exc); raise
        output = _write(staging, KNOWLEDGE_PLAN, {"version": 1, "plan": plan.to_dict(), "cloud_info": cloud_info, "brief": brief.to_dict()})
        capability = "cloud" if cloud_info.get("model") else "offline"
        status = StepStatus.DEGRADED if context.policy.cloud_authorized and capability == "offline" else StepStatus.SUCCEEDED
        return StepOutcome(self.spec.step_id, context.run_id, status, capability, (ArtifactRef(KNOWLEDGE_PLAN, output),))

    def validate(self, _context, outcome):
        LessonPlan.from_dict(_read(outcome.artifacts[0]).get("plan", {}))


@dataclass
class VisualJobsStep:
    spec = StepSpec(
        "visual.jobs", 2, dependencies=("knowledge.plan", "frames.candidates", "transcript.normalize"),
        inputs=(KNOWLEDGE_PLAN, FRAMES_CANDIDATES, TRANSCRIPT_NORMALIZED), outputs=(VISUAL_JOBS,),
        config_keys=("visual.jobs",), owner="video_study.execution.steps.knowledge",
        tests=("tests/test_visual_retrieval.py",), error_code_prefix="VISUAL_JOBS",
        contract_version="visual-jobs-v1",
    )

    def fingerprint(self, context, inputs):
        plan_ref = inputs.get(KNOWLEDGE_PLAN)
        frames_ref = inputs.get(FRAMES_CANDIDATES)
        transcript_ref = inputs.get(TRANSCRIPT_NORMALIZED)
        return FingerprintMaterial({
            "upstream.frames.candidates": frames_ref.digest if frames_ref else "",
            "upstream.transcript.normalized": transcript_ref.digest if transcript_ref else "",
            "visual_plan": _visual_plan_digest(plan_ref),
            "visual": dict(context.options.visual),
        })

    def execute(self, context, inputs, staging):
        plan = LessonPlan.from_dict(_read(_input(inputs, KNOWLEDGE_PLAN)).get("plan", {}))
        transcript = _read(_input(inputs, TRANSCRIPT_NORMALIZED))
        duration = max((float(row.get("end_seconds", 0)) for row in transcript.get("segments", [])), default=0.0)
        settings = dict(dict(context.options.visual).get("visual_evidence", {}))
        jobs = collect_visual_jobs(plan, duration, settings)
        policy = LocalDecisionPolicy()
        need_levels = {
            unit.plan_id: policy.visual_need(unit).value
            for unit in plan.all_unit_plans if unit.plan_id
        }
        output = _write(staging, VISUAL_JOBS, {
            "version": 2,
            "jobs": [job.to_dict() for job in jobs],
            "need_levels": need_levels,
        })
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(VISUAL_JOBS, output),))

    def validate(self, _context, outcome):
        if not isinstance(_read(outcome.artifacts[0]).get("jobs"), list):
            raise ValueError("Visual jobs 索引无效")


@dataclass
class VisualEvidenceStep:
    spec = StepSpec(
        "visual.evidence", 4, dependencies=("visual.jobs", "knowledge.plan", "frames.candidates", "transcript.normalize"),
        inputs=(VISUAL_JOBS, KNOWLEDGE_PLAN, FRAMES_CANDIDATES, TRANSCRIPT_NORMALIZED), outputs=(VISUAL_EVIDENCE,),
        config_keys=("visual.provider",), remote_cost=RemoteCost.LOCAL_HEAVY,
        degradation_policy="offline",
        owner="video_study.execution.steps.knowledge", tests=("tests/test_visual_retrieval.py", "tests/test_vision_providers.py"),
        error_code_prefix="VISUAL_EVIDENCE", contract_version="visual-evidence-v1",
    )

    def fingerprint(self, context, inputs):
        plan_ref = inputs.get(KNOWLEDGE_PLAN)
        jobs_ref = inputs.get(VISUAL_JOBS)
        frames_ref = inputs.get(FRAMES_CANDIDATES)
        transcript_ref = inputs.get(TRANSCRIPT_NORMALIZED)
        return FingerprintMaterial({
            "upstream.visual.jobs": jobs_ref.digest if jobs_ref else "",
            "upstream.frames.candidates": frames_ref.digest if frames_ref else "",
            "upstream.transcript.normalized": transcript_ref.digest if transcript_ref else "",
            "visual_plan": _visual_plan_digest(plan_ref),
            "visual": dict(context.options.visual),
        })

    def execute(self, context, inputs, staging):
        plan = LessonPlan.from_dict(_read(_input(inputs, KNOWLEDGE_PLAN)).get("plan", {}))
        frames_ref = _input(inputs, FRAMES_CANDIDATES)
        frames = _read(frames_ref)
        candidate_dir = frames_ref.path.parent / "candidates"
        for row in frames.get("candidates", []):
            if not row.get("path") and row.get("file"):
                row["path"] = str((candidate_dir / str(row["file"])).resolve())
        transcript = _read(_input(inputs, TRANSCRIPT_NORMALIZED))
        settings = dict(dict(context.options.visual).get("visual_evidence", {}))
        runtime_state: dict[str, Any] = {}
        degraded_reason = ""
        task_cache = FileTaskGroupCache(
            context.workspace.state_dir,
            f"{self.spec.step_id}.v{self.spec.implementation_version}",
        )

        def provider_factory():
            port = context.services.port("vision")
            session = port.open_session(settings)
            return session.provider, ""

        levels = [LocalDecisionPolicy().visual_need(unit) for unit in plan.all_unit_plans]
        visual_need = (
            VisualNeedLevel.REQUIRED if VisualNeedLevel.REQUIRED in levels else
            VisualNeedLevel.SUPPORTIVE if VisualNeedLevel.SUPPORTIVE in levels else
            VisualNeedLevel.NONE
        )

        def run_visual(provider):
            from ..graphs.visual_graph import VisualGraph
            return VisualGraph().run(
                visual_need,
                execute=lambda: build_visual_evidence(
                    plan, frames, transcript, staging, settings,
                    task_cache=task_cache,
                    provider_factory=provider,
                    cancel_check=context.services.cancelled,
                    event_sink=context.services.event_sink,
                    progress_sink=context.services.progress_sink,
                    runtime_state=runtime_state,
                ),
            )["evidence"]
        try:
            if visual_need == VisualNeedLevel.NONE:
                evidence = run_visual(provider_factory)
            else:
                with ResourceLeaseManager.acquire("gpu", cancel_check=context.services.cancelled):
                    evidence = run_visual(provider_factory)
        except BaseException as exc:
            _cancel(exc)
            degraded_reason = f"{type(exc).__name__}: {exc}"
            context.services.event_sink({
                "type": "runtime", "run_id": context.run_id, "step_id": self.spec.step_id,
                "stage": "visual", "level": "warning", "code": "visual_evidence_offline_fallback",
                "message": f"本地视觉增强未完成，已降级为无视觉模型证据并继续生成文档：{degraded_reason}",
            })
            evidence = run_visual(lambda: (None, degraded_reason))
            runtime_state.update({
                "degraded": True,
                "degradation_reason": degraded_reason,
                "provider_initialized": False,
                "session_started": False,
            })
        output = _write(staging, VISUAL_EVIDENCE, {
            "version": 1, "visual_evidence": [item.to_dict() for item in evidence],
            "runtime": dict(runtime_state),
        })
        status = StepStatus.DEGRADED if degraded_reason else StepStatus.SUCCEEDED
        diagnostics = (
            {"degraded_reason": degraded_reason}
            if status == StepStatus.DEGRADED else {}
        )
        return StepOutcome(
            self.spec.step_id, context.run_id, status,
            artifacts=(ArtifactRef(VISUAL_EVIDENCE, output),), diagnostics=diagnostics,
        )

    def validate(self, _context, outcome):
        if not isinstance(_read(outcome.artifacts[0]).get("visual_evidence"), list):
            raise ValueError("VisualEvidence Artifact 无效")


@dataclass
class FrameSemanticsStep:
    spec = StepSpec(
        "frames.semantics", 1, dependencies=("frames.select", "transcript.normalize"),
        inputs=(FRAMES_SELECTED, TRANSCRIPT_NORMALIZED), outputs=(FRAMES_SEMANTICS,),
        owner="video_study.execution.steps.knowledge", tests=("tests/test_visuals.py",),
        error_code_prefix="FRAME_SEMANTICS", contract_version="frame-semantics-v1",
    )

    def fingerprint(self, _context, inputs): return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        values = build_frame_semantics(
            _read(_input(inputs, FRAMES_SELECTED)), _read(_input(inputs, TRANSCRIPT_NORMALIZED)),
        )
        output = _write(staging, FRAMES_SEMANTICS, {"version": 1, "semantics": [item.to_dict() for item in values]})
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(FRAMES_SEMANTICS, output),))

    def validate(self, _context, outcome):
        if not isinstance(_read(outcome.artifacts[0]).get("semantics"), list): raise ValueError("Frame semantics 无效")


@dataclass
class CourseIRStep:
    spec = StepSpec(
        "knowledge.course_ir", 1, dependencies=("knowledge.plan", "transcript.normalize", "visual.evidence"),
        inputs=(KNOWLEDGE_PLAN, TRANSCRIPT_NORMALIZED, VISUAL_EVIDENCE), outputs=(KNOWLEDGE_COURSE_IR,),
        config_keys=(), owner="video_study.execution.steps.knowledge",
        tests=("tests/test_cloud_payload.py",), error_code_prefix="COURSE_IR", contract_version="course-ir-v1",
    )

    def fingerprint(self, context, inputs):
        return _material(
            inputs, content_level=context.policy.content_level,
        )

    def execute(self, context, inputs, staging):
        plan = LessonPlan.from_dict(_read(_input(inputs, KNOWLEDGE_PLAN)).get("plan", {}))
        evidence = [VisualEvidence.from_dict(row) for row in _read(_input(inputs, VISUAL_EVIDENCE)).get("visual_evidence", [])]
        course_ir = build_course_ir(plan, _read(_input(inputs, TRANSCRIPT_NORMALIZED)), evidence)
        output = _write(staging, KNOWLEDGE_COURSE_IR, course_ir.to_dict())
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(KNOWLEDGE_COURSE_IR, output),))

    def validate(self, _context, outcome): _read(outcome.artifacts[0])


@dataclass
class KnowledgeUnitsStep:
    spec = StepSpec(
        "knowledge.units", 3,
        dependencies=("knowledge.plan", "knowledge.course_ir", "frames.semantics", "visual.evidence", "transcript.normalize"),
        inputs=(KNOWLEDGE_PLAN, KNOWLEDGE_COURSE_IR, FRAMES_SEMANTICS, VISUAL_EVIDENCE, TRANSCRIPT_NORMALIZED),
        outputs=(KNOWLEDGE_UNITS,), config_keys=("knowledge.units",), remote_cost=RemoteCost.CLOUD,
        capabilities=("offline", "cloud"), degradation_policy="offline",
        owner="video_study.execution.steps.knowledge", tests=("tests/test_knowledge_organizer.py", "tests/test_cloud_payload.py"),
        error_code_prefix="KNOWLEDGE_UNITS", contract_version="knowledge-units-v2",
    )

    def fingerprint(self, context, inputs):
        brief = _brief(context)
        return _material(inputs, content_level=context.policy.content_level, cloud=context.policy.cloud_authorized, brief_sha256=brief.sha256)

    def execute(self, context, inputs, staging):
        plan_payload = _read(_input(inputs, KNOWLEDGE_PLAN))
        plan = LessonPlan.from_dict(plan_payload.get("plan", {}))
        semantics = [FrameSemantic.from_dict(row) for row in _read(_input(inputs, FRAMES_SEMANTICS)).get("semantics", [])]
        evidence = [VisualEvidence.from_dict(row) for row in _read(_input(inputs, VISUAL_EVIDENCE)).get("visual_evidence", [])]
        brief = _brief(context)
        degraded_reason = ""
        try:
            units, cloud_info = build_units(
                plan, _read(_input(inputs, TRANSCRIPT_NORMALIZED)), context.policy.content_level,
                _settings(context), cloud=context.policy.cloud_authorized,
                frame_semantics=semantics, visual_evidence=evidence,
                cloud_port=context.services.port("cloud") if context.policy.cloud_authorized else None,
                cancel_check=context.services.cancelled,
                event_sink=context.services.event_sink,
                brief=brief,
            )
        except BaseException as exc:
            _cancel(exc)
            if not context.policy.cloud_authorized:
                raise
            degraded_reason = f"{type(exc).__name__}: {exc}"
            context.services.event_sink({
                "type": "runtime", "run_id": context.run_id, "step_id": self.spec.step_id,
                "stage": "knowledge", "level": "warning", "code": "course_ir_offline_fallback",
                "message": f"云端知识整理未完成，已保留本地 CourseIR 并回退离线：{degraded_reason}",
            })
            units, cloud_info = build_units(
                plan, _read(_input(inputs, TRANSCRIPT_NORMALIZED)), context.policy.content_level,
                _settings(context), cloud=False,
                frame_semantics=semantics, visual_evidence=evidence,
            )
        output = _write(staging, KNOWLEDGE_UNITS, {
            "version": 1, "units": [unit.to_dict() for unit in units], "cloud_info": cloud_info,
        })
        capability = "cloud" if cloud_info.get("model") else "offline"
        status = StepStatus.DEGRADED if context.policy.cloud_authorized and capability == "offline" else StepStatus.SUCCEEDED
        return StepOutcome(
            self.spec.step_id, context.run_id, status, capability, (ArtifactRef(KNOWLEDGE_UNITS, output),),
            diagnostics={"degraded_reason": degraded_reason} if degraded_reason else {},
        )

    def validate(self, _context, outcome):
        if not isinstance(_read(outcome.artifacts[0]).get("units"), list):
            raise ValueError("Knowledge units Artifact 无效")


@dataclass
class KnowledgeSelfcheckStep:
    spec = StepSpec(
        "knowledge.selfcheck", 1,
        dependencies=("knowledge.units", "knowledge.plan", "transcript.normalize", "frames.select"),
        inputs=(KNOWLEDGE_UNITS, KNOWLEDGE_PLAN, TRANSCRIPT_NORMALIZED, FRAMES_SELECTED),
        outputs=(KNOWLEDGE_SELFCHECK,), owner="video_study.execution.steps.knowledge",
        tests=("tests/test_knowledge_selfcheck.py",), error_code_prefix="KNOWLEDGE_SELFCHECK",
        contract_version="selfcheck-v1",
    )

    def fingerprint(self, _context, inputs): return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        units = [KnowledgeUnit.from_dict(row) for row in _read(_input(inputs, KNOWLEDGE_UNITS)).get("units", [])]
        plan = LessonPlan.from_dict(_read(_input(inputs, KNOWLEDGE_PLAN)).get("plan", {}))
        report = run_selfcheck(
            units, plan, _read(_input(inputs, TRANSCRIPT_NORMALIZED)), _read(_input(inputs, FRAMES_SELECTED)), [],
        )
        output = _write(staging, KNOWLEDGE_SELFCHECK, report.to_dict())
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(KNOWLEDGE_SELFCHECK, output),))

    def validate(self, _context, outcome): _read(outcome.artifacts[0])


@dataclass
class DocumentPlanStep:
    spec = StepSpec(
        "document.plan", 2,
        dependencies=("knowledge.units", "knowledge.selfcheck", "visual.evidence", "knowledge.plan", "frames.semantics", "frames.select", "transcript.normalize", "source.probe"),
        inputs=(KNOWLEDGE_UNITS, KNOWLEDGE_SELFCHECK, VISUAL_EVIDENCE, KNOWLEDGE_PLAN, FRAMES_SEMANTICS, FRAMES_SELECTED, TRANSCRIPT_NORMALIZED, SOURCE_MANIFEST),
        outputs=(DOCUMENT_PLAN,), owner="video_study.execution.steps.knowledge",
        capabilities=("offline", "cloud"),
        tests=("tests/test_document_v3.py",), error_code_prefix="DOCUMENT_PLAN", contract_version="document-plan-v2",
    )

    def fingerprint(self, context, inputs):
        return _material(
            inputs,
            source_link_base=dict(context.options.render).get("source_link_base", "video-study://play"),
            content_level=context.policy.content_level,
            version=3,
        )

    def execute(self, context, inputs, staging):
        plan_payload = _read(_input(inputs, KNOWLEDGE_PLAN))
        units_payload = _read(_input(inputs, KNOWLEDGE_UNITS))
        plan = LessonPlan.from_dict(plan_payload.get("plan", {}))
        units = [KnowledgeUnit.from_dict(row) for row in units_payload.get("units", [])]
        evidence_payload = _read(_input(inputs, VISUAL_EVIDENCE))
        evidence = [VisualEvidence.from_dict(row) for row in evidence_payload.get("visual_evidence", [])]
        semantics = [FrameSemantic.from_dict(row) for row in _read(_input(inputs, FRAMES_SEMANTICS)).get("semantics", [])]
        cloud_info = merge_cloud_info(
            dict(plan_payload.get("cloud_info", {})), dict(units_payload.get("cloud_info", {})),
            context.services.cloud_budget if context.policy.cloud_authorized else None,
        ) if context.policy.cloud_authorized else {}
        manifest = _read(_input(inputs, SOURCE_MANIFEST))
        transcript = _read(_input(inputs, TRANSCRIPT_NORMALIZED))
        frames = _read(_input(inputs, FRAMES_SELECTED))
        if units:
            document = units_to_document(
                units, manifest, transcript, frames,
                str(dict(context.options.render).get("source_link_base", "video-study://play")),
                cloud_info=cloud_info, selfcheck_report=_read(_input(inputs, KNOWLEDGE_SELFCHECK)),
                lesson_plan=plan, visual_bindings=[], frame_semantics=semantics,
                visual_evidence=evidence,
            )
        else:
            from ...knowledge.offline_document import build_offline_document
            document = build_offline_document(
                manifest, transcript, frames, dict(context.options.render),
                reason="课程内容不足以形成结构化知识单元；已保留可追溯的离线内容。",
            )
        selected = [item for item in evidence if item.decision == "select"]
        runtime = dict(evidence_payload.get("runtime", {}))
        document["knowledge_pipeline"] = {
            "version": 8, "cloud": context.policy.cloud_authorized,
            "content_level": context.policy.content_level,
            "plan_units": len(plan.all_unit_plans), "unit_count": len(units),
            "visual_evidence": [item.to_dict() for item in evidence],
            "visual_runtime": {
                **runtime, "selected_count": len(selected),
                "question_count": sum(len(unit.visual_questions) for unit in plan.all_unit_plans),
                "gpu_used": bool(runtime.get("session_started")),
            },
            "selfcheck": _read(_input(inputs, KNOWLEDGE_SELFCHECK)),
        }
        from ...document_v3 import build_document_plan
        document_plan = build_document_plan(
            document,
            mode="cloud" if cloud_info.get("model") else "local",
        )
        output = _write(staging, DOCUMENT_PLAN, document_plan)
        capability = "cloud" if cloud_info.get("model") else "offline"
        status = StepStatus.DEGRADED if context.policy.cloud_authorized and capability == "offline" else StepStatus.SUCCEEDED
        return StepOutcome(
            self.spec.step_id, context.run_id, status, capability,
            (ArtifactRef(DOCUMENT_PLAN, output),),
        )

    def validate(self, _context, outcome):
        from ...document_v3 import validate_document_plan
        validate_document_plan(_read(outcome.artifacts[0]))


@dataclass
class ChapterComposeStep:
    spec = StepSpec(
        "chapter.compose", 1, dependencies=("document.plan",),
        inputs=(DOCUMENT_PLAN,), outputs=(CHAPTER_DRAFTS,),
        owner="video_study.execution.steps.knowledge", tests=("tests/test_document_v3.py",),
        error_code_prefix="CHAPTER_COMPOSE", contract_version="chapter-components-v1",
    )

    def fingerprint(self, _context, inputs): return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        from ...document_v3 import compose_chapters
        chapters = compose_chapters(_read(_input(inputs, DOCUMENT_PLAN)))
        output = _write(staging, CHAPTER_DRAFTS, {"version": 1, "chapters": chapters})
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(CHAPTER_DRAFTS, output),))

    def validate(self, _context, outcome): _read(outcome.artifacts[0])


@dataclass
class ChapterValidateStep:
    spec = StepSpec(
        "chapter.validate", 1, dependencies=("chapter.compose",),
        inputs=(CHAPTER_DRAFTS,), outputs=(CHAPTER_VALIDATED,),
        owner="video_study.execution.steps.knowledge", tests=("tests/test_document_v3.py",),
        error_code_prefix="CHAPTER_VALIDATE", contract_version="chapter-validation-v1",
    )

    def fingerprint(self, _context, inputs): return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        from ...document_v3 import validate_chapters
        chapters = _read(_input(inputs, CHAPTER_DRAFTS))["chapters"]
        issues = validate_chapters(chapters)
        output = _write(staging, CHAPTER_VALIDATED, {"version": 1, "chapters": chapters, "issues": issues})
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(CHAPTER_VALIDATED, output),))

    def validate(self, _context, outcome): _read(outcome.artifacts[0])


@dataclass
class ChapterRepairStep:
    spec = StepSpec(
        "chapter.repair", 1, dependencies=("chapter.validate",),
        inputs=(CHAPTER_VALIDATED,), outputs=(CHAPTER_REPAIRED,),
        owner="video_study.execution.steps.knowledge", tests=("tests/test_document_v3.py",),
        error_code_prefix="CHAPTER_REPAIR", contract_version="chapter-repair-v1",
    )

    def fingerprint(self, _context, inputs): return _material(inputs, implementation=1, max_repairs=1)

    def execute(self, context, inputs, staging):
        from ...document_v3 import repair_chapters
        value = _read(_input(inputs, CHAPTER_VALIDATED))
        chapters = repair_chapters(value["chapters"], value["issues"])
        output = _write(staging, CHAPTER_REPAIRED, {"version": 1, "chapters": chapters, "repair_count": 1 if value["issues"] else 0})
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(CHAPTER_REPAIRED, output),))

    def validate(self, _context, outcome): _read(outcome.artifacts[0])


@dataclass
class DocumentCompileStep:
    spec = StepSpec(
        "document.compile", 1, dependencies=("document.plan", "chapter.repair"),
        inputs=(DOCUMENT_PLAN, CHAPTER_REPAIRED), outputs=(DOCUMENT_V3,),
        owner="video_study.execution.steps.knowledge", tests=("tests/test_document_v3.py",),
        error_code_prefix="DOCUMENT_COMPILE", contract_version="document-v3",
    )

    def fingerprint(self, _context, inputs): return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        from ...document_v3 import compile_document_v3
        document = compile_document_v3(
            _read(_input(inputs, DOCUMENT_PLAN)),
            _read(_input(inputs, CHAPTER_REPAIRED))["chapters"],
        )
        output = _write(staging, DOCUMENT_V3, document)
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(DOCUMENT_V3, output),))

    def validate(self, _context, outcome):
        from ...document_v3 import validate_document_v3
        validate_document_v3(_read(outcome.artifacts[0]))


@dataclass
class DocumentValidateStep:
    spec = StepSpec(
        "document.validate", 1, dependencies=("document.compile",),
        inputs=(DOCUMENT_V3,), outputs=(DOCUMENT_VALIDATION,),
        owner="video_study.execution.steps.knowledge", tests=("tests/test_document_v3.py",),
        error_code_prefix="DOCUMENT_VALIDATE", contract_version="document-v3-validation-v1",
    )

    def fingerprint(self, _context, inputs): return _material(inputs, implementation=1)

    def execute(self, context, inputs, staging):
        from ...document_v3 import validate_document_v3
        validate_document_v3(_read(_input(inputs, DOCUMENT_V3)))
        output = _write(staging, DOCUMENT_VALIDATION, {"version": 1, "valid": True, "issues": []})
        return StepOutcome(self.spec.step_id, context.run_id, StepStatus.SUCCEEDED, artifacts=(ArtifactRef(DOCUMENT_VALIDATION, output),))

    def validate(self, _context, outcome):
        if not _read(outcome.artifacts[0]).get("valid"): raise ValueError("Document v3 validation failed")


def build_knowledge_steps():
    """V6.1 生产链：knowledge.units 后进入 v3.1 编辑步骤（CP61-5 切换）。

    旧 document.plan → chapter.compose/validate/repair → document.compile 生产拓扑
    已由 editorial.policy → evidence.reconcile → document.blueprint → document.write
    → document.assemble → document.validate 替换；旧步骤代码保留为历史只读。
    """
    from .editorial_steps import build_editorial_steps
    return (
        KnowledgePlanStep(), VisualJobsStep(), VisualEvidenceStep(), FrameSemanticsStep(),
        CourseIRStep(), KnowledgeUnitsStep(), KnowledgeSelfcheckStep(),
        *build_editorial_steps(),
    )
