from __future__ import annotations

from typing import Any, Mapping


CONTENT_PROFILES = {
    "精简": {"label": "复习提纲", "points": "每章通常 1–3 个知识点", "detail": "只保留最关键内容", "target_divisor": 2400, "target_min": 4, "target_max": 12},
    "推荐": {"label": "标准课程笔记", "points": "每章通常 2–6 个知识点", "detail": "保留必要解释、步骤、案例和边界", "target_divisor": 2200, "target_min": 8, "target_max": 18},
    "丰富": {"label": "完整课程讲义", "points": "每章通常 2–6 个知识点", "detail": "尽量保留推导、步骤、案例和复习提示", "target_divisor": 4000, "target_min": 8, "target_max": 10},
}


def content_profile(settings: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    level = str(settings.get("content_level", "推荐"))
    if level not in CONTENT_PROFILES:
        level = "推荐"
    return level, dict(CONTENT_PROFILES[level])
