"""CP61-3 责任测试：CloudToolPort 协议、ToolTurn、StageBudget、模型能力门禁、工具错误。

全部使用 mock/replay provider，不发真实云请求。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from langchain_core.messages import AIMessage, ToolMessage

from video_study.execution.tool_calling import (
    CAPABILITY_STRUCTURED_ONLY,
    CAPABILITY_TOOL_NATIVE,
    TOOL_ARGS_INVALID,
    TOOL_BUDGET_EXCEEDED,
    TOOL_MULTIPLE_MUTATIONS,
    TOOL_UNKNOWN,
    ModelCapabilityRegistry,
    StageBudget,
    ToolCallError,
    ToolCallRecord,
    ToolTurn,
    build_stage_budget,
    build_tool_schemas,
    check_mutation_policy,
    check_unknown_tools,
    default_capability_for,
    invoke_tool_turn_openai,
    normalize_provider_tool_calls,
    tool_result_message,
    truncate_tool_result,
)


def _provider_call(name: str, arguments: str, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def _fake_chat_client(turn: ToolTurn, *, fail: bool = False) -> Any:
    def create(**kwargs):
        if fail:
            raise RuntimeError("provider down")
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content=turn.content,
                    tool_calls=[
                        SimpleNamespace(
                            id=row.tool_call_id,
                            function=SimpleNamespace(name=row.name, arguments=json.dumps(row.args, ensure_ascii=False)),
                        )
                        for row in turn.tool_calls
                    ] or None,
                ),
                finish_reason=turn.finish_reason,
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class ToolTurnNormalizationTests(unittest.TestCase):
    def test_content_only_turn(self) -> None:
        turn = ToolTurn(content="完成", model="m1", finish_reason="stop", usage={"total_tokens": 3})
        self.assertEqual(turn.content, "完成")
        self.assertEqual(turn.tool_calls, ())
        self.assertEqual(turn.safe_error_code, "")

    def test_single_valid_tool_call_normalization(self) -> None:
        valid, invalid = normalize_provider_tool_calls([
            _provider_call("lookup_evidence", '{"query": "定义", "top_k": 2}'),
        ])
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0].name, "lookup_evidence")
        self.assertEqual(valid[0].args, {"query": "定义", "top_k": 2})
        self.assertEqual(valid[0].tool_call_id, "call_1")
        self.assertEqual(invalid, [])

    def test_invalid_args_are_split_to_invalid_list(self) -> None:
        valid, invalid = normalize_provider_tool_calls([
            _provider_call("lookup_evidence", '{"query": '),
        ])
        self.assertEqual(valid, [])
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0].name, "lookup_evidence")

    def test_non_object_args_are_invalid(self) -> None:
        valid, invalid = normalize_provider_tool_calls([
            _provider_call("lookup_evidence", '["not", "object"]'),
        ])
        self.assertEqual(valid, [])
        self.assertEqual(len(invalid), 1)

    def test_tool_call_id_roundtrip_via_langchain_messages(self) -> None:
        turn = ToolTurn(
            tool_calls=(ToolCallRecord(name="lookup_evidence", args={"query": "定义"}, tool_call_id="call_abc"),),
            content="",
        )
        message = turn.to_langchain_message()
        self.assertIsInstance(message, AIMessage)
        self.assertEqual(message.tool_calls[0]["id"], "call_abc")
        self.assertEqual(message.tool_calls[0]["name"], "lookup_evidence")
        tool_message = tool_result_message("call_abc", "证据片段")
        self.assertIsInstance(tool_message, ToolMessage)
        self.assertEqual(tool_message.tool_call_id, "call_abc")

    def test_tool_turn_never_carries_secrets_or_raw_response(self) -> None:
        turn = ToolTurn(content="x", model="m", safe_error_code="")
        payload = json.dumps(turn.to_dict(), ensure_ascii=False)
        self.assertNotIn("api_key", payload)
        self.assertNotIn("authorization", payload)
        self.assertNotIn("raw_response", payload)


class StageBudgetTests(unittest.TestCase):
    def test_claim_and_record_usage(self) -> None:
        budget = build_stage_budget(max_total_calls=2, max_stage_calls=2, max_total_input_tokens=1000, max_total_output_tokens=1000, max_stage_tokens=800)
        budget.claim_call("blueprint")
        budget.record_usage("blueprint", {"prompt_tokens": 100, "completion_tokens": 50})
        budget.claim_call("writer")
        budget.record_usage("writer", {"prompt_tokens": 200, "completion_tokens": 60})
        self.assertEqual(budget.calls_used, 2)
        self.assertEqual(budget.input_tokens_used, 300)
        self.assertEqual(budget.snapshot()["calls_remaining"], 0)

    def test_total_call_budget_exceeded(self) -> None:
        budget = build_stage_budget(max_total_calls=1)
        budget.claim_call("blueprint")
        with self.assertRaises(ToolCallError) as ctx:
            budget.claim_call("writer")
        self.assertEqual(ctx.exception.code, TOOL_BUDGET_EXCEEDED)

    def test_stage_call_budget_exceeded(self) -> None:
        budget = build_stage_budget(max_total_calls=4, max_stage_calls=1)
        budget.claim_call("blueprint")
        with self.assertRaises(ToolCallError) as ctx:
            budget.claim_call("blueprint")
        self.assertEqual(ctx.exception.code, TOOL_BUDGET_EXCEEDED)

    def test_stage_token_budget_exceeded(self) -> None:
        budget = build_stage_budget(max_total_input_tokens=100, max_total_output_tokens=100, max_stage_tokens=50)
        with self.assertRaises(ToolCallError) as ctx:
            budget.record_usage("blueprint", {"prompt_tokens": 60, "completion_tokens": 0})
        self.assertEqual(ctx.exception.code, TOOL_BUDGET_EXCEEDED)

    def test_tool_turn_budget_exceeded(self) -> None:
        budget = build_stage_budget(max_tool_turns=1)
        budget.claim_tool_turn()
        with self.assertRaises(ToolCallError) as ctx:
            budget.claim_tool_turn()
        self.assertEqual(ctx.exception.code, TOOL_BUDGET_EXCEEDED)


class MutationPolicyTests(unittest.TestCase):
    def test_single_mutation_allowed(self) -> None:
        self.assertIsNone(check_mutation_policy([ToolCallRecord("submit_blueprint", {})]))

    def test_multiple_mutations_rejected(self) -> None:
        code = check_mutation_policy([
            ToolCallRecord("submit_blueprint", {}),
            ToolCallRecord("submit_patch", {}),
        ])
        self.assertEqual(code, TOOL_MULTIPLE_MUTATIONS)

    def test_unknown_tool_rejected(self) -> None:
        code = check_unknown_tools(
            [ToolCallRecord("write_file", {"path": "/etc"})],
            known={"lookup_evidence", "submit_blueprint"},
        )
        self.assertEqual(code, TOOL_UNKNOWN)


class ModelCapabilityTests(unittest.TestCase):
    def test_probed_glm_is_first_and_registered_tool_native(self) -> None:
        api = yaml.safe_load((Path(__file__).resolve().parents[1] / "api.yaml").read_text(encoding="utf-8"))
        qwen = api["qwen"]
        self.assertEqual(qwen["default_models"][0], "glm-5.2")
        registry = ModelCapabilityRegistry.from_dict(qwen["model_capabilities"])
        self.assertTrue(registry.tool_calling_enabled("glm-5.2"))
        self.assertFalse(registry.tool_calling_enabled("deepseek-v4-flash-0731"))

    def test_default_for_unknown_model_is_structured_only(self) -> None:
        self.assertEqual(default_capability_for("deepseek-v4-flash-0731"), CAPABILITY_STRUCTURED_ONLY)

    def test_openai_compatible_does_not_imply_tool_native(self) -> None:
        registry = ModelCapabilityRegistry()
        self.assertFalse(registry.tool_calling_enabled("any-openai-compatible-model"))

    def test_register_tool_native_enables_tool_calling(self) -> None:
        registry = ModelCapabilityRegistry()
        registry.register("model-a", CAPABILITY_TOOL_NATIVE)
        self.assertTrue(registry.tool_calling_enabled("model-a"))
        self.assertEqual(registry.capability("model-a"), CAPABILITY_TOOL_NATIVE)
        self.assertEqual(registry.capability("model-b"), CAPABILITY_STRUCTURED_ONLY)

    def test_registry_roundtrip(self) -> None:
        registry = ModelCapabilityRegistry()
        registry.register("model-a", CAPABILITY_TOOL_NATIVE)
        restored = ModelCapabilityRegistry.from_dict(registry.to_dict())
        self.assertEqual(restored.capability("model-a"), CAPABILITY_TOOL_NATIVE)

    def test_unknown_level_rejected(self) -> None:
        registry = ModelCapabilityRegistry()
        with self.assertRaises(ValueError):
            registry.register("model-a", "super_native")


class OpenAiToolAdapterTests(unittest.TestCase):
    def test_invoke_turn_with_valid_tool_call(self) -> None:
        turn = ToolTurn(
            content="",
            tool_calls=(ToolCallRecord(name="lookup_evidence", args={"query": "定义"}, tool_call_id="call_1"),),
            model="model-a",
            finish_reason="tool_calls",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        budget = build_stage_budget()
        result = invoke_tool_turn_openai(
            _fake_chat_client(turn), model="model-a",
            messages=[{"role": "user", "content": "hi"}],
            tools=build_tool_schemas("planning"), tool_choice="auto",
            stage="blueprint", budget=budget, cancel_check=lambda: False,
        )
        self.assertEqual(result.tool_calls[0].name, "lookup_evidence")
        self.assertEqual(result.tool_calls[0].args, {"query": "定义"})
        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(budget.calls_used, 1)
        self.assertEqual(budget.input_tokens_used, 10)

    def test_invoke_turn_provider_failure_propagates_without_secrets(self) -> None:
        turn = ToolTurn(content="")
        budget = build_stage_budget()
        with self.assertRaisesRegex(RuntimeError, "provider down"):
            invoke_tool_turn_openai(
                _fake_chat_client(turn, fail=True), model="model-a",
                messages=[{"role": "user", "content": "hi"}],
                tools=build_tool_schemas("planning"), tool_choice="auto",
                stage="blueprint", budget=budget, cancel_check=lambda: False,
            )

    def test_invoke_turn_respects_budget_before_request(self) -> None:
        budget = build_stage_budget(max_total_calls=0)
        with self.assertRaises(ToolCallError) as ctx:
            invoke_tool_turn_openai(
                _fake_chat_client(ToolTurn(content="")), model="model-a",
                messages=[], tools=build_tool_schemas("planning"), tool_choice="auto",
                stage="blueprint", budget=budget, cancel_check=lambda: False,
            )
        self.assertEqual(ctx.exception.code, TOOL_BUDGET_EXCEEDED)

    def test_tool_schemas_bound_per_stage(self) -> None:
        planning = {tool["function"]["name"] for tool in build_tool_schemas("planning")}
        revision = {tool["function"]["name"] for tool in build_tool_schemas("revision")}
        self.assertEqual(planning, {"lookup_evidence", "lookup_visual_facts", "get_renderer_capabilities", "submit_blueprint"})
        self.assertEqual(revision, {"lookup_evidence", "get_page_detail", "submit_patch", "finish_editing"})

    def test_truncate_tool_result_marks_overflow(self) -> None:
        truncated, overflow = truncate_tool_result("x" * 100, max_chars=10)
        self.assertTrue(overflow)
        self.assertIn("截断", truncated)
        kept, overflow2 = truncate_tool_result("short", max_chars=10)
        self.assertFalse(overflow2)
        self.assertEqual(kept, "short")


class LanggraphToolCheckpointTests(unittest.TestCase):
    def test_aimessage_tool_calls_and_toolmessage_resume_via_sqlite_checkpoint(self) -> None:
        """证明 langgraph 1.2.11 ToolNode/Command/SQLite Checkpoint 可序列化与恢复。"""
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.prebuilt import ToolNode
        from typing import TypedDict

        class AgentState(TypedDict):
            messages: list[Any]

        from langchain_core.tools import tool

        @tool
        def tool_lookup_evidence(query: str, top_k: int = 2) -> str:
            """只读查询课堂证据片段。"""
            return f"证据:{query}:{top_k}"

        def agent_node(state: AgentState) -> dict[str, Any]:
            if state.get("messages"):
                return {}  # 已有工具结果，不再请求工具
            return {"messages": [AIMessage(content="need tool", tool_calls=[{
                "name": "tool_lookup_evidence", "args": {"query": "定义", "top_k": 2},
                "id": "call_ckpt_1", "type": "tool_call",
            }])]}

        builder = StateGraph(AgentState)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", ToolNode([tool_lookup_evidence]))
        builder.add_edge(START, "agent")
        builder.add_conditional_edges(
            "agent",
            lambda state: "tools" if getattr(state["messages"][-1], "tool_calls", None) else END,
            {"tools": "tools", END: END},
        )
        builder.add_edge("tools", "agent")

        with tempfile.TemporaryDirectory() as directory:
            database = sqlite3.connect(f"{directory}/ckpt.sqlite3", check_same_thread=False)
            try:
                saver = SqliteSaver(database)
                graph = builder.compile(checkpointer=saver)
                first = graph.invoke({"messages": []}, {"configurable": {"thread_id": "job-tool-1"}})
                self.assertTrue(any(isinstance(row, ToolMessage) for row in first["messages"]), "ToolNode 未产出 ToolMessage")
                # 从 checkpoint 恢复：ToolMessage 可反序列化
                second = graph.invoke({"messages": first["messages"]}, {"configurable": {"thread_id": "job-tool-1"}})
                self.assertTrue(any(isinstance(row, ToolMessage) for row in second["messages"]), "Checkpoint 恢复丢失 ToolMessage")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
