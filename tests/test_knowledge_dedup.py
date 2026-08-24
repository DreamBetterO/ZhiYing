from __future__ import annotations

import unittest

from zhiying.knowledge.course_ir import (
    build_course_ir,
    build_source_blocks,
    course_ir_to_units,
    route_claims_to_blocks,
)
from zhiying.knowledge.dedup import run_dedup_gate, validate_title
from zhiying.knowledge.schema import (
    ChapterPlan,
    Claim,
    CourseIR,
    KnowledgeUnit,
    LessonPlan,
    UnitPlan,
)
from zhiying.knowledge.source_blocks import build_cloud_source_blocks


def _transcript(*texts: str) -> dict:
    return {"segments": [{
        "segment_id": f"seg_{index:05d}",
        "start_seconds": float((index - 1) * 50),
        "end_seconds": float((index - 1) * 50 + 40),
        "text": text,
    } for index, text in enumerate(texts, start=1)]}


class CourseIRTests(unittest.TestCase):
    def test_source_builder_is_shared_with_cloud_mapping(self) -> None:
        transcript = _transcript("第一段内容", "第二段内容")
        sources = build_source_blocks(transcript)
        _, mapping = build_cloud_source_blocks(transcript)
        self.assertEqual([item.source_id for item in sources], list(mapping))
        self.assertEqual(sources[0].segment_ids, mapping["block_0001"])

    def test_course_ir_round_trip_keeps_claim_ownership_and_sources(self) -> None:
        transcript = _transcript("如果价格突破，则需要确认成交量。然后记录结果。")
        plan = LessonPlan(chapters=[ChapterPlan(
            chapter_id="chapter_001",
            unit_plans=[UnitPlan(
                plan_id="plan_001",
                title="突破确认规则",
                knowledge_types=["rule"],
                source_segment_ids=["seg_00001"],
            )],
        )])
        course_ir = build_course_ir(plan, transcript, [])
        restored = CourseIR.from_dict(course_ir.to_dict())
        self.assertEqual(restored.units[0].unit_id, "plan_001")
        self.assertTrue(restored.claims)
        self.assertTrue(all(claim.unit_id == "plan_001" for claim in restored.claims))
        self.assertTrue(all(claim.source_ids for claim in restored.claims))
        self.assertTrue(all(claim.origin == "audio_backed" for claim in restored.claims))

    def test_claim_routes_to_only_one_primary_block(self) -> None:
        claims = [
            Claim("c1", "p1", "explanation", "概念解释", ["b1"], "audio_backed", "f1", "paragraph"),
            Claim("c2", "p1", "condition", "必须先确认方向", ["b1"], "audio_backed", "f2", "rule_list"),
        ]
        blocks = route_claims_to_blocks(claims)
        owners = [claim_id for block in blocks for claim_id in block.claim_ids]
        self.assertEqual(owners, ["c1", "c2"])

    def test_course_ir_to_units_has_no_paragraph_list_reuse(self) -> None:
        transcript = _transcript("核心概念用于判断趋势。必须先确认方向。")
        plan = LessonPlan(chapters=[ChapterPlan(unit_plans=[UnitPlan(
            plan_id="plan_001",
            title="趋势判断",
            knowledge_types=["rule"],
            source_segment_ids=["seg_00001"],
        )])])
        units = course_ir_to_units(build_course_ir(plan, transcript, []), "推荐")
        paragraph = "".join(block.get("text", "") for block in units[0].content_blocks)
        list_items = [item for block in units[0].content_blocks for item in block.get("items", [])]
        self.assertFalse(any(item in paragraph for item in list_items))


class DedupGateTests(unittest.TestCase):
    def test_exact_and_containment_duplicates_are_removed(self) -> None:
        unit = KnowledgeUnit(
            unit_id="unit_0001",
            type="rule",
            title="必须先确认方向",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
            content_blocks=[
                {"block_id": "p", "type": "paragraph", "origin": "audio_backed", "text": "背景说明。必须先确认方向。"},
                {"block_id": "r", "type": "rule_list", "origin": "audio_backed", "items": ["必须先确认方向", "必须先确认方向"]},
            ],
        )
        units, report = run_dedup_gate([unit])
        body = " ".join(str(block) for block in units[0].content_blocks)
        self.assertEqual(body.count("必须先确认方向"), 1)
        self.assertEqual(report.duplicate_claim_count, 1)
        self.assertEqual(report.containment_duplicate_count, 1)
        self.assertNotEqual(units[0].title, "必须先确认方向")

    def test_sensitive_near_duplicates_are_retained(self) -> None:
        unit = KnowledgeUnit(
            unit_id="unit_0002",
            title="边界条件",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
            content_blocks=[
                {"block_id": "p1", "type": "paragraph", "origin": "audio_backed", "text": "价格上涨时成立"},
                {"block_id": "p2", "type": "paragraph", "origin": "audio_backed", "text": "价格不上涨时成立"},
            ],
        )
        units, _ = run_dedup_gate([unit])
        self.assertEqual(len(units[0].content_blocks), 2)

    def test_title_validator_rejects_body_prefix(self) -> None:
        result = validate_title("必须先确认方向", "必须先确认方向，然后检查边界")
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "title_body_overlap")


if __name__ == "__main__":
    unittest.main()
