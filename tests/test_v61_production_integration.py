from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from zhiying.execution.artifacts import ArtifactId, ArtifactRef, DOCUMENT_V3
from zhiying.execution.contracts import StepOutcome, StepStatus
from zhiying.execution.steps.coarse import RenderVerifyStep, build_coarse_steps


class _Ports:
    def __init__(self, values):
        self.values = values
        self.calls = []
        self.credentials = SimpleNamespace(models=("model-a",))

    def port(self, name):
        self.calls.append(name)
        value = self.values[name]
        return value() if callable(value) else value

    @staticmethod
    def cancelled():
        return False


class _JsonPort:
    def __init__(self, blueprint):
        self.blueprint = blueprint
        self.calls = 0
        self.writer_payloads = []

    def request_json(self, payload, *, validator, stage, **_kwargs):
        self.calls += 1
        if stage == "writer":
            self.writer_payloads.append(payload)
            return validator({"chapters": payload["draft_chapters"]})
        return validator(self.blueprint)


class _ToolPort:
    def __init__(self, blueprint):
        self.blueprint = blueprint
        self.calls = 0

    def invoke_turn(self, **_kwargs):
        from zhiying.execution.tool_calling import ToolCallRecord, ToolTurn
        self.calls += 1
        return ToolTurn(tool_calls=(ToolCallRecord(
            name="submit_blueprint", args={"blueprint": self.blueprint},
            tool_call_id=f"call-{self.calls}",
        ),), model="model-a", finish_reason="tool_calls")


class _FailJsonPort:
    calls = 0

    def request_json(self, *_args, **_kwargs):
        self.calls += 1
        raise RuntimeError("replay unavailable")


class _FailToolPort:
    calls = 0

    def invoke_turn(self, **_kwargs):
        self.calls += 1
        raise TimeoutError("replay tool timeout")


class _WriterFailJsonPort(_JsonPort):
    def request_json(self, payload, *, validator, stage, **kwargs):
        if stage == "writer":
            self.calls += 1
            raise TimeoutError("replay writer timeout")
        return super().request_json(payload, validator=validator, stage=stage, **kwargs)


class V61ProductionIntegrationTests(unittest.TestCase):
    @staticmethod
    def _editorial_inputs():
        from zhiying.editorial.intent import compile_editorial_policy
        from zhiying.editorial.local import build_local_blueprint
        from zhiying.knowledge.editorial import brief_from_text
        from zhiying.knowledge.schema import ChapterPlan, LessonPlan, UnitPlan

        plan = LessonPlan(chapters=[ChapterPlan(
            chapter_id="chapter_001", title="定义",
            unit_plans=[UnitPlan(plan_id="plan_001", title="定义", knowledge_types=["concept"])],
        )])
        policy = compile_editorial_policy(brief_from_text("不要内容导览")).to_dict()
        blueprint = build_local_blueprint(
            plan, compile_editorial_policy(brief_from_text("不要内容导览")),
        ).to_dict()
        return {
            "plan": plan.to_dict(), "units": [{
                "unit_id": "unit_001", "plan_id": "plan_001", "type": "concept",
                "title": "定义", "definition_or_conclusion": "定义正文",
                "source_refs": {"segment_ids": ["seg_1"]},
            }],
            "overlay": {"version": 1, "transcript_digest": "sha256:test", "corrections": []},
            "visual_evidence": [], "policy": policy,
            "transcript": {"segments": [{"segment_id": "seg_1", "text": "定义正文"}]},
            "manifest": {"duration_seconds": 120}, "blueprint": blueprint,
        }

    def test_production_editorial_session_offline_constructs_no_cloud_port(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        ports = _Ports({})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=False), services=ports,
            options=SimpleNamespace(knowledge={}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )
        result = run_editorial_session(context, **{key: value for key, value in data.items() if key != "blueprint"})
        self.assertEqual(result["capability"], "local_deterministic")
        self.assertEqual(result["document_candidate"]["contract_version"], "document-v3.1")
        self.assertEqual(ports.calls, [])

    def test_production_editorial_session_structured_uses_json_port_only(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        json_port = _JsonPort(data["blueprint"])
        ports = _Ports({"cloud": json_port})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=True), services=ports,
            options=SimpleNamespace(knowledge={}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )
        result = run_editorial_session(context, **{key: value for key, value in data.items() if key != "blueprint"})
        self.assertEqual(result["capability"], "structured_only")
        self.assertEqual(result["provenance"]["blueprint"], "structured_only")
        self.assertEqual(ports.calls, ["cloud"])
        self.assertEqual(json_port.calls, 2)

    def test_short_video_uses_cloud_blueprint_without_redundant_cloud_writer(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        data["manifest"]["duration_seconds"] = 33
        json_port = _JsonPort(data["blueprint"])
        ports = _Ports({"cloud": json_port})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=True), services=ports,
            options=SimpleNamespace(knowledge={}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )

        result = run_editorial_session(
            context, **{key: value for key, value in data.items() if key != "blueprint"},
        )

        self.assertEqual(result["terminal_status"], "succeeded")
        self.assertEqual(json_port.calls, 1)
        self.assertEqual(json_port.writer_payloads, [])
        self.assertEqual(result["provenance"]["writer_strategy"], "short_video_local")

    def test_structured_writer_sends_each_chapter_with_dynamic_output_budget(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        data["plan"]["chapters"].append({
            "chapter_id": "chapter_002", "title": "性质", "source_segment_ids": ["seg_2"],
            "unit_plans": [{
                "plan_id": "plan_002", "title": "性质", "role": "core",
                "knowledge_types": ["concept"],
            }],
        })
        data["units"].append({
            "unit_id": "unit_002", "plan_id": "plan_002", "type": "concept",
            "title": "性质", "definition_or_conclusion": "性质正文",
            "source_refs": {"segment_ids": ["seg_2"]},
        })
        data["blueprint"]["chapters"].append({
            "chapter_id": "chapter_002", "title": "性质", "mode": "concept",
            "unit_refs": ["plan_002"], "component_intents": ["definition"],
            "layout_hint": "full_width", "depth": "standard", "target_chars": 360,
        })
        json_port = _JsonPort(data["blueprint"])
        ports = _Ports({"cloud": json_port})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=True), services=ports,
            options=SimpleNamespace(knowledge={}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )

        result = run_editorial_session(
            context, **{key: value for key, value in data.items() if key != "blueprint"},
        )

        self.assertEqual(result["terminal_status"], "succeeded")
        self.assertEqual(json_port.calls, 3)
        self.assertEqual([len(item["draft_chapters"]) for item in json_port.writer_payloads], [1, 1])
        self.assertTrue(all(item["omit_max_tokens"] for item in json_port.writer_payloads))

    def test_production_editorial_session_tool_native_uses_restricted_tool_port(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        tool_port = _ToolPort(data["blueprint"])
        json_port = _JsonPort(data["blueprint"])
        ports = _Ports({"cloud_tool": tool_port, "cloud": json_port})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=True), services=ports,
            options=SimpleNamespace(knowledge={"model_capabilities": {"model-a": "tool_native"}}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )
        result = run_editorial_session(context, **{key: value for key, value in data.items() if key != "blueprint"})
        self.assertEqual(result["capability"], "tool_native")
        self.assertEqual(result["provenance"]["blueprint"], "tool_native")
        self.assertEqual(set(result["provenance"]["chapter_writing"].values()), {"structured_only"})
        self.assertEqual(tool_port.calls, 1)
        self.assertEqual(json_port.calls, 1)
        self.assertEqual(ports.calls, ["cloud_tool", "cloud"])

    def test_production_editorial_session_cloud_failure_records_local_degradation(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        json_port = _FailJsonPort()
        ports = _Ports({"cloud": json_port})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=True), services=ports,
            options=SimpleNamespace(knowledge={}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )
        result = run_editorial_session(context, **{key: value for key, value in data.items() if key != "blueprint"})
        self.assertEqual(result["requested_capability"], "structured_only")
        self.assertEqual(result["capability"], "local_deterministic")
        self.assertEqual(result["terminal_status"], "degraded")
        self.assertEqual(result["provenance"]["blueprint"], "local_deterministic")
        self.assertTrue(result["degradation_reasons"])

    def test_tool_native_failure_falls_back_without_unbounded_retry(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        tool_port = _FailToolPort()
        json_port = _JsonPort(data["blueprint"])
        ports = _Ports({"cloud_tool": tool_port, "cloud": json_port})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=True), services=ports,
            options=SimpleNamespace(knowledge={"model_capabilities": {"model-a": "tool_native"}}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )
        result = run_editorial_session(context, **{key: value for key, value in data.items() if key != "blueprint"})
        self.assertEqual(tool_port.calls, 1)
        self.assertEqual(result["capability"], "structured_only")
        self.assertEqual(result["terminal_status"], "degraded")
        self.assertIn("TOOL_PROVIDER_UNSUPPORTED", result["error_codes"])

    def test_structured_writer_timeout_is_explicit_local_chapter_degradation(self) -> None:
        from zhiying.execution.steps.editorial_steps import run_editorial_session

        data = self._editorial_inputs()
        json_port = _WriterFailJsonPort(data["blueprint"])
        ports = _Ports({"cloud": json_port})
        context = SimpleNamespace(
            policy=SimpleNamespace(cloud_authorized=True), services=ports,
            options=SimpleNamespace(knowledge={}),
            source=SimpleNamespace(video_id="lesson", display_title="课程"),
        )
        result = run_editorial_session(context, **{key: value for key, value in data.items() if key != "blueprint"})
        self.assertEqual(json_port.calls, 2)
        self.assertEqual(result["terminal_status"], "degraded")
        self.assertTrue(any(reason.startswith("writer_structured:TimeoutError") for reason in result["degradation_reasons"]))
        self.assertEqual(set(result["provenance"]["chapter_writing"].values()), {"local_deterministic"})

    def test_terminal_status_preserves_degraded_outcome(self) -> None:
        from zhiying.execution.bootstrap import summarize_terminal_status

        self.assertEqual(summarize_terminal_status(["succeeded", "cached"]), "succeeded")
        self.assertEqual(summarize_terminal_status(["succeeded", "degraded"]), "degraded")
        self.assertEqual(summarize_terminal_status(["succeeded", "failed"]), "failed")
        self.assertEqual(summarize_terminal_status(["succeeded", "cancelled"]), "cancelled")

    def test_production_topology_has_only_v31_editorial_document_path(self) -> None:
        output = ArtifactId("render.bundle", ("lesson.md", "lesson.docx", "lesson.pdf"), "output")
        step_ids = tuple(step.spec.step_id for step in build_coarse_steps(output))
        self.assertEqual(len(step_ids), 23)
        for active in (
            "editorial.policy", "evidence.reconcile", "document.blueprint",
            "document.write", "document.assemble", "document.validate",
        ):
            self.assertIn(active, step_ids)
        for legacy in (
            "document.plan", "chapter.compose", "chapter.validate",
            "chapter.repair", "document.compile",
        ):
            self.assertNotIn(legacy, step_ids)

    def test_render_verify_requires_document_for_cross_format_audit(self) -> None:
        output = ArtifactId("render.bundle", ("lesson.md", "lesson.docx", "lesson.pdf"), "output")
        step = RenderVerifyStep(output)
        self.assertIn("document.assemble", step.spec.dependencies)
        self.assertIn(DOCUMENT_V3, step.spec.inputs)

    def test_render_verify_rejects_forbidden_legacy_heading(self) -> None:
        output = ArtifactId(
            "render.bundle", ("state/render/lesson.md", "state/render/lesson.docx", "state/render/lesson.pdf")
        )
        step = RenderVerifyStep(output)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "lesson.md"
            markdown.write_text("# 课程\n\n## 内容导览\n", encoding="utf-8")
            (root / "lesson.docx").write_bytes(b"docx")
            (root / "lesson.pdf").write_bytes(b"pdf")
            outcome = StepOutcome(
                "render.verify", "run-1", StepStatus.SUCCEEDED,
                artifacts=(ArtifactRef(output, markdown),),
            )
            with self.assertRaisesRegex(ValueError, "旧业务栏目"):
                step.validate(None, outcome)


if __name__ == "__main__":
    unittest.main()
