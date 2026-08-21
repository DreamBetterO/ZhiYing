from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from video_study.execution.task_groups import FileTaskGroupCache
from video_study.knowledge.schema import ChapterPlan, EvidenceSpan, LessonPlan, UnitPlan, VisualEvidence, VisualNeed, VisualQuestion
from video_study.knowledge.visual_retrieval import (
    _criteria_supported_by_visible_evidence,
    _resolve_candidate_id,
    _vlm_select,
    build_visual_evidence,
    is_vlm_failure_source,
    recall_candidates_for_question,
    sanitize_visual_evidence_for_reader,
)
from video_study.execution.adapters.vision import VisualProviderOOMError


class FakeVLM:
    name = "fake-vlm"

    def __init__(
        self,
        *,
        invalid_id: bool = False,
        missing: bool = False,
        detail: bool = False,
        echo_criteria: bool = False,
        criteria_typos: bool = False,
        visual_answer: str = "输入经过处理后得到输出",
    ) -> None:
        self.invalid_id = invalid_id
        self.missing = missing
        self.detail = detail
        self.echo_criteria = echo_criteria
        self.criteria_typos = criteria_typos
        self.visual_answer = visual_answer
        self.compare_calls = 0
        self.detail_calls = 0

    def compare_candidates(self, question, candidates, contract):
        self.compare_calls += 1
        criteria = list(contract.get("success_criteria", []))
        return {
            "decision": "select",
            "selected_candidate_id": "invented" if self.invalid_id else candidates[0]["candidate_id"],
            "visible_evidence": criteria if self.echo_criteria else ["图中清楚显示流程节点"],
            "criteria_met": (
                ["可见输入节占", "可见输出节占"] if self.criteria_typos
                else criteria[:-1] if self.missing else criteria
            ),
            "criteria_missing": criteria[-1:] if self.missing else [],
            "visual_answer": self.visual_answer,
            "confidence": 0.88,
            "needs_detail_pass": self.detail,
        }

    def extract_selected(self, candidate, contract):
        self.detail_calls += 1
        return {
            "visible_evidence": ["图中可见输入、处理、输出三个节点"],
            "criteria_met": list(contract.get("success_criteria", [])),
            "criteria_missing": [],
            "visual_answer": "三个节点由箭头依次连接",
        }


class OOMVLM(FakeVLM):
    def compare_candidates(self, question, candidates, contract):
        self.compare_calls += 1
        raise VisualProviderOOMError("CUDA out of memory")


class VisualRetrievalTests(unittest.TestCase):
    def test_visual_evidence_step_consumes_candidate_pool_not_global_selection(self) -> None:
        from video_study.execution.artifacts import FRAMES_CANDIDATES, FRAMES_SELECTED
        from video_study.execution.steps.knowledge import VisualEvidenceStep, VisualJobsStep

        self.assertIn(FRAMES_CANDIDATES, VisualEvidenceStep.spec.inputs)
        self.assertNotIn(FRAMES_SELECTED, VisualEvidenceStep.spec.inputs)
        self.assertIn(FRAMES_CANDIDATES, VisualJobsStep.spec.inputs)

    def test_candidate_artifact_rows_keep_candidate_identity(self) -> None:
        from video_study.knowledge.visual_retrieval import _candidate_rows

        rows = _candidate_rows(Path("unused"), {
            "candidates": [{
                "candidate_id": "candidate_00007",
                "timestamp_seconds": 12.5,
                "path": "candidate_00007.jpg",
            }],
        })
        self.assertEqual(rows[0]["image_id"], "candidate_00007")
        self.assertEqual(rows[0]["timestamp_label"], "00:00:12")

    def test_only_inference_failures_are_classified_as_vlm_degradation(self) -> None:
        for source in ("vlm_provider_error", "vlm_oom_no_match", "vlm_detail_failed", "vlm_invalid_candidate_id"):
            self.assertTrue(is_vlm_failure_source(source), source)
        for source in (
            "vlm_rejected", "vlm_criteria_rejected", "vlm_no_candidate",
            "fallback_no_candidate", "fallback_no_pixel_evidence", "global_scene_arbitration",
        ):
            self.assertFalse(is_vlm_failure_source(source), source)

    def test_candidate_alias_is_removed_from_reader_fields(self) -> None:
        item = VisualEvidence(
            visual_answer="C",
            visual_summary="C",
            visible_evidence=["图中可见K线位于反穿标注上方"],
            criteria_met=["画面中可辨认K线"],
            timestamp=160.0,
            suggested_caption="C（00:02:40）",
            explanation_for_reader="为什么重要：C",
        )
        sanitize_visual_evidence_for_reader(item)
        self.assertNotEqual(item.visual_answer, "C")
        self.assertNotIn("为什么重要：C", item.explanation_for_reader)
        self.assertIn("K线", item.suggested_caption)

    def test_visible_facts_can_cover_omitted_relation_checkbox(self) -> None:
        criteria = ["画面中可辨认K线", "画面中可辨认K线与反穿的相对位置或线条关系"]
        evidence = ["图中第6个K线位于第7个K线的上方", "两根K线之间有明显的反穿形态"]
        self.assertEqual(_criteria_supported_by_visible_evidence(criteria, evidence), criteria)
        self.assertEqual(
            _criteria_supported_by_visible_evidence(criteria, ["图中可见K线"]),
            [criteria[0]],
        )

    def test_candidate_id_normalization_still_requires_allowed_set(self) -> None:
        allowed = {"candidate_00011", "candidate_00017"}
        self.assertEqual(_resolve_candidate_id(" Candidate_00017.JPG ", allowed), "candidate_00017")
        self.assertEqual(_resolve_candidate_id("folder/candidate_00011.png", allowed), "candidate_00011")
        self.assertEqual(_resolve_candidate_id("candidate_00036.jpg", allowed), "")

    def test_detail_jobs_are_capped_at_two(self) -> None:
        provider = FakeVLM(detail=True)
        question = VisualQuestion(
            question_id="vq_001_01", unit_id="plan_001",
            question="哪张图能展示流程节点？", expected_entities=["流程节点"],
        )
        contract = VisualNeed(required=True, success_criteria=["画面中可辨认流程节点"])
        rows = [{
            "image_id": "candidate_00001", "path": "frame.jpg",
            "timestamp_seconds": 1.0, "scene_cluster_id": "scene_1",
        }]
        detail_counter = [0]
        for _ in range(3):
            _vlm_select(question, rows, contract, provider, True, detail_counter)
        self.assertEqual(provider.detail_calls, 2)
        self.assertEqual(detail_counter[0], 2)

    def test_recalls_candidates_inside_question_window(self) -> None:
        question = VisualQuestion(
            question_id="vq_001_01",
            unit_id="plan_001",
            question="哪张图能展示流程",
            anchor_spans=[EvidenceSpan(start_seconds=30, end_seconds=40, segment_ids=["seg_1"])],
        )
        candidates = [
            {"image_id": "c1", "timestamp_seconds": 5.0, "path": ""},
            {"image_id": "c2", "timestamp_seconds": 35.0, "path": ""},
            {"image_id": "c3", "timestamp_seconds": 130.0, "path": ""},
        ]
        rows = recall_candidates_for_question(question, candidates, max_candidates=2)
        self.assertEqual(rows[0]["image_id"], "c2")
        self.assertGreater(rows[0]["time_prior"], rows[1]["time_prior"])

    def _candidate_fixture(self, root: Path) -> tuple[LessonPlan, dict, dict]:
        candidates = root / "images" / "candidates"
        candidates.mkdir(parents=True)
        for index in range(1, 4):
            image = Image.new("RGB", (120, 80), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((10 * index, 10, 80, 60), outline="black", width=2)
            image.save(candidates / f"candidate_{index:05d}.jpg")
        plan = LessonPlan(chapters=[ChapterPlan(unit_plans=[UnitPlan(
            plan_id="plan_001",
            title="流程图",
            visual_questions=[VisualQuestion(
                question_id="vq_001_01",
                unit_id="plan_001",
                question="哪张图能展示流程图",
                expected_entities=["流程图"],
                anchor_spans=[EvidenceSpan(start_seconds=0, end_seconds=12, segment_ids=["seg_1"])],
            )],
        )])])
        frames = {"sample_interval_seconds": 10.0, "frames": []}
        transcript = {"segments": [{"segment_id": "seg_1", "start_seconds": 0, "end_seconds": 5, "text": "看这个流程"}]}
        return plan, frames, transcript

    def test_without_pixel_evidence_returns_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(plan, frames, transcript, root)
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].matched_knowledge_point_id, "plan_001")
            self.assertEqual(evidence[0].decision, "no_match")
            self.assertEqual(evidence[0].source, "fallback_no_pixel_evidence")
            self.assertFalse(evidence[0].image_path)

    def test_per_job_cache_does_not_initialize_local_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan, frames, transcript = self._candidate_fixture(root)
            settings = {"local_vlm_enabled": "auto"}
            task_cache = FileTaskGroupCache(root / "state", "visual.evidence")
            build_visual_evidence(
                plan, frames, transcript, root, settings=settings, vlm_provider=FakeVLM(),
                task_cache=task_cache,
            )
            state: dict = {}
            cached = build_visual_evidence(
                plan, frames, transcript, root, settings=settings,
                task_cache=task_cache,
                provider_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("逐任务缓存命中时不应 preflight 或 cold load")
                ),
                runtime_state=state,
            )
            self.assertEqual(len(cached), 1)
            self.assertFalse(state["provider_initialized"])
            self.assertFalse(state["session_started"])
            self.assertEqual(state["model_load_count"], 0)

    def test_batch_failure_stops_later_vlm_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan, frames, transcript = self._candidate_fixture(root)
            first = plan.chapters[0].unit_plans[0]
            second = UnitPlan.from_dict(first.to_dict())
            second.plan_id = "plan_002"
            second.visual_questions[0].question_id = "vq_002_01"
            second.visual_questions[0].unit_id = "plan_002"
            plan.chapters[0].unit_plans.append(second)
            provider = OOMVLM()
            state: dict = {}
            evidence = build_visual_evidence(
                plan, frames, transcript, root,
                vlm_provider=provider, runtime_state=state,
            )
            self.assertEqual(provider.compare_calls, 2)
            self.assertEqual([item.source for item in evidence], ["vlm_oom_no_match", "vlm_batch_skipped"])
            self.assertEqual(state["current_failure_sources"], ["vlm_oom_no_match"])
            self.assertEqual(state["batch_skipped_count"], 1)

    def test_ocr_content_evidence_can_select(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(
                plan, frames, transcript, root,
                ocr_provider=lambda _path: "课程流程图：输入 处理 输出",
            )
            self.assertEqual(evidence[0].decision, "select")
            self.assertTrue(evidence[0].image_path.endswith(".jpg"))
            self.assertTrue(evidence[0].visible_evidence)
            self.assertTrue(evidence[0].dedup_group_id.startswith("scene_"))
            self.assertIn("OCR 命中", evidence[0].match_reason)

    def test_global_scene_arbitration_assigns_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidates = root / "images" / "candidates"
            candidates.mkdir(parents=True)
            image = Image.new("RGB", (120, 80), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 10, 90, 60), outline="black", width=2)
            image.save(candidates / "candidate_00001.jpg", quality=80)
            image.save(candidates / "candidate_00002.jpg", quality=95)
            plan = LessonPlan(chapters=[ChapterPlan(unit_plans=[
                UnitPlan(plan_id="plan_001", visual_questions=[VisualQuestion(
                    question_id="vq_001_01", unit_id="plan_001", question="流程图在哪里",
                    expected_entities=["流程图"],
                )]),
                UnitPlan(plan_id="plan_002", visual_questions=[VisualQuestion(
                    question_id="vq_002_01", unit_id="plan_002", question="流程图说明什么",
                    expected_entities=["流程图"],
                )]),
            ])])
            evidence = build_visual_evidence(
                plan,
                {"sample_interval_seconds": 10.0, "frames": []},
                {"segments": []},
                root,
                ocr_provider=lambda _path: "流程图",
            )
            selected = [item for item in evidence if item.decision == "select"]
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0].matched_knowledge_ids, ["plan_001", "plan_002"])
            self.assertEqual(len({item.dedup_group_id for item in evidence if item.image_path}), 1)

    def _v22_plan(self) -> LessonPlan:
        return LessonPlan(chapters=[ChapterPlan(unit_plans=[UnitPlan(
            plan_id="plan_001",
            title="输入处理输出流程",
            visual_need=VisualNeed(
                required=True,
                question="流程节点如何连接",
                role="procedure",
                target_count=1,
                max_count=1,
                sequence_mode="single",
                explanation_depth="teaching_note",
                success_criteria=["可见输入节点", "可见输出节点"],
            ),
            visual_questions=[VisualQuestion(
                question_id="vq_001_01",
                unit_id="plan_001",
                question="流程节点如何连接",
                expected_entities=["流程", "输入", "输出"],
                preferred_visual_role="procedure",
            )],
        )])])

    def test_fake_vlm_selects_only_legal_candidate_with_all_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            provider = FakeVLM(detail=True)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"allow_detail_pass": True},
                vlm_provider=provider,
            )
            self.assertEqual(evidence[0].decision, "select")
            self.assertEqual(evidence[0].source, "fake-vlm")
            self.assertEqual(evidence[0].criteria_missing, [])
            self.assertEqual(evidence[0].visual_role, "procedure")
            self.assertEqual(provider.compare_calls, 1)
            self.assertEqual(provider.detail_calls, 1)

    def test_fake_vlm_rejects_invented_candidate_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                vlm_provider=FakeVLM(invalid_id=True),
            )
            self.assertEqual(evidence[0].decision, "no_match")
            self.assertEqual(evidence[0].source, "vlm_invalid_candidate_id")

    def test_retry_transient_failure_reuses_contract_but_not_failed_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = FileTaskGroupCache(root / "state", "visual.evidence")
            _, frames, transcript = self._candidate_fixture(root)
            failed = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                vlm_provider=OOMVLM(),
                task_cache=cache,
            )
            self.assertEqual(failed[0].source, "vlm_oom_no_match")
            recovered = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"retry_transient_failures": True},
                vlm_provider=FakeVLM(),
                task_cache=cache,
            )
            self.assertEqual(recovered[0].decision, "select")
            self.assertEqual(recovered[0].source, "fake-vlm")

    def test_fake_vlm_rejects_missing_success_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                vlm_provider=FakeVLM(missing=True),
            )
            self.assertEqual(evidence[0].decision, "no_match")
            self.assertEqual(evidence[0].source, "vlm_criteria_rejected")
            self.assertEqual(evidence[0].visible_evidence, ["图中清楚显示流程节点"])
            self.assertTrue(evidence[0].criteria_met)
            self.assertTrue(evidence[0].criteria_missing)
            self.assertIn(evidence[0].criteria_missing[0], evidence[0].match_reason)

    def test_fake_vlm_maps_small_copying_errors_back_to_allowed_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                vlm_provider=FakeVLM(criteria_typos=True),
            )
            self.assertEqual(evidence[0].decision, "select")
            self.assertEqual(evidence[0].criteria_missing, [])

    def test_fake_vlm_rejects_criteria_echo_as_visible_evidence_without_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"allow_detail_pass": False},
                vlm_provider=FakeVLM(echo_criteria=True),
            )
            self.assertEqual(evidence[0].decision, "no_match")
            self.assertEqual(evidence[0].source, "vlm_criteria_rejected")

    def test_fake_vlm_can_use_visual_answer_as_fallback_visible_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"allow_detail_pass": False},
                vlm_provider=FakeVLM(
                    echo_criteria=True,
                    visual_answer="图中可见输入节点通过箭头连接到输出节点。",
                ),
            )
            self.assertEqual(evidence[0].decision, "select")
            self.assertEqual(evidence[0].visible_evidence, ["图中可见输入节点通过箭头连接到输出节点。"])

    def test_fake_vlm_rejects_reader_unsafe_visual_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"allow_detail_pass": False},
                vlm_provider=FakeVLM(
                    echo_criteria=True,
                    visual_answer="candidate_00018",
                ),
            )
            self.assertEqual(evidence[0].decision, "no_match")
            self.assertEqual(evidence[0].source, "vlm_criteria_rejected")

    def test_fake_vlm_uses_detail_pass_when_compare_only_echoes_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            provider = FakeVLM(echo_criteria=True)
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"allow_detail_pass": True},
                vlm_provider=provider,
            )
            self.assertEqual(evidence[0].decision, "select")
            self.assertEqual(provider.detail_calls, 1)
            self.assertNotIn("可见输入节点", evidence[0].visible_evidence)

    def test_vlm_oom_retries_once_with_two_candidates_then_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            provider = OOMVLM()
            evidence = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                vlm_provider=provider,
            )
            self.assertEqual(evidence[0].decision, "no_match")
            self.assertEqual(evidence[0].source, "vlm_oom_no_match")
            self.assertEqual(provider.compare_calls, 2)

    def test_vlm_runtime_settings_invalidate_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, frames, transcript = self._candidate_fixture(root)
            task_cache = FileTaskGroupCache(root / "state", "visual.evidence")
            first = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"local_vlm_enabled": True, "local_vlm_runtime_dir": "runtime-a"},
                vlm_provider=FakeVLM(),
                task_cache=task_cache,
            )
            second = build_visual_evidence(
                self._v22_plan(), frames, transcript, root,
                settings={"local_vlm_enabled": True, "local_vlm_runtime_dir": "runtime-b"},
                vlm_provider=FakeVLM(missing=True),
                task_cache=task_cache,
            )
            self.assertEqual(first[0].decision, "select")
            self.assertEqual(second[0].decision, "no_match")
            self.assertEqual(second[0].source, "vlm_criteria_rejected")

    def test_visual_step_unexpected_provider_error_degrades_and_writes_artifact(self) -> None:
        from types import SimpleNamespace

        from video_study.execution.artifacts import (
            FRAMES_CANDIDATES,
            KNOWLEDGE_PLAN,
            TRANSCRIPT_NORMALIZED,
            VISUAL_JOBS,
            ArtifactRef,
        )
        from video_study.execution.contracts import StepStatus
        from video_study.execution.steps.knowledge import VisualEvidenceStep

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = {}
            payloads = {
                KNOWLEDGE_PLAN: {"plan": LessonPlan().to_dict()},
                FRAMES_CANDIDATES: {"candidates": []},
                TRANSCRIPT_NORMALIZED: {"segments": []},
                VISUAL_JOBS: {"jobs": []},
            }
            for artifact_id, payload in payloads.items():
                path = root / f"{artifact_id.name}.json"
                path.write_text(__import__("json").dumps(payload), encoding="utf-8")
                inputs[artifact_id] = ArtifactRef(artifact_id, path)
            events = []
            services = SimpleNamespace(
                cancelled=lambda: False,
                event_sink=events.append,
                progress_sink=lambda _event: None,
            )
            context = SimpleNamespace(
                run_id="run-visual-fallback",
                workspace=SimpleNamespace(state_dir=root / "state"),
                options=SimpleNamespace(visual={"visual_evidence": {}}),
                services=services,
            )
            step = VisualEvidenceStep()
            with patch(
                "video_study.execution.steps.knowledge.build_visual_evidence",
                side_effect=[NameError("tempfile is not defined"), []],
            ) as build:
                outcome = step.execute(context, inputs, root / "staging")
            self.assertEqual(outcome.status, StepStatus.DEGRADED)
            self.assertEqual(build.call_count, 2)
            self.assertTrue(outcome.artifacts[0].path.is_file())
            self.assertEqual(events[-1]["code"], "visual_evidence_offline_fallback")


if __name__ == "__main__":
    unittest.main()
