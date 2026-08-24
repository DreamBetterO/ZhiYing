"""编辑意图加载器：读取、规范化和哈希用户的课程资料整理偏好。

负责模板加载、规范化、hash 和默认资源定位。
不解析自然语言为配置项，不做 YAML 表单。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BRIEF_FILENAME = "课程资料整理偏好.md"
MAX_BRIEF_CHARS = 4000

DEFAULT_BRIEF_TEXT = """\
# 课程资料整理偏好

希望最终文档形成适合系统学习和复习的正式课程资料。

优先提炼全课主线，再根据内容关系组织章节；如果课程明显按照演示、推导、案例或操作步骤逐步展开，可以保留老师讲课的时间顺序。

重点关注概念定义、判断条件、推导关系、操作步骤、案例结论、容易混淆的地方，以及老师反复强调或明确提醒的内容。

删除寒暄、口头语和无信息重复。详略由知识的重要程度、理解难度和课程强调程度决定，不平均分配篇幅。
"""

STRUCTURE_MODES = frozenset({
    "thematic_hierarchy",
    "lecture_timeline",
    "process",
    "case_driven",
    "hybrid",
})

SEQUENCE_POLICIES = frozenset({
    "reorganize",
    "preserve",
    "hybrid",
})


@dataclass(frozen=True)
class EditorialBrief:
    """规范化后的编辑意图。"""

    text: str
    sha256: str
    char_count: int
    is_default: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "char_count": self.char_count,
            "is_default": self.is_default,
        }


@dataclass(frozen=True)
class EditorialDecision:
    """规划阶段生成的可审计编辑决策。"""

    brief_sha256: str = ""
    structure_mode: str = "lecture_timeline"
    core_thread: str = ""
    focus_priorities: list[str] = field(default_factory=list)
    sequence_policy: str = "preserve"
    decision_reason: str = ""

    def __post_init__(self) -> None:
        if self.structure_mode not in STRUCTURE_MODES:
            object.__setattr__(self, "structure_mode", "hybrid")
        if self.sequence_policy not in SEQUENCE_POLICIES:
            object.__setattr__(self, "sequence_policy", "hybrid")
        object.__setattr__(
            self, "focus_priorities",
            [str(p) for p in self.focus_priorities if str(p).strip()],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_sha256": self.brief_sha256,
            "structure_mode": self.structure_mode,
            "core_thread": self.core_thread,
            "focus_priorities": list(self.focus_priorities),
            "sequence_policy": self.sequence_policy,
            "decision_reason": self.decision_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EditorialDecision:
        return cls(
            brief_sha256=str(data.get("brief_sha256", "")),
            structure_mode=str(data.get("structure_mode", "lecture_timeline")),
            core_thread=str(data.get("core_thread", "")),
            focus_priorities=list(data.get("focus_priorities", [])),
            sequence_policy=str(data.get("sequence_policy", "preserve")),
            decision_reason=str(data.get("decision_reason", "")),
        )


def _normalize(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("编辑意图不能为空")
    if len(stripped) > MAX_BRIEF_CHARS:
        raise ValueError(f"编辑意图超过 {MAX_BRIEF_CHARS} 字符上限（当前 {len(stripped)} 字符）")
    return stripped


def _compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_brief(brief_path: Path | None = None) -> EditorialBrief:
    """加载编辑意图。

    优先读取 brief_path 指向的用户文件；文件不存在时使用内置默认文本。
    """
    if brief_path is not None and brief_path.is_file():
        raw = brief_path.read_text(encoding="utf-8")
        text = _normalize(raw)
        return EditorialBrief(
            text=text,
            sha256=_compute_hash(text),
            char_count=len(text),
            is_default=(text == _normalize(DEFAULT_BRIEF_TEXT)),
        )
    text = _normalize(DEFAULT_BRIEF_TEXT)
    return EditorialBrief(
        text=text,
        sha256=_compute_hash(text),
        char_count=len(text),
        is_default=True,
    )


def brief_from_text(text: str, *, default: str | None = None) -> EditorialBrief:
    """从 UI/调用方传入的本次编辑意图构造 EditorialBrief，不落盘。"""
    normalized = _normalize(text)
    default_text = _normalize(default if default is not None else DEFAULT_BRIEF_TEXT)
    return EditorialBrief(
        text=normalized,
        sha256=_compute_hash(normalized),
        char_count=len(normalized),
        is_default=(normalized == default_text),
    )


def restore_default(brief_path: Path) -> None:
    """将默认偏好写入指定路径。"""
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(DEFAULT_BRIEF_TEXT, encoding="utf-8")


def default_decision(brief: EditorialBrief) -> EditorialDecision:
    """本地保守编辑决策，用于离线规划或云端规划失败时回退。"""
    return EditorialDecision(
        brief_sha256=brief.sha256,
        structure_mode="lecture_timeline",
        core_thread="",
        focus_priorities=[],
        sequence_policy="preserve",
        decision_reason="本地保守规划：保留来源时间顺序，不做主题重组",
    )
