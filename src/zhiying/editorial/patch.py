"""V6.1 Patch 应用：在 Document v3.1 组件树上执行白名单操作（CP61-4）。

操作白名单：add_component / remove_component / replace_component / move_component /
set_layout_hint / set_style_token。
禁止整篇替换、任意 JSON Pointer、docx_xml、文件路径与脚本。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from .document import COMPONENT_TYPES, make_component


def _find_parent(components: list[dict[str, Any]], component_id: str) -> tuple[list[dict[str, Any]], int] | None:
    """返回 (父列表, 索引)；找不到返回 None。"""
    for index, component in enumerate(components):
        if str(component.get("component_id", "")) == component_id:
            return components, index
    for component in components:
        children = component.get("children")
        if isinstance(children, list):
            found = _find_parent(children, component_id)
            if found is not None:
                return found
    return None


def _validate_component(component: Mapping[str, Any]) -> None:
    component_type = str(component.get("type", ""))
    if component_type not in COMPONENT_TYPES:
        raise ValueError(f"未知组件类型：{component_type}")
    if not str(component.get("component_id", "")).strip():
        raise ValueError("组件必须含 component_id")


def apply_patch(
    components: Iterable[Mapping[str, Any]],
    operations: Iterable[Mapping[str, Any]],
    *,
    current_revision: int,
) -> tuple[list[dict[str, Any]], int]:
    """应用单个 Patch 的修改操作，返回 (新组件树, 新 revision)。

    只允许一个修改操作（白名单）；revision 每次应用 +1。
    """
    tree = deepcopy([dict(component) for component in components])
    operations = list(operations)
    if len(operations) > 1:
        raise ValueError("单个 Patch 只允许一个修改操作")
    for operation in operations:
        op = str(operation.get("op", ""))
        component_id = str(operation.get("component_id", ""))
        if op == "add_component":
            component = dict(operation.get("component", {}))
            _validate_component(component)
            parent_id = str(operation.get("parent_id", ""))
            target = _find_parent(tree, parent_id) if parent_id else None
            if target is not None:
                target[0].insert(target[1] + 1, component)
            else:
                tree.append(component)
        elif op == "remove_component":
            found = _find_parent(tree, component_id)
            if found is None:
                raise ValueError(f"找不到组件：{component_id}")
            found[0].pop(found[1])
        elif op == "replace_component":
            replacement = dict(operation.get("component", {}))
            _validate_component(replacement)
            replacement["component_id"] = component_id
            found = _find_parent(tree, component_id)
            if found is None:
                raise ValueError(f"找不到组件：{component_id}")
            found[0][found[1]] = replacement
        elif op == "move_component":
            found = _find_parent(tree, component_id)
            if found is None:
                raise ValueError(f"找不到组件：{component_id}")
            target_id = str(operation.get("parent_id", ""))
            target = _find_parent(tree, target_id) if target_id else None
            if target is None:
                raise ValueError(f"找不到目标容器：{target_id}")
            component = found[0].pop(found[1])
            index = min(max(0, int(operation.get("index", 0))), len(target[0]))
            target[0].insert(index, component)
        elif op == "set_layout_hint":
            found = _find_parent(tree, component_id)
            if found is None:
                raise ValueError(f"找不到组件：{component_id}")
            found[0][found[1]]["layout_hint"] = str(operation.get("layout_hint", ""))
        elif op == "set_style_token":
            found = _find_parent(tree, component_id)
            if found is None:
                raise ValueError(f"找不到组件：{component_id}")
            found[0][found[1]]["style_token"] = str(operation.get("token", ""))
        else:
            raise ValueError(f"操作不在白名单：{op}")
    return tree, current_revision + 1


def patch_issue_scope(patch: Mapping[str, Any], issue_ids: Iterable[str]) -> bool:
    """Patch 的 issue_ids 必须 ⊆ 质量报告 issue id 集合。"""
    allowed = set(issue_ids)
    return all(str(issue) in allowed for issue in patch.get("issue_ids", []))


def new_component_for_patch(
    component_type: str,
    *,
    semantic_role: str,
    text: str = "",
    source_refs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成带稳定新 ID 的组件（供 add/replace 使用）。"""
    import hashlib
    digest = hashlib.sha256(f"{semantic_role}|{text}".encode("utf-8")).hexdigest()[:10]
    return make_component(
        component_type,
        component_id=f"patch_{digest}",
        semantic_role=semantic_role,
        text=text,
        source_refs=source_refs or {},
    )
