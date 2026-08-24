"""V6.1 LocalIntentCompiler：把用户自然语言偏好编译为 EditorialPolicy。

覆盖目标合同 editorial_policy.local_minimum_intents 的最小集合；
无法本地解析的复杂意图记录 unmatched_hints 并标记 partially_satisfied。
本地编译不发起任何云端请求。
"""
from __future__ import annotations

import re
from typing import Any

from .policy import EditorialConstraint, EditorialPolicy

# 极性前缀：forbidden / required / preferred（按顺序匹配）
_NEG_PREFIX = r"(?:不要|不需要|别要|禁止|去掉|移除|不设|不加|取消|没有)"
_POS_PREFIX = r"(?:需要|要有|要|保留|加上|加入|设置)"

_LEGACY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("overview", ("内容导览", "导览")),
    ("learning_objectives", ("学习目标", "教学目标")),
    ("review", ("课程复习", "课程回顾", "复习栏", "回顾")),
    ("fixed_numbering", ("固定章节编号", "章节编号", "固定编号", "编号")),
]

_STRUCTURE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("case_solution", ("例题加思路", "例题和思路", "例题讲解", "例题", "列题", "解题思路", "题加思路")),
    ("concept_properties", ("定义和性质", "定义与性质", "概念和性质", "概念定义", "定义与性质")),
    ("procedure", ("操作步骤", "步骤讲解", "步骤", "流程")),
    ("comparison", ("对比", "区别", "差异", "比较")),
]

_DENSITY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("concise", ("精简", "简洁", "压缩", "简短")),
    ("recommended", ("推荐", "适中")),
    ("rich", ("丰富", "详细", "详尽", "扩充", "完整讲义")),
]

_VISUAL_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("image_less", ("少放图", "图片少", "少图", "少放图片", "图少")),
    ("image_more", ("多放图", "图片多", "多图", "图片多一些", "多配图")),
    ("image_forbidden", ("不要图片", "禁止图片", "不放图", "禁图", "不要配图", "不配图")),
]

_FORMULA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("formula_priority", ("公式优先", "保留公式", "公式为主")),
]

_TABLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("table_priority", ("表格优先", "多用表格", "表格为主")),
]

_SOURCE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("show", ("显示来源", "标注来源", "来源链接", "来源显示", "保留来源")),
]

# 超出本地最小集合的意图线索（命中即部分满足）
_UNSUPPORTED_TERMS: tuple[str, ...] = (
    "风格", "排版", "双栏", "三栏", "封面", "页眉", "页脚", "字号", "字体",
    "名言", "引言", "配色", "装帧", "交互", "模板", "讲义样式",
)


def _add_legacy_constraint(policy: EditorialPolicy, text: str, code: str, keywords: tuple[str, ...]) -> None:
    for keyword in keywords:
        negative = re.search(rf"{_NEG_PREFIX}\s*{re.escape(keyword)}", text)
        positive = re.search(rf"{_POS_PREFIX}\s*{re.escape(keyword)}", text)
        if negative:
            policy.forbidden.append(EditorialConstraint(
                kind="forbidden", code=code, source_span=negative.group(0),
                predicate=f"组件树不得出现角色 {code}", reason="用户明确禁止",
            ))
            return
        if positive:
            policy.required.append(EditorialConstraint(
                kind="required", code=code, source_span=positive.group(0),
                predicate=f"组件树必须出现角色 {code}", reason="用户明确要求",
            ))
            return
        if keyword in text:
            policy.preferred.append(EditorialConstraint(
                kind="preferred", code=code, source_span=keyword,
                predicate=f"尽量出现角色 {code}", reason="用户提及（未表明确切极性）",
            ))
            return


def _first_hit(text: str, rules: list[tuple[str, tuple[str, ...]]]) -> str:
    for code, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return code
    return ""


def compile_editorial_policy(brief: Any) -> EditorialPolicy:
    """把编辑意图编译为 EditorialPolicy（本地、无云端）。"""
    text = brief.text if hasattr(brief, "text") else str(brief)
    policy = EditorialPolicy()

    for code, keywords in _LEGACY_RULES:
        _add_legacy_constraint(policy, text, code, keywords)

    policy.structure_preference = _first_hit(text, _STRUCTURE_RULES)
    policy.density = _first_hit(text, _DENSITY_RULES)
    policy.visual_preference = _first_hit(text, _VISUAL_RULES)
    policy.formula_preference = _first_hit(text, _FORMULA_RULES)
    policy.table_preference = _first_hit(text, _TABLE_RULES)
    policy.source_display = _first_hit(text, _SOURCE_RULES)

    focus_parts = [
        part for part in (
            policy.structure_preference,
            policy.density,
            policy.visual_preference,
            policy.formula_preference or policy.table_preference,
        ) if part
    ]
    policy.content_focus = "、".join(focus_parts)

    for term in _UNSUPPORTED_TERMS:
        if term in text:
            policy.unmatched_hints.append(f"无法本地解析的意图：{term}")
    if policy.unmatched_hints:
        policy.status = "partially_satisfied"
    return policy
