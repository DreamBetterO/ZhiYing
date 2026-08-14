"""编辑意图加载器测试：模板加载、默认资源、UTF-8、hash、大小限制。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_study.knowledge.editorial import (
    BRIEF_FILENAME,
    DEFAULT_BRIEF_TEXT,
    MAX_BRIEF_CHARS,
    EditorialBrief,
    EditorialDecision,
    default_decision,
    load_brief,
    restore_default,
)


class EditorialBriefTests(unittest.TestCase):
    def test_load_default_when_no_file(self) -> None:
        brief = load_brief(None)
        self.assertTrue(brief.is_default)
        self.assertTrue(brief.text)
        self.assertEqual(brief.char_count, len(brief.text))
        self.assertEqual(len(brief.sha256), 64)

    def test_load_default_when_path_not_exists(self) -> None:
        brief = load_brief(Path(tempfile.gettempdir()) / "nonexistent_brief.md")
        self.assertTrue(brief.is_default)
        self.assertEqual(brief.text, DEFAULT_BRIEF_TEXT.strip())

    def test_load_user_custom_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BRIEF_FILENAME
            path.write_text("只需要时间顺序的简要笔记。", encoding="utf-8")
            brief = load_brief(path)
            self.assertFalse(brief.is_default)
            self.assertEqual(brief.text, "只需要时间顺序的简要笔记。")
            self.assertEqual(len(brief.sha256), 64)

    def test_strips_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BRIEF_FILENAME
            path.write_text("\n\n  测试内容  \n\n", encoding="utf-8")
            brief = load_brief(path)
            self.assertEqual(brief.text, "测试内容")

    def test_rejects_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BRIEF_FILENAME
            path.write_text("   \n  \n  ", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_brief(path)

    def test_rejects_oversized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BRIEF_FILENAME
            path.write_text("x" * (MAX_BRIEF_CHARS + 1), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_brief(path)

    def test_restore_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BRIEF_FILENAME
            path.write_text("自定义内容", encoding="utf-8")
            restore_default(path)
            brief = load_brief(path)
            self.assertTrue(brief.is_default)

    def test_hash_is_deterministic(self) -> None:
        b1 = load_brief(None)
        b2 = load_brief(None)
        self.assertEqual(b1.sha256, b2.sha256)

    def test_hash_changes_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "a.md"
            p1.write_text("内容A", encoding="utf-8")
            p2 = Path(tmp) / "b.md"
            p2.write_text("内容B", encoding="utf-8")
            self.assertNotEqual(load_brief(p1).sha256, load_brief(p2).sha256)

    def test_to_dict_excludes_text(self) -> None:
        brief = load_brief(None)
        d = brief.to_dict()
        self.assertNotIn("text", d)
        self.assertIn("sha256", d)
        self.assertIn("char_count", d)
        self.assertIn("is_default", d)


class EditorialDecisionTests(unittest.TestCase):
    def test_default_decision_uses_brief_hash(self) -> None:
        brief = load_brief(None)
        decision = default_decision(brief)
        self.assertEqual(decision.brief_sha256, brief.sha256)
        self.assertEqual(decision.structure_mode, "lecture_timeline")
        self.assertEqual(decision.sequence_policy, "preserve")

    def test_from_dict_normalizes_invalid_values(self) -> None:
        decision = EditorialDecision.from_dict({
            "brief_sha256": "abc",
            "structure_mode": "invalid_mode",
            "sequence_policy": "invalid_policy",
        })
        self.assertEqual(decision.structure_mode, "hybrid")
        self.assertEqual(decision.sequence_policy, "hybrid")

    def test_to_dict_roundtrip(self) -> None:
        original = EditorialDecision(
            brief_sha256="abc123",
            structure_mode="thematic_hierarchy",
            core_thread="主线",
            focus_priorities=["概念", "步骤"],
            sequence_policy="reorganize",
            decision_reason="主题重组更适合",
        )
        restored = EditorialDecision.from_dict(original.to_dict())
        self.assertEqual(restored.brief_sha256, "abc123")
        self.assertEqual(restored.structure_mode, "thematic_hierarchy")
        self.assertEqual(restored.focus_priorities, ["概念", "步骤"])

    def test_focus_priorities_filtered(self) -> None:
        decision = EditorialDecision(
            focus_priorities=["有效", "", "  ", "也有效"],
        )
        self.assertEqual(decision.focus_priorities, ["有效", "也有效"])


if __name__ == "__main__":
    unittest.main()
