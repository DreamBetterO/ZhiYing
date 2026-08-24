"""CP61-7 cached golden replay；只读 Workspace，provider 全为本地 replay。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from zhiying.editorial.evidence import build_evidence_overlay
from zhiying.editorial.intent import compile_editorial_policy
from zhiying.editorial.local import build_local_blueprint
from zhiying.editorial.quality import audit_render_outputs
from zhiying.execution.steps.editorial_steps import _enrich_units_with_sources, run_editorial_session
from zhiying.knowledge.editorial import load_brief
from zhiying.knowledge.schema import ChapterPlan, LessonPlan, UnitPlan
from zhiying.documents.render_v31 import render_docx_v31, render_markdown_v31, render_pdf_fallback_v31


ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = (
    ("高数-定积分定义-be06877a42cf", ("圆寒数", "便上线级分")),
    ("高数-例题讲解-a5c1f2d59bf3", ("长数",)),
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _ReplayJsonPort:
    def __init__(self, blueprint: dict) -> None:
        self.blueprint = blueprint
        self.calls = 0

    def request_json(self, payload, *, validator, stage, **_kwargs):
        self.calls += 1
        value = {"chapters": payload["draft_chapters"]} if stage == "writer" else self.blueprint
        return validator(value)


class _ReplayToolPort:
    def __init__(self, blueprint: dict) -> None:
        self.blueprint = blueprint
        self.calls = 0

    def invoke_turn(self, **_kwargs):
        from zhiying.execution.tool_calling import ToolCallRecord, ToolTurn
        self.calls += 1
        return ToolTurn(tool_calls=(ToolCallRecord(
            name="submit_blueprint", args={"blueprint": self.blueprint},
            tool_call_id=f"golden-{self.calls}",
        ),), model="replay-model", finish_reason="tool_calls")


class _Services:
    def __init__(self, ports: dict, models: tuple[str, ...] = ("replay-model",)) -> None:
        self.ports = ports
        self.credentials = SimpleNamespace(models=models)

    def port(self, name: str):
        return self.ports[name]

    @staticmethod
    def cancelled() -> bool:
        return False


class V61OfflineAcceptanceTests(unittest.TestCase):
    def test_two_high_math_cached_workspaces_replay_all_three_capabilities(self) -> None:
        for video_id, forbidden_raw in WORKSPACES:
            workspace = ROOT / "workspace" / video_id
            required = (
                workspace / "manifest.json",
                workspace / "transcript" / "transcript.json",
                workspace / "knowledge" / "lesson-plan.json",
                workspace / "knowledge" / "knowledge-units.json",
                workspace / "knowledge" / "visual-evidence.json",
            )
            missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
            if missing:
                self.skipTest(f"冻结 Workspace 不完整：{video_id}；缺少 {', '.join(missing)}")
            manifest = _read(workspace / "manifest.json")
            transcript = _read(workspace / "transcript" / "transcript.json")
            plan = LessonPlan.from_dict(_read(workspace / "knowledge" / "lesson-plan.json").get("plan", {}))
            raw_units = list(_read(workspace / "knowledge" / "knowledge-units.json").get("units", []))
            visual = list(_read(workspace / "knowledge" / "visual-evidence.json").get("visual_evidence", []))
            policy = compile_editorial_policy(load_brief())
            blueprint = build_local_blueprint(plan, policy).to_dict()

            for mode in ("local_deterministic", "structured_only", "tool_native"):
                with self.subTest(video_id=video_id, mode=mode):
                    json_port = _ReplayJsonPort(blueprint)
                    tool_port = _ReplayToolPort(blueprint)
                    ports = {} if mode == "local_deterministic" else {"cloud": json_port}
                    knowledge = {}
                    if mode == "tool_native":
                        ports["cloud_tool"] = tool_port
                        knowledge["model_capabilities"] = {"replay-model": "tool_native"}
                    context = SimpleNamespace(
                        policy=SimpleNamespace(cloud_authorized=mode != "local_deterministic"),
                        services=_Services(ports), options=SimpleNamespace(knowledge=knowledge),
                        source=SimpleNamespace(video_id=video_id, display_title=str(manifest.get("title", video_id))),
                    )
                    units = _enrich_units_with_sources(context, plan, raw_units)
                    result = run_editorial_session(
                        context, plan=plan.to_dict(), units=units,
                        overlay=build_evidence_overlay(transcript).to_dict(),
                        visual_evidence=visual, policy=policy.to_dict(),
                        transcript=transcript, manifest=manifest,
                    )
                    document = result["document_candidate"]
                    self.assertEqual(document["contract_version"], "document-v3.1")
                    self.assertEqual(result["quality_report"]["status"], "valid")
                    body = json.dumps(document["components"], ensure_ascii=False)
                    for raw in forbidden_raw:
                        self.assertNotIn(raw, body)
                    if mode == "local_deterministic":
                        self.assertEqual(json_port.calls, 0)
                        self.assertEqual(tool_port.calls, 0)
                    elif mode == "structured_only":
                        self.assertEqual(json_port.calls, 2)
                    else:
                        self.assertEqual(tool_port.calls, 1)
                        self.assertEqual(json_port.calls, 1)

                    with tempfile.TemporaryDirectory() as directory:
                        output = Path(directory)
                        document_path = output / "document.json"
                        document_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
                        markdown = render_markdown_v31(document, output / "document.md", project_root=ROOT)
                        docx = render_docx_v31(document_path, output / "document.docx", project_root=ROOT)
                        pdf = render_pdf_fallback_v31(document, output / "document.pdf")
                        audit = audit_render_outputs(
                            document, markdown=markdown, docx=docx, pdf=pdf, pdf_mode="built_in_v31",
                        )
                    self.assertEqual(audit["status"], "valid")

    def test_strong_and_weak_visual_goldens_match_all_three_capabilities(self) -> None:
        for fixture_name, expected_images in (("strong_visual.json", 1), ("weak_visual.json", 0)):
            fixture = _read(ROOT / "tests" / "fixtures" / "v61" / fixture_name)
            source_unit = fixture["plan"]["units"][0]
            plan = LessonPlan(chapters=[ChapterPlan(
                chapter_id="chapter_001", title=source_unit["title"],
                source_segment_ids=list(source_unit.get("source_segment_ids", [])),
                unit_plans=[UnitPlan(
                    plan_id=source_unit["plan_id"], title=source_unit["title"],
                    role=source_unit.get("role", "core"),
                    knowledge_types=[source_unit.get("type", "concept")],
                    source_segment_ids=list(source_unit.get("source_segment_ids", [])),
                )],
            )])
            policy = compile_editorial_policy(load_brief())
            blueprint = build_local_blueprint(plan, policy).to_dict()
            segment_ids = set(source_unit.get("source_segment_ids", []))
            definition = "。".join(
                str(row.get("text", "")) for row in fixture["transcript"]["segments"]
                if row.get("segment_id") in segment_ids
            )
            raw_units = [{
                "unit_id": "unit_0001", "plan_id": source_unit["plan_id"],
                "type": source_unit.get("type", "concept"), "title": source_unit["title"],
                "definition_or_conclusion": definition, "source_refs": {"segment_ids": sorted(segment_ids)},
            }]
            for mode in ("local_deterministic", "structured_only", "tool_native"):
                with self.subTest(fixture=fixture_name, mode=mode):
                    json_port = _ReplayJsonPort(blueprint)
                    tool_port = _ReplayToolPort(blueprint)
                    ports = {} if mode == "local_deterministic" else {"cloud": json_port}
                    knowledge = {}
                    if mode == "tool_native":
                        ports["cloud_tool"] = tool_port
                        knowledge["model_capabilities"] = {"replay-model": "tool_native"}
                    context = SimpleNamespace(
                        policy=SimpleNamespace(cloud_authorized=mode != "local_deterministic"),
                        services=_Services(ports), options=SimpleNamespace(knowledge=knowledge),
                        source=SimpleNamespace(video_id=fixture["id"], display_title=source_unit["title"]),
                    )
                    units = _enrich_units_with_sources(context, plan, raw_units)
                    result = run_editorial_session(
                        context, plan=plan.to_dict(), units=units,
                        overlay=build_evidence_overlay(fixture["transcript"]).to_dict(),
                        visual_evidence=list(fixture["visual_evidence"]), policy=policy.to_dict(),
                        transcript=fixture["transcript"], manifest={"duration_seconds": 38},
                    )
                    images = _collect(result["document_candidate"].get("components", []), "image")
                    self.assertEqual(len(images), expected_images)
                    for image in images:
                        for field in ("visual_id", "role", "caption", "alt_text", "source_timestamp"):
                            self.assertTrue(image.get(field) is not None, field)
                    self.assertEqual(result["quality_report"]["status"], "valid")


def _collect(components: list[dict], component_type: str) -> list[dict]:
    rows: list[dict] = []
    for component in components:
        if component.get("type") == component_type:
            rows.append(component)
        rows.extend(_collect(component.get("children", []), component_type))
    return rows


if __name__ == "__main__":
    unittest.main()
