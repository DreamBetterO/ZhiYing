"""V6.1 EditorialAgentSubgraph（CP61-4）：显式 LangGraph 子图 + 有限编辑循环。

宏观由 LangGraph 决定（阶段/预算/降级/重试/质量门/终态）；
LLM 只在受控编辑子图内决定语义与阶段内工具调用。
三条路径输出同一 Document v3.1 合同：
- tool_native：CloudToolPort.invoke_turn + ToolNode（主动观察 + submit 工具）；
- structured_only：CloudJsonPort 强制结构化 Blueprint/Chapter；
- local_deterministic：CP61-1 本地链（不构造 CloudPort）。
提交类工具只写候选 State；validate/apply/commit 为确定性节点。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, TypedDict

from ..execution.tool_calling import (
    CAPABILITY_LOCAL,
    CAPABILITY_STRUCTURED_ONLY,
    CAPABILITY_TOOL_NATIVE,
    TOOL_ARGS_INVALID,
    TOOL_NO_PROGRESS,
    TOOL_PROVIDER_UNSUPPORTED,
    ToolCallError,
    check_mutation_policy,
    check_unknown_tools,
    StageBudget,
    build_stage_budget,
    build_tool_schemas,
)
from .blueprint import DocumentBlueprint, validate_blueprint
from .evidence import EvidenceCorrectionOverlay
from .local import build_local_blueprint
from .patch import apply_patch, patch_issue_scope
from .quality import audit_document_v31, build_page_audit_report
from .tools import EditorialToolContext, EditorialTools, build_langchain_tools, validate_patch
from .writer import compose_chapter, write_chapters_in_batches


class EditorialState(TypedDict, total=False):
    # 有界小数据（大对象走 Artifact 引用；生产切换后输入改 refs）
    phase: str
    capability: str
    effective_capability: str
    degradation_reasons: list[str]
    error_codes: list[str]
    model_chain: list[str]
    usage: dict[str, int]
    messages: list[Any]
    tool_turns: int
    tool_failures: int
    same_error_feedback: int
    revision_cycles_used: int
    no_progress_count: int
    blueprint_candidate: dict | None
    accepted_blueprint: dict | None
    document_revision: int
    document_candidate: dict | None
    patch_candidate: dict | None
    page_report: dict | None
    quality_report: dict | None
    terminal_status: str | None
    provenance: dict
    # 输入（紧凑）
    editorial_policy: dict
    plan: dict
    plan_units: list
    evidence_overlay: dict
    visual_evidence: list
    metadata: dict
    transcript_digest: str


def _initial_state(
    *,
    capability: str,
    policy: dict,
    plan: dict,
    plan_units: list,
    evidence_overlay: dict,
    visual_evidence: list,
    metadata: dict,
    transcript_digest: str,
) -> EditorialState:
    return {
        "phase": "start",
        "capability": capability,
        "effective_capability": capability,
        "degradation_reasons": [],
        "error_codes": [],
        "model_chain": [],
        "usage": {},
        "messages": [],
        "tool_turns": 0,
        "tool_failures": 0,
        "same_error_feedback": 0,
        "revision_cycles_used": 0,
        "no_progress_count": 0,
        "blueprint_candidate": None,
        "accepted_blueprint": None,
        "document_revision": 0,
        "document_candidate": None,
        "page_report": None,
        "quality_report": None,
        "terminal_status": None,
        "provenance": {},
        "editorial_policy": policy,
        "plan": plan,
        "plan_units": plan_units,
        "evidence_overlay": evidence_overlay,
        "visual_evidence": visual_evidence,
        "metadata": metadata,
        "transcript_digest": transcript_digest,
    }


def build_editorial_agent(
    *,
    capability: str,
    tool_port: Any | None = None,
    json_port: Any | None = None,
    tools_ctx: EditorialToolContext,
    known_unit_ids: set[str],
    budget: StageBudget | None = None,
    max_tool_turns: int = 6,
    max_revision_cycles: int = 1,
    max_same_error_feedback: int = 1,
    cancel_check: Callable[[], bool] | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """构建 EditorialAgentSubgraph（LangGraph StateGraph）。"""
    from langgraph.graph import END, START, StateGraph

    budget = budget or build_stage_budget()
    cancel_check = cancel_check or (lambda: False)

    planning_tools = build_langchain_tools(EditorialTools(tools_ctx), "planning")
    revision_tools = build_langchain_tools(EditorialTools(tools_ctx), "revision")
    from langgraph.prebuilt import ToolNode
    # ToolNode 必须作为一等图节点（langgraph 注入 tools 配置并正确往返 tool_call_id）
    planning_tool_node = ToolNode(planning_tools)
    revision_tool_node = ToolNode(revision_tools)

    # ------------------------------------------------------------------ nodes

    def _trim_messages(messages: list[Any], limit: int = 16) -> list[Any]:
        return messages[-limit:]

    def blueprint_agent_turn(state: EditorialState) -> dict[str, Any]:
        if tool_port is None:
            return {"phase": "blueprint_structured"}
        messages = _trim_messages(state["messages"])
        try:
            turn = tool_port.invoke_turn(
                messages=[_to_dict_message(message) for message in messages],
                tools=build_tool_schemas("planning"),
                tool_choice="auto",
                stage="blueprint",
                budget=budget,
                cancel_check=cancel_check,
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, ToolCallError) else TOOL_PROVIDER_UNSUPPORTED
            return {
                "phase": "blueprint_structured",
                "error_codes": list(dict.fromkeys([*state.get("error_codes", []), code])),
                "degradation_reasons": [
                    *state.get("degradation_reasons", []),
                    f"{code}:blueprint_agent_turn:{type(exc).__name__}",
                ],
            }
        planning_names = {row["function"]["name"] for row in build_tool_schemas("planning")}
        protocol_error = (
            check_unknown_tools(turn.tool_calls, known=planning_names)
            or check_mutation_policy(turn.tool_calls)
        )
        if protocol_error:
            from langchain_core.messages import AIMessage
            updated = list(messages) + [AIMessage(content=f"{protocol_error}：请修正工具调用")]
        else:
            updated = list(messages) + [turn.to_langchain_message()]
        for invalid in turn.invalid_tool_calls:
            from langchain_core.messages import ToolMessage
            updated.append(ToolMessage(
                content=f"{TOOL_ARGS_INVALID}：工具参数非法（{invalid.name}）",
                tool_call_id=invalid.tool_call_id or "invalid",
            ))
        next_turn = state["tool_turns"] + 1
        no_progress = not turn.tool_calls and not turn.invalid_tool_calls
        error_codes = list(state.get("error_codes", []))
        degradation_reasons = list(state.get("degradation_reasons", []))
        if turn.invalid_tool_calls:
            error_codes.append(TOOL_ARGS_INVALID)
        if protocol_error:
            error_codes.append(protocol_error)
        if no_progress and next_turn >= max_tool_turns:
            error_codes.append(TOOL_NO_PROGRESS)
            degradation_reasons.append(f"{TOOL_NO_PROGRESS}:blueprint:{next_turn}")
        return {
            "messages": _trim_messages(updated),
            "tool_turns": next_turn,
            "tool_failures": state.get("tool_failures", 0) + (1 if turn.invalid_tool_calls or protocol_error else 0),
            "same_error_feedback": state["same_error_feedback"] + (1 if turn.invalid_tool_calls or protocol_error else 0),
            "no_progress_count": state.get("no_progress_count", 0) + (1 if no_progress else 0),
            "error_codes": list(dict.fromkeys(error_codes)),
            "degradation_reasons": list(dict.fromkeys(degradation_reasons)),
            "phase": "blueprint_agent_turn",
            "model_chain": list(dict.fromkeys([*state.get("model_chain", []), *([turn.model] if turn.model else [])])),
            "usage": _merge_usage(state.get("usage", {}), turn.usage),
        }

    def blueprint_tools(state: EditorialState) -> dict[str, Any]:
        # ToolNode 以一等图节点执行工具（见下方 add_node("blueprint_tools", planning_tool_node)）；
        # 本节点负责把工具写出的候选搬运进 State。
        candidate = tools_ctx.blueprint_candidate
        return {
            "phase": "blueprint_tools",
            "blueprint_candidate": dict(candidate) if candidate else state.get("blueprint_candidate"),
        }

    def blueprint_validate(state: EditorialState) -> dict[str, Any]:
        candidate = state.get("blueprint_candidate")
        if not candidate:
            return {"phase": "blueprint_structured"}
        try:
            validate_blueprint(DocumentBlueprint.from_dict(candidate), known_unit_ids=known_unit_ids)
            return {"phase": "blueprint_validated", "same_error_feedback": 0}
        except ValueError as exc:
            from langchain_core.messages import ToolMessage
            messages = _trim_messages(list(state["messages"]) + [
                ToolMessage(content=f"蓝图校验失败：{exc}", tool_call_id="validation"),
            ])
            same = state["same_error_feedback"] + 1
            if same > max_same_error_feedback:
                return {"messages": messages, "phase": "blueprint_structured"}
            return {"messages": messages, "phase": "blueprint_validate_retry", "same_error_feedback": same}

    def blueprint_structured(state: EditorialState) -> dict[str, Any]:
        from .policy import EditorialPolicy
        policy = EditorialPolicy.from_dict(state["editorial_policy"])
        if json_port is not None:
            from ..knowledge.schema import LessonPlan
            plan = LessonPlan.from_dict(state["plan"])
            try:
                candidate, info = _request_json_with_optional_info(
                    json_port,
                    _blueprint_request_payload(policy.to_dict(), plan.to_dict()),
                    validator=lambda value: _validate_blueprint_payload(value, known_unit_ids),
                    stage="blueprint",
                    cancel_check=cancel_check,
                )
                return {
                    "blueprint_candidate": dict(candidate), "phase": "blueprint_validated",
                    "effective_capability": CAPABILITY_STRUCTURED_ONLY,
                    "model_chain": list(dict.fromkeys([*state.get("model_chain", []), *([str(info.get("model", ""))] if info.get("model") else [])])),
                    "usage": _merge_usage(state.get("usage", {}), info.get("usage", {})),
                }
            except Exception as exc:
                reasons = list(state.get("degradation_reasons", []))
                reasons.append(f"blueprint_structured:{type(exc).__name__}")
                fallback = True
        else:
            fallback = False
        from ..knowledge.schema import LessonPlan
        plan = LessonPlan.from_dict(state["plan"])
        candidate = build_local_blueprint(plan, policy)
        return {
            "blueprint_candidate": candidate.to_dict(), "phase": "blueprint_validated",
            "effective_capability": CAPABILITY_LOCAL,
            "degradation_reasons": reasons if fallback else list(state.get("degradation_reasons", [])),
        }

    def blueprint_commit(state: EditorialState) -> dict[str, Any]:
        effective = state.get("effective_capability", state["capability"])
        return {
            "accepted_blueprint": dict(state.get("blueprint_candidate") or {}),
            "phase": "blueprint_commit",
            "provenance": {**state.get("provenance", {}), "blueprint": effective},
        }

    def chapter_write(state: EditorialState) -> dict[str, Any]:
        blueprint = DocumentBlueprint.from_dict(state["accepted_blueprint"])
        overlay = EvidenceCorrectionOverlay.from_dict(state["evidence_overlay"])
        chapters, _fingerprints = write_chapters_in_batches(
            blueprint,
            state["plan_units"],
            overlay,
            state["visual_evidence"],
            max_batch_units=budget.max_batch_units,
        )
        effective = state.get("effective_capability", state["capability"])
        chapter_capability = CAPABILITY_LOCAL
        reasons = list(state.get("degradation_reasons", []))
        if json_port is not None and effective in {CAPABILITY_TOOL_NATIVE, CAPABILITY_STRUCTURED_ONLY}:
            try:
                payload, info = _request_json_with_optional_info(
                    json_port,
                    _writer_request_payload(
                        state["editorial_policy"], state["accepted_blueprint"], chapters,
                    ),
                    validator=_validate_chapters_payload,
                    stage="writer",
                    cancel_check=cancel_check,
                )
                chapters = list(payload["chapters"])
                chapter_capability = CAPABILITY_STRUCTURED_ONLY
                model_chain = list(dict.fromkeys([*state.get("model_chain", []), *([str(info.get("model", ""))] if info.get("model") else [])]))
                usage = _merge_usage(state.get("usage", {}), info.get("usage", {}))
            except Exception as exc:
                reasons.append(f"writer_structured:{type(exc).__name__}")
                model_chain = list(state.get("model_chain", []))
                usage = dict(state.get("usage", {}))
        else:
            model_chain = list(state.get("model_chain", []))
            usage = dict(state.get("usage", {}))
        return {
            "document_candidate": {"components": chapters},
            "phase": "chapter_write",
            "degradation_reasons": reasons,
            "model_chain": model_chain,
            "usage": usage,
            "provenance": {
                **state.get("provenance", {}),
                "chapter_writing": {chapter["component_id"]: chapter_capability for chapter in chapters},
                "model_chain": model_chain,
                "usage": usage,
            },
        }

    def document_assemble(state: EditorialState) -> dict[str, Any]:
        from .document import build_v31_document
        chapters = list(state.get("document_candidate", {}).get("components", []))
        document = build_v31_document(
            metadata=dict(state["metadata"]),
            components=chapters,
            provenance=dict(state.get("provenance", {})),
        )
        return {"document_candidate": document, "phase": "document_assemble"}

    def preview_render(state: EditorialState) -> dict[str, Any]:
        document = state.get("document_candidate") or {}
        report = build_page_audit_report(document)
        return {"page_report": report, "phase": "preview_render"}

    def quality_audit(state: EditorialState) -> dict[str, Any]:
        from .policy import EditorialPolicy
        document = state.get("document_candidate") or {}
        policy = EditorialPolicy.from_dict(state["editorial_policy"])
        report = audit_document_v31(document, policy=policy)
        return {"quality_report": report, "phase": "quality_audit"}

    def revision_agent_turn(state: EditorialState) -> dict[str, Any]:
        if tool_port is None:
            return {"phase": "revision_local"}
        messages = _trim_messages(state["messages"])
        try:
            turn = tool_port.invoke_turn(
                messages=[_to_dict_message(message) for message in messages],
                tools=build_tool_schemas("revision"),
                tool_choice="auto",
                stage="revision",
                budget=budget,
                cancel_check=cancel_check,
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, ToolCallError) else TOOL_PROVIDER_UNSUPPORTED
            return {
                "phase": "editorial_finalize",
                "error_codes": list(dict.fromkeys([*state.get("error_codes", []), code])),
                "degradation_reasons": [
                    *state.get("degradation_reasons", []),
                    f"{code}:revision_agent_turn:{type(exc).__name__}",
                ],
            }
        updated = list(messages) + [turn.to_langchain_message()]
        return {
            "messages": _trim_messages(updated),
            "tool_turns": state["tool_turns"] + 1,
            "phase": "revision_agent_turn",
            "model_chain": list(dict.fromkeys([*state.get("model_chain", []), *([turn.model] if turn.model else [])])),
            "usage": _merge_usage(state.get("usage", {}), turn.usage),
        }

    def revision_tools(state: EditorialState) -> dict[str, Any]:
        # ToolNode 以一等图节点执行；这里把工具写出的候选搬运进 State
        candidate = dict(tools_ctx.patch_candidate) if tools_ctx.patch_candidate else None
        return {
            "phase": "revision_tools",
            "patch_candidate": candidate,
        }

    def patch_validate(state: EditorialState) -> dict[str, Any]:
        patch = state.get("patch_candidate")
        if not patch:
            return {"phase": "editorial_finalize"}
        issue_ids = [row.get("issue_id") or row.get("code") for row in (state.get("quality_report") or {}).get("issues", [])]
        if not patch_issue_scope(patch, [str(item) for item in issue_ids if item]):
            return {"phase": "editorial_finalize"}
        try:
            validate_patch(patch, current_revision=state["document_revision"])
            return {"phase": "patch_validated"}
        except Exception:
            return {"phase": "editorial_finalize"}

    def patch_apply(state: EditorialState) -> dict[str, Any]:
        document = dict(state.get("document_candidate") or {})
        components = list(document.get("components", []))
        patch = state.get("patch_candidate") or {}
        new_components, revision = apply_patch(
            components, patch.get("operations", []), current_revision=state["document_revision"],
        )
        document["components"] = new_components
        return {
            "document_candidate": document,
            "document_revision": revision,
            "revision_cycles_used": state["revision_cycles_used"] + 1,
            "phase": "patch_apply",
        }

    def editorial_finalize(state: EditorialState) -> dict[str, Any]:
        quality = state.get("quality_report") or {}
        status = "succeeded" if quality.get("status") == "valid" and not state.get("degradation_reasons") else "degraded"
        return {"terminal_status": status, "phase": "editorial_finalize"}

    # ------------------------------------------------------------------ edges
    graph = StateGraph(EditorialState)

    node_fns = {
        "blueprint_agent_turn": blueprint_agent_turn,
        "blueprint_validate": blueprint_validate,
        "blueprint_structured": blueprint_structured,
        "blueprint_commit": blueprint_commit,
        "chapter_write": chapter_write,
        "document_assemble": document_assemble,
        "preview_render": preview_render,
        "quality_audit": quality_audit,
        "revision_agent_turn": revision_agent_turn,
        "patch_validate": patch_validate,
        "patch_apply": patch_apply,
        "editorial_finalize": editorial_finalize,
    }
    for name, node_fn in node_fns.items():
        graph.add_node(name, node_fn)
    # ToolNode 作为一等图节点（langgraph 注入 tools 配置并正确往返 tool_call_id）
    graph.add_node("blueprint_tools", planning_tool_node)
    graph.add_node("revision_tools", revision_tool_node)
    # ToolNode 执行后由搬运节点把候选写入 State
    graph.add_edge("blueprint_tools", "blueprint_tools_carrier")
    graph.add_node("blueprint_tools_carrier", blueprint_tools)
    graph.add_edge("revision_tools", "revision_tools_carrier")
    graph.add_node("revision_tools_carrier", revision_tools)

    graph.add_edge(START, "blueprint_agent_turn" if capability == CAPABILITY_TOOL_NATIVE and tool_port else "blueprint_structured")
    graph.add_conditional_edges("blueprint_agent_turn", lambda state: _route_blueprint(state, max_tool_turns))
    graph.add_conditional_edges("blueprint_tools_carrier", lambda state: _route_blueprint_after_tools(state, max_tool_turns))
    graph.add_conditional_edges("blueprint_validate", lambda state: _route_validate(state))
    graph.add_edge("blueprint_structured", "blueprint_validate")
    graph.add_edge("blueprint_commit", "chapter_write")
    graph.add_edge("chapter_write", "document_assemble")
    graph.add_edge("document_assemble", "preview_render")
    graph.add_edge("preview_render", "quality_audit")
    graph.add_conditional_edges("quality_audit", lambda state: _route_quality(state, max_revision_cycles))
    graph.add_conditional_edges("revision_agent_turn", lambda state: _route_revision(state, max_tool_turns))
    graph.add_conditional_edges("revision_tools_carrier", lambda state: _route_revision_after_tools(state, tools_ctx))
    graph.add_conditional_edges(
        "patch_validate",
        lambda state: "patch_apply" if state.get("phase") == "patch_validated" else "editorial_finalize",
    )
    graph.add_edge("patch_apply", "preview_render")
    graph.add_edge("editorial_finalize", END)

    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 路由（显式条件边；LangGraph 决定阶段与终态）
# ---------------------------------------------------------------------------

def _route_blueprint(state: EditorialState, max_tool_turns: int) -> str:
    if state.get("phase") == "blueprint_structured":
        return "blueprint_structured"
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "blueprint_tools"
    if state["tool_turns"] >= max_tool_turns:
        return "blueprint_structured"
    return "blueprint_agent_turn"


def _route_blueprint_after_tools(state: EditorialState, max_tool_turns: int) -> str:
    if state.get("blueprint_candidate"):
        return "blueprint_validate"
    if state["tool_turns"] >= max_tool_turns:
        return "blueprint_structured"
    return "blueprint_agent_turn"


def _route_validate(state: EditorialState) -> str:
    if state.get("phase") == "blueprint_validated":
        return "blueprint_commit"
    if state.get("phase") == "blueprint_structured":
        # 校验失败且无重试额度或结构化输入非法 → 直接提交候选或本地重建
        return "blueprint_commit" if state.get("blueprint_candidate") else "blueprint_structured"
    if state.get("phase") == "blueprint_validate_retry":
        return "blueprint_agent_turn"
    return "blueprint_structured"


def _route_quality(state: EditorialState, max_revision_cycles: int) -> str:
    report = state.get("quality_report") or {}
    if report.get("status") == "valid":
        return "editorial_finalize"
    if state["revision_cycles_used"] < max_revision_cycles:
        return "revision_agent_turn"
    return "editorial_finalize"


def _route_revision(state: EditorialState, max_tool_turns: int) -> str:
    if state.get("phase") == "editorial_finalize":
        return "editorial_finalize"
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "revision_tools"
    if state["tool_turns"] >= max_tool_turns:
        return "editorial_finalize"
    return "revision_agent_turn"


def _route_revision_after_tools(state: EditorialState, tools_ctx: EditorialToolContext) -> str:
    if state.get("patch_candidate"):
        return "patch_validate"
    if tools_ctx.editing_finished:
        return "editorial_finalize"
    return "revision_agent_turn"


def _validate_blueprint_payload(value: Any, known_unit_ids: set[str]) -> Any:
    validate_blueprint(DocumentBlueprint.from_dict(value), known_unit_ids=known_unit_ids)
    return value


def _validate_chapters_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not isinstance(value.get("chapters"), list) or not value["chapters"]:
        raise ValueError("Writer 结果必须包含非空 chapters")
    for chapter in value["chapters"]:
        if not isinstance(chapter, Mapping) or chapter.get("type") != "container" or chapter.get("semantic_role") != "chapter":
            raise ValueError("Writer chapter 必须是 chapter container")
    return {"chapters": [dict(chapter) for chapter in value["chapters"]]}


def _request_json_with_optional_info(port: Any, payload: dict[str, Any], **kwargs) -> tuple[Any, dict[str, Any]]:
    if hasattr(port, "request_json_with_info"):
        return port.request_json_with_info(payload, **kwargs)
    return port.request_json(payload, **kwargs), {}


def _merge_usage(current: Mapping[str, Any], added: Mapping[str, Any]) -> dict[str, int]:
    keys = set(current) | set(added)
    return {key: int(current.get(key, 0) or 0) + int(added.get(key, 0) or 0) for key in keys}


def _blueprint_request_payload(policy: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    import json
    compact = {"editorial_policy": dict(policy), "lesson_plan": dict(plan)}
    return {
        "messages": [
            {"role": "system", "content": "你是受控课程文档规划器。只返回符合 DocumentBlueprint v2 的 JSON，不引用不存在的 unit_id，不输出旧 source_section。"},
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False, separators=(",", ":"))},
        ],
        "editorial_policy": dict(policy),
        "plan": dict(plan),
    }


def _writer_request_payload(policy: Mapping[str, Any], blueprint: Mapping[str, Any], chapters: list[dict[str, Any]]) -> dict[str, Any]:
    import json
    compact = {
        "editorial_policy": dict(policy), "blueprint": dict(blueprint),
        "draft_chapters": chapters,
    }
    return {
        "messages": [
            {"role": "system", "content": "你是受控课程章节编辑器。只返回 {\"chapters\":[...]}，保持 Document v3.1 chapter container、组件 ID 与来源引用。"},
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False, separators=(",", ":"))},
        ],
        **compact,
    }


def _to_dict_message(message: Any) -> dict[str, Any]:
    """把 langchain message 转 OpenAI 兼容 dict（有界）。"""
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

    def _tool_calls_payload(tool_calls: Any) -> list[dict[str, Any]]:
        result = []
        for call in tool_calls:
            if isinstance(call, dict):
                call = type("C", (), {"id": call.get("id", ""), "name": call.get("name", ""), "args": call.get("args", {})})()
            result.append({
                "id": str(getattr(call, "id", "") or ""),
                "type": "function",
                "function": {
                    "name": str(getattr(call, "name", "") or ""),
                    "arguments": __import__("json").dumps(getattr(call, "args", {}) or {}, ensure_ascii=False),
                },
            })
        return result

    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, AIMessage):
        payload: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if getattr(message, "tool_calls", None):
            payload["tool_calls"] = _tool_calls_payload(message.tool_calls)
        return payload
    if isinstance(message, ToolMessage):
        return {"role": "tool", "content": message.content, "tool_call_id": message.tool_call_id}
    if isinstance(message, Mapping):
        return dict(message)
    return {"role": "user", "content": str(message)}
