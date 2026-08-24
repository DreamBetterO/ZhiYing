"""V6.1 EditorialAgentSubgraph 工具集（CP61-4）。

工具只读已授权 Artifact 的投影（evidence/visual/capability/page report），
提交类工具只写候选 State；确定性节点负责 validate/apply/commit。
禁止任意文件/Shell/网络/图片上传；返回有界、无秘密。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..execution.tool_calling import (
    TOOL_ARGS_INVALID,
    TOOL_MULTIPLE_MUTATIONS,
    TOOL_RESULT_TOO_LARGE,
    TOOL_REVISION_CONFLICT,
    TOOL_SCOPE_VIOLATION,
    ToolCallError,
    truncate_tool_result,
)

PATCH_OPERATION_WHITELIST = frozenset({
    "add_component", "remove_component", "replace_component", "move_component",
    "set_layout_hint", "set_style_token",
})

_MAX_RESULT_CHARS = 4000
_MAX_EVIDENCE_CHARS = 300


def _error_text(code: str, detail: str = "") -> str:
    return json.dumps({"tool_error": code, "detail": detail}, ensure_ascii=False)


def _ok_text(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False, default=str)


@dataclass
class EditorialToolContext:
    """工具可访问的授权投影（无秘密、无完整大对象、无绝对路径）。"""

    evidence_index: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # unit_id -> segments
    visual_facts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)   # unit_id -> facts
    renderer_capabilities: dict[str, Any] = field(default_factory=dict)
    page_report: dict[str, Any] | None = None

    # 候选 State（submit 工具只写这里，不写 Artifact）
    blueprint_candidate: dict[str, Any] | None = None
    patch_candidate: dict[str, Any] | None = None
    chapter_candidate: dict[str, Any] | None = None
    editing_finished: bool = False


class EditorialTools:
    """EditorialAgentSubgraph 的 langchain @tool 包装。"""

    def __init__(
        self,
        context: EditorialToolContext,
        *,
        max_result_chars: int = _MAX_RESULT_CHARS,
        mutation_allowed: bool = True,
    ) -> None:
        self.context = context
        self.max_result_chars = max_result_chars
        self.mutation_allowed = mutation_allowed

    # -- 只读观察工具 ------------------------------------------------------

    def lookup_evidence(self, query: str, unit_ids: list[str] | None = None, top_k: int = 3, include_neighbors: bool = False) -> str:
        """只读查询课堂证据片段（有界返回，不返回完整转写或本地路径）。"""
        top_k = max(1, min(5, int(top_k or 3)))
        rows: list[dict[str, Any]] = []
        for unit_id in unit_ids or sorted(self.context.evidence_index):
            for segment in self.context.evidence_index.get(unit_id, []):
                text = str(segment.get("text", ""))
                score = sum(text.count(term) for term in str(query).split()) if query else 1
                rows.append({
                    "unit_id": unit_id,
                    "source_id": str(segment.get("segment_id", "")),
                    "timestamp": float(segment.get("timestamp", 0.0)),
                    "text": text[:_MAX_EVIDENCE_CHARS],
                    "confidence": float(segment.get("confidence", 0.0)),
                    "correction_status": str(segment.get("correction_status", "raw")),
                })
        rows.sort(key=lambda row: row["confidence"], reverse=True)
        result, _overflow = truncate_tool_result(
            json.dumps(rows[:top_k], ensure_ascii=False), max_chars=self.max_result_chars,
        )
        return result

    def lookup_visual_facts(self, unit_ids: list[str], purpose: str = "explain", top_k: int = 4) -> str:
        """只读查询本地核实的视觉事实；不返回图片数据，不触发云图片上传。"""
        top_k = max(1, min(4, int(top_k or 4)))
        rows: list[dict[str, Any]] = []
        for unit_id in unit_ids:
            for fact in self.context.visual_facts.get(unit_id, []):
                if purpose and str(fact.get("purpose", "")) and str(fact.get("purpose")) != purpose:
                    continue
                rows.append({
                    "unit_id": unit_id,
                    "visual_id": str(fact.get("visual_id", "")),
                    "source_timestamp": float(fact.get("source_timestamp", 0.0)),
                    "visible_objects": list(fact.get("visible_objects", []))[:5],
                    "visible_text": str(fact.get("visible_text", ""))[:_MAX_EVIDENCE_CHARS],
                    "formula_candidates": list(fact.get("formula_candidates", []))[:3],
                    "confidence": float(fact.get("confidence", 0.0)),
                    "crop_suggestion_id": str(fact.get("crop_suggestion_id", "")),
                })
        return truncate_tool_result(json.dumps(rows[:top_k], ensure_ascii=False), max_chars=self.max_result_chars)[0]

    def get_renderer_capabilities(self, component_types: list[str] | None = None) -> str:
        """只读查询 Document v3.1 渲染能力与确定性 fallback。"""
        manifest = dict(self.context.renderer_capabilities)
        if component_types:
            allowed = set(component_types)
            manifest["components"] = [name for name in manifest.get("components", []) if name in allowed]
        return json.dumps(manifest, ensure_ascii=False)

    def get_page_detail(self, page_ids: list[str] | None = None, component_ids: list[str] | None = None) -> str:
        """只读获取已生成页面审计报告的组件定位（不重新渲染，不返回截图）。"""
        if not self.context.page_report:
            return _error_text(TOOL_SCOPE_VIOLATION, "页面审计报告尚未生成")
        report = dict(self.context.page_report)
        report["statistics"] = {key: value for key, value in report.get("statistics", {}).items()}
        return truncate_tool_result(json.dumps(report, ensure_ascii=False), max_chars=self.max_result_chars)[0]

    # -- 提交类工具（只写候选 State） --------------------------------------

    def submit_blueprint(self, blueprint: dict[str, Any]) -> str:
        """提交完整 DocumentBlueprint 候选（只写候选 State，由确定性节点校验提交）。"""
        if not self.mutation_allowed:
            return _error_text(TOOL_SCOPE_VIOLATION, "当前阶段不允许提交")
        if not isinstance(blueprint, dict) or not blueprint.get("blueprint_id"):
            return _error_text(TOOL_ARGS_INVALID, "Blueprint 必须含 blueprint_id")
        if "source_section" in json.dumps(blueprint, ensure_ascii=False):
            return _error_text(TOOL_SCOPE_VIOLATION, "Blueprint 不得包含 source_section 旧包装")
        self.context.blueprint_candidate = dict(blueprint)
        return _ok_text(accepted=True, blueprint_id=blueprint["blueprint_id"])

    def submit_chapter(self, chapter: dict[str, Any]) -> str:
        """Writer 强制结构化输出：提交单章组件候选（只写候选 State）。"""
        if not isinstance(chapter, dict) or not chapter.get("chapter_id"):
            return _error_text(TOOL_ARGS_INVALID, "章节必须含 chapter_id")
        self.context.chapter_candidate = dict(chapter)
        return _ok_text(accepted=True, chapter_id=chapter["chapter_id"])

    def submit_patch(self, base_revision: str, operations: list[dict[str, Any]], issue_ids: list[str], reason: str = "") -> str:
        """提交局部 Patch 候选（操作白名单 + base_revision 守卫）。"""
        if not self.mutation_allowed:
            return _error_text(TOOL_SCOPE_VIOLATION, "当前阶段不允许提交 Patch")
        if not base_revision or not issue_ids:
            return _error_text(TOOL_ARGS_INVALID, "Patch 必须含 base_revision 与 issue_ids")
        if not isinstance(operations, list) or not operations:
            return _error_text(TOOL_ARGS_INVALID, "Patch 至少一个操作")
        if len(operations) > 1:
            return _error_text(TOOL_MULTIPLE_MUTATIONS, "单个 Patch 只允许一个修改操作")
        operation = operations[0]
        op_name = str(operation.get("op", ""))
        if op_name not in PATCH_OPERATION_WHITELIST:
            return _error_text(TOOL_SCOPE_VIOLATION, f"操作不在白名单：{op_name}")
        if str(operation.get("component_id", "")) == "":
            return _error_text(TOOL_ARGS_INVALID, "操作缺少 component_id")
        self.context.patch_candidate = {
            "base_revision": str(base_revision),
            "operations": operations,
            "issue_ids": [str(item) for item in issue_ids],
            "reason": str(reason),
        }
        return _ok_text(accepted=True, revision=base_revision)

    def finish_editing(self, reason: str = "", handled_issue_ids: list[str] | None = None, remaining_limits: list[str] | None = None) -> str:
        """请求进入最终审计（审计失败仍可拒绝完成）。"""
        self.context.editing_finished = True
        return _ok_text(requested=True, reason=reason, handled_issue_ids=handled_issue_ids or [])


def build_langchain_tools(tools: EditorialTools, stage: str) -> list[Any]:
    """按阶段最小绑定 langchain @tool（只绑定该阶段工具）。"""
    from langchain_core.tools import tool

    bindings: dict[str, Callable[..., str]] = {}
    if stage in {"planning", "blueprint", "observation"}:
        bindings.update({
            "lookup_evidence": tools.lookup_evidence,
            "lookup_visual_facts": tools.lookup_visual_facts,
            "get_renderer_capabilities": tools.get_renderer_capabilities,
            "submit_blueprint": tools.submit_blueprint,
        })
    if stage in {"revision", "patch"}:
        bindings.update({
            "lookup_evidence": tools.lookup_evidence,
            "get_page_detail": tools.get_page_detail,
            "submit_patch": tools.submit_patch,
            "finish_editing": tools.finish_editing,
        })
    if stage == "writing":
        bindings["submit_chapter"] = tools.submit_chapter

    result = []
    for name, func in bindings.items():
        wrapped = tool(func)
        wrapped.name = name  # 保持稳定工具名
        result.append(wrapped)
    return result


def validate_patch(patch: Mapping[str, Any], *, current_revision: int) -> None:
    """Patch 校验：base_revision 守卫 + 操作白名单 + issue 范围。"""
    from ..execution.tool_calling import ToolCallError
    base = str(patch.get("base_revision", ""))
    if not base.startswith(f"rev-{current_revision}"):
        raise ToolCallError(TOOL_REVISION_CONFLICT, f"Patch base_revision 冲突：{base} != rev-{current_revision}")
    operations = patch.get("operations", [])
    if len(operations) > 1:
        raise ToolCallError(TOOL_MULTIPLE_MUTATIONS, "单个 Patch 只允许一个修改操作")
    for operation in operations:
        op = str(operation.get("op", ""))
        if op not in PATCH_OPERATION_WHITELIST:
            raise ToolCallError(TOOL_SCOPE_VIOLATION, f"操作不在白名单：{op}")
        if not str(operation.get("component_id", "")).strip():
            raise ToolCallError(TOOL_ARGS_INVALID, "操作缺少 component_id")
    if not patch.get("issue_ids"):
        raise ToolCallError(TOOL_ARGS_INVALID, "Patch 必须关联 issue_ids")
