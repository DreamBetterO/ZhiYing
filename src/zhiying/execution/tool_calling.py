"""V6.1 CloudToolPort 协议层（CP61-3）：ToolTurn、StageBudget、模型能力门禁、工具错误规范。

- CloudToolPort（ports.py）以 invoke_turn 调用，返回 ToolTurn；
- ToolTurn 只含 content/tool_calls/invalid_tool_calls/model/attempts/usage/finish_reason/safe_error_code，
  不含请求头、密钥或完整供应商响应；
- 供应商 tool_calls 规范化为 AIMessage.tool_calls，工具结果使用 ToolMessage（langchain-core）；
- StageBudget 同时限制调用数、Token、工具轮次、返回大小与修订保留；
- 工具错误转稳定错误码，相同错误最多一次修正机会（由上层图控制）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# 工具错误码（目标合同 tool_errors）
# ---------------------------------------------------------------------------
TOOL_UNKNOWN = "TOOL_UNKNOWN"
TOOL_ARGS_INVALID = "TOOL_ARGS_INVALID"
TOOL_SCOPE_VIOLATION = "TOOL_SCOPE_VIOLATION"
TOOL_RESULT_TOO_LARGE = "TOOL_RESULT_TOO_LARGE"
TOOL_MULTIPLE_MUTATIONS = "TOOL_MULTIPLE_MUTATIONS"
TOOL_REVISION_CONFLICT = "TOOL_REVISION_CONFLICT"
TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
TOOL_PROVIDER_UNSUPPORTED = "TOOL_PROVIDER_UNSUPPORTED"
TOOL_NO_PROGRESS = "TOOL_NO_PROGRESS"
TOOL_CHECKPOINT_SERIALIZATION_FAILED = "TOOL_CHECKPOINT_SERIALIZATION_FAILED"

TOOL_ERROR_CODES = frozenset({
    TOOL_UNKNOWN, TOOL_ARGS_INVALID, TOOL_SCOPE_VIOLATION, TOOL_RESULT_TOO_LARGE,
    TOOL_MULTIPLE_MUTATIONS, TOOL_REVISION_CONFLICT, TOOL_BUDGET_EXCEEDED,
    TOOL_PROVIDER_UNSUPPORTED, TOOL_NO_PROGRESS, TOOL_CHECKPOINT_SERIALIZATION_FAILED,
})


class ToolCallError(RuntimeError):
    """带稳定错误码的工具错误。"""

    def __init__(self, code: str, message: str = ""):
        if code not in TOOL_ERROR_CODES:
            raise ValueError(f"未知工具错误码：{code}")
        super().__init__(message or code)
        self.code = code


# ---------------------------------------------------------------------------
# 模型能力门禁
# ---------------------------------------------------------------------------
CAPABILITY_UNKNOWN = "unknown"
CAPABILITY_TOOL_NATIVE = "tool_native"
CAPABILITY_STRUCTURED_ONLY = "structured_only"
CAPABILITY_LOCAL = "local_deterministic"
CAPABILITY_UNAVAILABLE = "unavailable"

CAPABILITY_LEVELS = frozenset({
    CAPABILITY_UNKNOWN, CAPABILITY_TOOL_NATIVE, CAPABILITY_STRUCTURED_ONLY, CAPABILITY_UNAVAILABLE,
})


def default_capability_for(_model: str) -> str:
    """未知模型默认 structured_only；OpenAI 兼容接口不自动等于 tool_native。"""
    return CAPABILITY_STRUCTURED_ONLY


class ModelCapabilityRegistry:
    """模型能力注册表（按模型记录，版本化可缓存）。"""

    def __init__(self) -> None:
        self._levels: dict[str, str] = {}

    def register(self, model: str, level: str) -> None:
        if level not in CAPABILITY_LEVELS:
            raise ValueError(f"未知能力级别：{level}")
        self._levels[model] = level

    def capability(self, model: str) -> str:
        return self._levels.get(model, default_capability_for(model))

    def tool_calling_enabled(self, model: str) -> bool:
        return self.capability(model) == CAPABILITY_TOOL_NATIVE

    def to_dict(self) -> dict[str, str]:
        return dict(self._levels)

    @classmethod
    def from_dict(cls, data: Mapping[str, str]) -> "ModelCapabilityRegistry":
        registry = cls()
        for model, level in data.items():
            registry.register(str(model), str(level))
        return registry


# ---------------------------------------------------------------------------
# ToolTurn / ToolCallRecord
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolCallRecord:
    """规范化后的单次工具调用。"""

    name: str
    args: dict[str, Any]
    tool_call_id: str = ""
    raw_args: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": dict(self.args),
            "tool_call_id": self.tool_call_id,
            "raw_args": self.raw_args,
        }


@dataclass(frozen=True)
class ToolTurn:
    """单轮工具调用结果；不含密钥/请求头/完整供应商响应。"""

    content: str = ""
    tool_calls: tuple[ToolCallRecord, ...] = ()
    invalid_tool_calls: tuple[ToolCallRecord, ...] = ()
    model: str = ""
    attempts: tuple[Any, ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)
    finish_reason: str = ""
    safe_error_code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "invalid_tool_calls", tuple(self.invalid_tool_calls))
        object.__setattr__(self, "attempts", tuple(self.attempts))
        object.__setattr__(self, "usage", dict(self.usage))
        if self.safe_error_code and self.safe_error_code not in TOOL_ERROR_CODES:
            object.__setattr__(self, "safe_error_code", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [row.to_dict() for row in self.tool_calls],
            "invalid_tool_calls": [row.to_dict() for row in self.invalid_tool_calls],
            "model": self.model,
            "usage": dict(self.usage),
            "finish_reason": self.finish_reason,
            "safe_error_code": self.safe_error_code,
        }

    def to_langchain_message(self) -> Any:
        """转换为 langchain-core AIMessage（tool_calls 规范化）。"""
        from langchain_core.messages import AIMessage
        return AIMessage(
            content=self.content,
            tool_calls=[
                {
                    "name": row.name,
                    "args": dict(row.args),
                    "id": row.tool_call_id,
                    "type": "tool_call",
                }
                for row in self.tool_calls
            ],
        )


def tool_result_message(tool_call_id: str, result: str, *, max_chars: int = 4000) -> Any:
    """把工具结果转换为有界 ToolMessage（超长截断并标记）。"""
    from langchain_core.messages import ToolMessage
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n…[截断，原长度 {len(result)}]"
    return ToolMessage(content=result, tool_call_id=tool_call_id)


def normalize_provider_tool_calls(provider_calls: Iterable[Any]) -> tuple[list[ToolCallRecord], list[ToolCallRecord]]:
    """把供应商 tool_calls 规范化为有效/无效两列表。

    provider_calls 元素具有 .id/.function.name/.function.arguments（OpenAI 兼容形状）。
    """
    valid: list[ToolCallRecord] = []
    invalid: list[ToolCallRecord] = []
    for call in provider_calls:
        name = str(getattr(getattr(call, "function", None), "name", "") or "")
        raw_args = str(getattr(getattr(call, "function", None), "arguments", "") or "")
        call_id = str(getattr(call, "id", "") or "")
        try:
            parsed = json.loads(raw_args) if raw_args.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError("工具参数必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError):
            invalid.append(ToolCallRecord(name=name, args={}, tool_call_id=call_id, raw_args=raw_args))
            continue
        valid.append(ToolCallRecord(name=name, args=parsed, tool_call_id=call_id, raw_args=raw_args))
    return valid, invalid


# ---------------------------------------------------------------------------
# StageBudget
# ---------------------------------------------------------------------------

@dataclass
class StageBudget:
    """阶段预算：同时限制调用、Token、工具轮次、返回大小、批次与修订保留。"""

    max_total_calls: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_stage_calls: int
    max_stage_tokens: int
    max_tool_turns: int
    max_tool_result_chars: int
    max_batch_units: int
    repair_reserve: int

    calls_used: int = 0
    tool_turns_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    stage_calls_used: dict[str, int] = field(default_factory=dict)
    stage_tokens_used: dict[str, int] = field(default_factory=dict)

    def claim_call(self, stage: str) -> None:
        if self.calls_used >= self.max_total_calls:
            raise ToolCallError(TOOL_BUDGET_EXCEEDED, f"总调用预算用尽（{self.calls_used}/{self.max_total_calls}）")
        stage_used = self.stage_calls_used.get(stage, 0)
        if stage_used >= self.max_stage_calls:
            raise ToolCallError(TOOL_BUDGET_EXCEEDED, f"阶段 {stage} 调用预算用尽（{stage_used}/{self.max_stage_calls}）")
        self.calls_used += 1
        self.stage_calls_used[stage] = stage_used + 1

    def claim_tool_turn(self) -> None:
        if self.tool_turns_used >= self.max_tool_turns:
            raise ToolCallError(TOOL_BUDGET_EXCEEDED, f"工具轮次预算用尽（{self.tool_turns_used}/{self.max_tool_turns}）")
        self.tool_turns_used += 1

    def record_usage(self, stage: str, usage: Mapping[str, int]) -> None:
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens
        if self.input_tokens_used > self.max_total_input_tokens or self.output_tokens_used > self.max_total_output_tokens:
            raise ToolCallError(TOOL_BUDGET_EXCEEDED, "Token 预算用尽")
        self.stage_tokens_used[stage] = self.stage_tokens_used.get(stage, 0) + input_tokens + output_tokens
        stage_total = self.stage_tokens_used[stage]
        if stage_total > self.max_stage_tokens:
            raise ToolCallError(TOOL_BUDGET_EXCEEDED, f"阶段 {stage} Token 预算用尽")

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls_used": self.calls_used,
            "calls_remaining": max(0, self.max_total_calls - self.calls_used),
            "tool_turns_used": self.tool_turns_used,
            "input_tokens_used": self.input_tokens_used,
            "output_tokens_used": self.output_tokens_used,
            "repair_reserve": self.repair_reserve,
            "stage_calls_used": dict(self.stage_calls_used),
            "stage_tokens_used": dict(self.stage_tokens_used),
        }


def build_stage_budget(
    *,
    max_total_calls: int = 4,
    max_total_input_tokens: int = 120_000,
    max_total_output_tokens: int = 40_000,
    max_stage_calls: int = 2,
    max_stage_tokens: int = 60_000,
    max_tool_turns: int = 6,
    max_tool_result_chars: int = 4000,
    max_batch_units: int = 6,
    repair_reserve: int = 1,
) -> StageBudget:
    return StageBudget(
        max_total_calls=max_total_calls,
        max_total_input_tokens=max_total_input_tokens,
        max_total_output_tokens=max_total_output_tokens,
        max_stage_calls=max_stage_calls,
        max_stage_tokens=max_stage_tokens,
        max_tool_turns=max_tool_turns,
        max_tool_result_chars=max_tool_result_chars,
        max_batch_units=max_batch_units,
        repair_reserve=repair_reserve,
    )


# ---------------------------------------------------------------------------
# 工具错误 / 变更策略 / Schema 绑定
# ---------------------------------------------------------------------------

# 修改型工具（同一调用只允许一个）
MUTATING_TOOLS = frozenset({"submit_blueprint", "submit_chapter", "submit_patch", "finish_editing"})

# 只读观察工具
READ_ONLY_TOOLS = frozenset({"lookup_evidence", "lookup_visual_facts", "get_renderer_capabilities", "get_page_detail"})


def check_mutation_policy(tool_calls: Sequence[ToolCallRecord]) -> str | None:
    """同一调用只允许一个修改型工具；违反返回 TOOL_MULTIPLE_MUTATIONS。"""
    mutations = [row.name for row in tool_calls if row.name in MUTATING_TOOLS]
    if len(mutations) > 1:
        return TOOL_MULTIPLE_MUTATIONS
    return None


def check_unknown_tools(tool_calls: Sequence[ToolCallRecord], known: Iterable[str]) -> str | None:
    known_set = set(known)
    for row in tool_calls:
        if row.name not in known_set:
            return TOOL_UNKNOWN
    return None


def build_tool_schemas(stage: str) -> list[dict[str, Any]]:
    """按阶段最小绑定工具 schema（不每轮发送全部工具）。"""
    planning = [
        {
            "type": "function",
            "function": {
                "name": "lookup_evidence",
                "description": "只读查询课堂证据片段（有界返回，不返回完整转写或本地路径）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit_ids": {"type": "array", "items": {"type": "string"}},
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "maximum": 5},
                        "include_neighbors": {"type": "boolean"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lookup_visual_facts",
                "description": "只读查询本地核实的视觉事实；不返回图片数据，不触发云图片上传。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit_ids": {"type": "array", "items": {"type": "string"}},
                        "purpose": {"type": "string", "enum": ["explain", "evidence", "compare", "recap", "formula"]},
                        "top_k": {"type": "integer", "maximum": 4},
                    },
                    "required": ["unit_ids"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_renderer_capabilities",
                "description": "只读查询 Document v3.1 渲染能力与确定性 fallback。",
                "parameters": {
                    "type": "object",
                    "properties": {"component_types": {"type": "array", "items": {"type": "string"}}},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_blueprint",
                "description": "提交完整 DocumentBlueprint 候选（只写候选 State，由确定性节点校验提交）。",
                "parameters": {
                    "type": "object",
                    "properties": {"blueprint": {"type": "object"}},
                    "required": ["blueprint"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    revision = [
        {
            "type": "function",
            "function": {
                "name": "lookup_evidence",
                "description": "只读查询质量报告涉及的 unit/component 证据。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "unit_ids": {"type": "array", "items": {"type": "string"}},
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "maximum": 5},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_page_detail",
                "description": "只读获取已生成页面审计报告的组件定位（不重新渲染，不返回截图）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_ids": {"type": "array", "items": {"type": "string"}},
                        "component_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_patch",
                "description": "提交局部 Patch 候选（操作白名单：add/remove/replace/move_component/set_layout_hint/set_style_token）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "base_revision": {"type": "string"},
                        "operations": {"type": "array", "items": {"type": "object"}},
                        "issue_ids": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                    "required": ["base_revision", "operations", "issue_ids"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish_editing",
                "description": "请求进入最终审计（审计失败仍可拒绝完成）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "handled_issue_ids": {"type": "array", "items": {"type": "string"}},
                        "remaining_limits": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if stage == "revision":
        return revision
    return planning


def truncate_tool_result(result: str, *, max_chars: int) -> tuple[str, bool]:
    """有界工具返回：超长截断并标记（TOOL_RESULT_TOO_LARGE 由上层反馈）。"""
    if len(result) <= max_chars:
        return result, False
    return result[:max_chars] + f"\n…[截断，原长度 {len(result)}]", True


# ---------------------------------------------------------------------------
# OpenAI 兼容 tool 调用（CloudToolPort 的 provider adapter）
# ---------------------------------------------------------------------------

def invoke_tool_turn_openai(
    client: Any,
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    tool_choice: Any,
    stage: str,
    budget: StageBudget,
    cancel_check: Callable[[], bool],
    max_tokens: int = 2000,
    temperature: float = 0.1,
) -> ToolTurn:
    """调用 OpenAI 兼容 chat.completions 并规范化为 ToolTurn。

    供应商 tool_calls 规范化由调用方使用 normalize_provider_tool_calls 处理；
    本函数返回的 ToolTurn 不含密钥/请求头/完整响应。
    """
    from ..providers import CloudAttemptInfo, RESULT_SUCCESS

    budget.claim_call(stage)
    budget.claim_tool_turn()
    payload = {
        "model": model,
        "messages": [dict(message) for message in messages],
        "tools": [dict(tool) for tool in tools],
        "tool_choice": tool_choice,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    response = client.chat.completions.create(**payload)
    choice = response.choices[0] if response.choices else None
    message = getattr(choice, "message", None)
    finish_reason = str(getattr(choice, "finish_reason", "") or "")
    content = str(getattr(message, "content", "") or "")
    usage = {
        "prompt_tokens": int(getattr(response, "usage", None).prompt_tokens or 0) if getattr(response, "usage", None) else 0,
        "completion_tokens": int(getattr(response, "usage", None).completion_tokens or 0) if getattr(response, "usage", None) else 0,
        "total_tokens": int(getattr(response, "usage", None).total_tokens or 0) if getattr(response, "usage", None) else 0,
    }
    budget.record_usage(stage, usage)
    attempt = CloudAttemptInfo(model=model, ok=True, result_type=RESULT_SUCCESS, finish_reason=finish_reason)
    valid, invalid = normalize_provider_tool_calls(getattr(message, "tool_calls", []) or [])
    return ToolTurn(
        content=content,
        tool_calls=tuple(valid),
        invalid_tool_calls=tuple(invalid),
        model=model,
        attempts=(attempt,),
        usage=usage,
        finish_reason=finish_reason,
        safe_error_code=TOOL_ARGS_INVALID if invalid else "",
    )
