"""CP61-4 责任测试：EditorialAgentSubgraph 与有限编辑循环。

全部使用 scripted/replay provider，不发真实云请求。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from zhiying.editorial.agent import _initial_state, build_editorial_agent
from zhiying.editorial.blueprint import DocumentBlueprint
from zhiying.editorial.document import validate_document_v31
from zhiying.editorial.evidence import EvidenceCorrectionOverlay, detect_local_corrections, transcript_digest
from zhiying.editorial.intent import compile_editorial_policy
from zhiying.editorial.local import build_local_blueprint
from zhiying.editorial.tools import EditorialToolContext
from zhiying.editorial.writer import write_chapters_in_batches
from zhiying.execution.tool_calling import StageBudget, ToolCallRecord, ToolTurn, build_stage_budget
from zhiying.knowledge.editorial import brief_from_text
from zhiying.knowledge.schema import ChapterPlan, LessonPlan, UnitPlan

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "v61"


class ScriptedToolPort:
    """replay provider：按序返回预置 ToolTurn，记录调用次数。"""

    def __init__(self, turns: list[ToolTurn]) -> None:
        self._turns = list(turns)
        self.calls = 0

    def invoke_turn(self, **kwargs):
        self.calls += 1
        if not self._turns:
            return ToolTurn(content="", model="replay", finish_reason="stop")
        return self._turns.pop(0)


class FakeJsonPort:
    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload
        self.calls = 0

    def request_json(self, payload, *, validator, stage, cancel_check):
        self.calls += 1
        if self._payload is None:
            raise RuntimeError("structured provider unavailable")
        return validator(self._payload)


class FailingJsonPort:
    def request_json(self, payload, *, validator, stage, cancel_check):
        raise RuntimeError("safe structured failure")


def _fixture_state(capability: str, **overrides) -> dict:
    fixture = json.loads((FIXTURES / "math_concept.json").read_text(encoding="utf-8"))
    plan = LessonPlan(chapters=[ChapterPlan(
        chapter_id="chapter_001", title="原函数", source_segment_ids=["seg_00001"],
        unit_plans=[UnitPlan(plan_id="plan_001", title="原函数的定义", role="core", knowledge_types=["concept"])],
    )])
    units = [{
        "unit_id": "unit_0001", "type": "concept", "title": "原函数的定义",
        "definition_or_conclusion": "如果有一个大F，它的导数等于小F，那这个大F就叫做小F的一个圆寒数，小F加上任意长数也还是圆寒数",
        "rules": [], "procedure": [], "pitfalls": [], "unresolved": [],
        "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00001"]},
    }]
    policy = compile_editorial_policy(brief_from_text("不要内容导览、不要学习目标、不要课程复习")).to_dict()
    overlay = EvidenceCorrectionOverlay(
        version=1, transcript_digest=transcript_digest(fixture["transcript"]),
        corrections=detect_local_corrections(fixture["transcript"]),
    )
    state = _initial_state(
        capability=capability,
        policy=policy,
        plan=plan.to_dict(),
        plan_units=units,
        evidence_overlay=overlay.to_dict(),
        visual_evidence=[],
        metadata={"video_id": "math_concept", "document_title": "原函数概念"},
        transcript_digest=overlay.transcript_digest,
    )
    state.update(overrides)
    return state


def _blueprint_payload() -> dict:
    fixture = json.loads((FIXTURES / "math_concept.json").read_text(encoding="utf-8"))
    plan = LessonPlan(chapters=[ChapterPlan(
        chapter_id="chapter_001", title="原函数", source_segment_ids=["seg_00001"],
        unit_plans=[UnitPlan(plan_id="plan_001", title="原函数的定义", role="core", knowledge_types=["concept"])],
    )])
    policy = compile_editorial_policy(brief_from_text("不要内容导览、不要学习目标、不要课程复习"))
    return build_local_blueprint(plan, policy).to_dict()


class EditorialGraphTests(unittest.TestCase):
    def _run(self, capability: str, tool_port=None, json_port=None, **state_overrides):
        tools_ctx = EditorialToolContext()
        graph = build_editorial_agent(
            capability=capability,
            tool_port=tool_port,
            json_port=json_port,
            tools_ctx=tools_ctx,
            known_unit_ids={"plan_001"},
            budget=build_stage_budget(max_tool_turns=8),
            max_revision_cycles=1,
        )
        state = _fixture_state(capability, **state_overrides)
        return graph.invoke(state), tools_ctx

    def test_direct_blueprint_submit_one_call(self) -> None:
        turn = ToolTurn(
            content="",
            tool_calls=(ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="call_1"),),
            model="replay", finish_reason="tool_calls",
        )
        port = ScriptedToolPort([turn])
        result, _ctx = self._run("tool_native", tool_port=port)
        self.assertEqual(result["terminal_status"], "succeeded")
        self.assertEqual(result["tool_turns"], 1)
        self.assertIsNotNone(result["accepted_blueprint"])
        self.assertEqual(result["provenance"]["blueprint"], "tool_native")

    def test_observe_then_submit_two_calls(self) -> None:
        observe = ToolTurn(
            content="需要看证据",
            tool_calls=(ToolCallRecord(name="lookup_evidence", args={"query": "原函数", "unit_ids": ["plan_001"]}, tool_call_id="call_1"),),
            model="replay", finish_reason="tool_calls",
        )
        submit = ToolTurn(
            content="",
            tool_calls=(ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="call_2"),),
            model="replay", finish_reason="tool_calls",
        )
        port = ScriptedToolPort([observe, submit])
        result, _ctx = self._run("tool_native", tool_port=port)
        self.assertEqual(result["terminal_status"], "succeeded")
        self.assertEqual(result["tool_turns"], 2)

    def test_invalid_then_single_retry(self) -> None:
        invalid = ToolTurn(
            content="",
            invalid_tool_calls=(ToolCallRecord(name="submit_blueprint", args={}, tool_call_id="call_bad"),),
            model="replay", finish_reason="tool_calls",
        )
        submit = ToolTurn(
            content="",
            tool_calls=(ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="call_2"),),
            model="replay", finish_reason="tool_calls",
        )
        port = ScriptedToolPort([invalid, submit])
        result, _ctx = self._run("tool_native", tool_port=port)
        self.assertEqual(result["terminal_status"], "succeeded")
        self.assertEqual(result["tool_turns"], 2)
        self.assertIn("TOOL_ARGS_INVALID", result["error_codes"])

    def test_unknown_and_multiple_mutation_calls_are_rejected_before_tool_node(self) -> None:
        invalid_turns = (
            (ToolTurn(tool_calls=(ToolCallRecord(
                name="write_file", args={"path": "outside"}, tool_call_id="bad-1",
            ),), model="replay", finish_reason="tool_calls"), "TOOL_UNKNOWN"),
            (ToolTurn(tool_calls=(
                ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="bad-2"),
                ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="bad-3"),
            ), model="replay", finish_reason="tool_calls"), "TOOL_MULTIPLE_MUTATIONS"),
        )
        for invalid, expected_code in invalid_turns:
            with self.subTest(expected_code=expected_code):
                submit = ToolTurn(tool_calls=(ToolCallRecord(
                    name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="good",
                ),), model="replay", finish_reason="tool_calls")
                result, _ = self._run("tool_native", tool_port=ScriptedToolPort([invalid, submit]))
                self.assertEqual(result["terminal_status"], "succeeded")
                self.assertIn(expected_code, result["error_codes"])

    def test_repeated_no_progress_is_bounded_and_explicitly_degraded(self) -> None:
        port = ScriptedToolPort([
            ToolTurn(content="still thinking", model="replay", finish_reason="stop"),
            ToolTurn(content="still thinking", model="replay", finish_reason="stop"),
        ])
        tools_ctx = EditorialToolContext()
        graph = build_editorial_agent(
            capability="tool_native", tool_port=port, tools_ctx=tools_ctx,
            known_unit_ids={"plan_001"}, budget=build_stage_budget(max_tool_turns=2),
            max_tool_turns=2,
        )
        result = graph.invoke(_fixture_state("tool_native"))
        self.assertEqual(port.calls, 2)
        self.assertEqual(result["capability"], "tool_native")
        self.assertEqual(result["effective_capability"], "local_deterministic")
        self.assertEqual(result["terminal_status"], "degraded")
        self.assertIn("TOOL_NO_PROGRESS", result["error_codes"])

    def test_audit_pass_does_not_enter_revision(self) -> None:
        turn = ToolTurn(
            content="",
            tool_calls=(ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="call_1"),),
            model="replay", finish_reason="tool_calls",
        )
        port = ScriptedToolPort([turn])
        result, _ctx = self._run("tool_native", tool_port=port)
        self.assertEqual(result["quality_report"]["status"], "valid")
        self.assertEqual(result["revision_cycles_used"], 0)
        self.assertEqual(result["terminal_status"], "succeeded")

    def test_structured_failure_reason_is_persisted_safely(self) -> None:
        result, _ctx = self._run("structured_only", json_port=FailingJsonPort())
        self.assertEqual(result["terminal_status"], "degraded")
        self.assertTrue(any(
            "RuntimeError:safe structured failure" in reason
            for reason in result["degradation_reasons"]
        ))

    def _poisoned_setup(self) -> tuple[dict, dict, ToolTurn, ToolTurn]:
        """返回 (state, poisoned_blueprint, submit_turn, patch_turn)。

        毒化输入：章节标题带固定编号（01 · 原函数）→ 质量门失败；
        修订 patch：用干净章节容器替换（去掉编号）。
        """
        policy = compile_editorial_policy(brief_from_text("不要固定章节编号")).to_dict()
        fixture = json.loads((FIXTURES / "math_concept.json").read_text(encoding="utf-8"))
        plan = LessonPlan(chapters=[ChapterPlan(
            chapter_id="chapter_001", title="01 · 原函数", source_segment_ids=["seg_00001"],
            unit_plans=[UnitPlan(plan_id="plan_001", title="原函数的定义", role="core", knowledge_types=["concept"])],
        )])
        units = [{
            "unit_id": "unit_0001", "type": "concept", "title": "原函数的定义",
            "definition_or_conclusion": "原函数是导数等于自身的函数",
            "rules": [], "procedure": [], "pitfalls": [], "unresolved": [],
            "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00001"]},
        }]
        overlay = EvidenceCorrectionOverlay(version=1, transcript_digest=transcript_digest(fixture["transcript"]), corrections=[])
        state = _initial_state(
            capability="tool_native", policy=policy, plan=plan.to_dict(), plan_units=units,
            evidence_overlay=overlay.to_dict(), visual_evidence=[],
            metadata={"video_id": "x", "document_title": "t"}, transcript_digest=overlay.transcript_digest,
        )
        poisoned = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("不要固定章节编号"))).to_dict()
        poisoned["chapters"][0]["title"] = "01 · 原函数"
        submit = ToolTurn(content="", tool_calls=(
            ToolCallRecord(name="submit_blueprint", args={"blueprint": poisoned}, tool_call_id="call_1"),
        ), model="replay", finish_reason="tool_calls")
        # 干净章节容器（用于替换）
        from zhiying.editorial.writer import compose_chapter
        clean_blueprint = DocumentBlueprint.from_dict(poisoned)
        clean_blueprint.chapters[0].title = "原函数"
        clean_container = compose_chapter(clean_blueprint.chapters[0], units, overlay, [])
        patch_turn = ToolTurn(content="", tool_calls=(
            ToolCallRecord(name="submit_patch", args={
                "base_revision": "rev-0",
                "operations": [{"op": "replace_component", "component_id": "chapter_001", "component": clean_container}],
                "issue_ids": ["INTENT_FORBIDDEN_HIT"],
                "reason": "去掉固定编号",
            }, tool_call_id="call_2"),
        ), model="replay", finish_reason="tool_calls")
        return state, poisoned, submit, patch_turn

    def test_audit_fail_patch_rerender_pass(self) -> None:
        """fixed_numbering forbidden → 质量门失败 → 局部 patch 替换容器 → 重渲染通过。"""
        state, _poisoned, submit, patch_turn = self._poisoned_setup()
        port = ScriptedToolPort([submit, patch_turn])
        tools_ctx = EditorialToolContext()
        graph = build_editorial_agent(
            capability="tool_native", tool_port=port, tools_ctx=tools_ctx,
            known_unit_ids={"plan_001"},
            budget=build_stage_budget(max_tool_turns=8), max_revision_cycles=1,
        )
        result = graph.invoke(state)
        self.assertEqual(result["terminal_status"], "succeeded")
        self.assertEqual(result["revision_cycles_used"], 1)
        self.assertEqual(result["document_revision"], 1)

    def test_revision_conflict_degrades(self) -> None:
        """patch base_revision 冲突 → patch_validate 失败 → 不应用 → 降级终态。"""
        state, _poisoned, submit, _patch_turn = self._poisoned_setup()
        conflict_patch = ToolTurn(content="", tool_calls=(
            ToolCallRecord(name="submit_patch", args={
                "base_revision": "rev-9",  # 与当前 rev-0 冲突
                "operations": [{"op": "set_style_token", "component_id": "chapter_001.h", "token": "plain"}],
                "issue_ids": ["INTENT_FORBIDDEN_HIT"], "reason": "x",
            }, tool_call_id="call_2"),
        ), model="replay", finish_reason="tool_calls")
        port = ScriptedToolPort([submit, conflict_patch])
        tools_ctx = EditorialToolContext()
        graph = build_editorial_agent(
            capability="tool_native", tool_port=port, tools_ctx=tools_ctx,
            known_unit_ids={"plan_001"},
            budget=build_stage_budget(max_tool_turns=8), max_revision_cycles=1,
        )
        result = graph.invoke(state)
        self.assertEqual(result["terminal_status"], "degraded")
        self.assertEqual(result["document_revision"], 0)  # patch 未应用

    def test_three_paths_same_output_contract(self) -> None:
        outputs = {}
        # tool_native
        turn = ToolTurn(content="", tool_calls=(
            ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="call_1"),
        ), model="replay", finish_reason="tool_calls")
        result, _ = self._run("tool_native", tool_port=ScriptedToolPort([turn]))
        outputs["tool_native"] = result["document_candidate"]
        # structured_only
        result, _ = self._run("structured_only", json_port=FakeJsonPort(_blueprint_payload()))
        outputs["structured_only"] = result["document_candidate"]
        # local_deterministic
        result, _ = self._run("local_deterministic")
        outputs["local_deterministic"] = result["document_candidate"]
        for name, document in outputs.items():
            self.assertEqual(document["contract_version"], "document-v3.1", name)
            validate_document_v31(document)
            self.assertEqual(document["provenance"]["blueprint"], name)

    def test_writer_batches_cache_reuses_chapters(self) -> None:
        blueprint = DocumentBlueprint.from_dict(_blueprint_payload())
        fixture = json.loads((FIXTURES / "math_concept.json").read_text(encoding="utf-8"))
        units = [{
            "unit_id": "unit_0001", "type": "concept", "title": "原函数的定义",
            "definition_or_conclusion": "定义文本", "rules": [], "procedure": [], "pitfalls": [],
            "unresolved": [], "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00001"]},
        }]
        overlay = EvidenceCorrectionOverlay(version=1, transcript_digest=transcript_digest(fixture["transcript"]), corrections=[])
        first, fingerprints = write_chapters_in_batches(blueprint, units, overlay, [], max_batch_units=6)
        self.assertEqual(len(first), 1)
        second, _ = write_chapters_in_batches(blueprint, units, overlay, [], max_batch_units=6, cache=fingerprints)
        self.assertEqual(second, [])

    def test_checkpoint_resume_does_not_duplicate_tool_cost(self) -> None:
        from langgraph.checkpoint.sqlite import SqliteSaver
        turn = ToolTurn(content="", tool_calls=(
            ToolCallRecord(name="submit_blueprint", args={"blueprint": _blueprint_payload()}, tool_call_id="call_1"),
        ), model="replay", finish_reason="tool_calls")
        state = _fixture_state("tool_native")
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(f"{directory}/ckpt.sqlite3", check_same_thread=False)
            try:
                saver = SqliteSaver(connection)
                port = ScriptedToolPort([turn, ToolTurn(content="done", model="replay", finish_reason="stop")])
                tools_ctx = EditorialToolContext()
                graph = build_editorial_agent(
                    capability="tool_native", tool_port=port, tools_ctx=tools_ctx,
                    known_unit_ids={"plan_001"},
                    budget=build_stage_budget(max_tool_turns=8), max_revision_cycles=1,
                    checkpointer=saver,
                )
                config = {"configurable": {"thread_id": "t1"}}
                first = graph.invoke(state, config)
                self.assertEqual(first["terminal_status"], "succeeded")
                calls_after_first = port.calls
                # 从 END checkpoint 恢复（不提供新输入）：不重跑任何节点 → 云成本 0 新增
                checkpoint = saver.get_tuple(config)
                self.assertIsNotNone(checkpoint)
                checkpoint_id = checkpoint.checkpoint.get("id")
                self.assertIsNotNone(checkpoint_id)
                config_resume = {"configurable": {"thread_id": "t1", "checkpoint_id": checkpoint_id}}
                resumed = graph.invoke(None, config_resume)
                self.assertEqual(port.calls, calls_after_first)
                self.assertEqual(resumed["terminal_status"], "succeeded")
                self.assertNotIn("api_key", repr(checkpoint))
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
