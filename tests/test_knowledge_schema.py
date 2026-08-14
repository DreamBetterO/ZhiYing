from __future__ import annotations

import unittest

from video_study.knowledge.schema import (
    ChapterPlan,
    ContentBlock,
    ContentDecision,
    CourseProfile,
    CourseIR,
    CourseUnit,
    Claim,
    EvidenceRef,
    FrameSemantic,
    KnowledgeUnit,
    LessonPlan,
    SideTopic,
    SourceBlock,
    UnitPlan,
    VisualBinding,
    VisualEvidence,
    VisualNeed,
    VisualProfile,
    VisualQuestion,
    decisions_from_list,
    decisions_to_list,
    units_from_list,
    units_to_list,
    bindings_from_list,
    bindings_to_list,
    frame_semantics_from_list,
    frame_semantics_to_list,
)


class CourseProfileTests(unittest.TestCase):
    def test_defaults_are_general(self) -> None:
        profile = CourseProfile()
        self.assertEqual(profile.course_form, "general")
        self.assertEqual(profile.confidence, 0.0)
        self.assertEqual(profile.primary_knowledge_types, [])

    def test_invalid_form_falls_back_to_general(self) -> None:
        profile = CourseProfile(course_form="invalid_form")
        self.assertEqual(profile.course_form, "general")

    def test_invalid_knowledge_types_are_filtered(self) -> None:
        profile = CourseProfile(primary_knowledge_types=["rule", "bad_type", "concept"])
        self.assertEqual(profile.primary_knowledge_types, ["rule", "concept"])

    def test_round_trip(self) -> None:
        profile = CourseProfile(
            domain="金融技术分析", sub_domain="缠论",
            course_form="rule_teaching",
            primary_knowledge_types=["rule", "concept", "case"],
            core_thread="线段中枢的定义与判定",
            side_topics=["大盘复盘"],
            confidence=0.85,
            terminology_hints=["黑K", "中枢", "起手三式"],
            basis="文件名和转写高频术语",
        )
        restored = CourseProfile.from_dict(profile.to_dict())
        self.assertEqual(restored.domain, "金融技术分析")
        self.assertEqual(restored.course_form, "rule_teaching")
        self.assertEqual(restored.primary_knowledge_types, ["rule", "concept", "case"])
        self.assertAlmostEqual(restored.confidence, 0.85)


class CourseIRSchemaTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        course_ir = CourseIR(
            course={"id": "demo"},
            sources=[SourceBlock(source_id="block_0001", text="来源", segment_ids=["seg_00001"])],
            units=[CourseUnit(unit_id="plan_001", title="知识点", source_ids=["block_0001"])],
            claims=[Claim(claim_id="claim_001", unit_id="plan_001", text="事实", source_ids=["block_0001"])],
        )
        restored = CourseIR.from_dict(course_ir.to_dict())
        self.assertEqual(restored.sources[0].segment_ids, ["seg_00001"])
        self.assertEqual(restored.claims[0].origin, "audio_backed")


class ContentDecisionTests(unittest.TestCase):
    def test_defaults(self) -> None:
        decision = ContentDecision()
        self.assertEqual(decision.importance, "supporting")
        self.assertEqual(decision.keep_mode, "concise")
        self.assertEqual(decision.role_tags, [])
        self.assertEqual(decision.knowledge_types, [])

    def test_invalid_enums_are_sanitized(self) -> None:
        decision = ContentDecision(
            role_tags=["core", "bad_role"],
            knowledge_types=["rule", "bad_type"],
            importance="bad_importance",
            keep_mode="bad_mode",
        )
        self.assertEqual(decision.role_tags, ["core"])
        self.assertEqual(decision.knowledge_types, ["rule"])
        self.assertEqual(decision.importance, "supporting")
        self.assertEqual(decision.keep_mode, "concise")

    def test_confidence_is_clamped(self) -> None:
        self.assertEqual(ContentDecision(confidence=1.5).confidence, 1.0)
        self.assertEqual(ContentDecision(confidence=-0.3).confidence, 0.0)

    def test_round_trip(self) -> None:
        decision = ContentDecision(
            decision_id="decision_0001",
            source_segment_ids=["seg_0001", "seg_0002"],
            role_tags=["core", "boundary"],
            knowledge_types=["rule", "boundary_case"],
            importance="core",
            keep_mode="expand",
            confidence=0.92,
            reason="后续多个案例重复使用该判定条件",
        )
        restored = ContentDecision.from_dict(decision.to_dict())
        self.assertEqual(restored.decision_id, "decision_0001")
        self.assertEqual(restored.role_tags, ["core", "boundary"])
        self.assertEqual(restored.knowledge_types, ["rule", "boundary_case"])
        self.assertEqual(restored.keep_mode, "expand")
        self.assertAlmostEqual(restored.confidence, 0.92)


class KnowledgeUnitTests(unittest.TestCase):
    def test_defaults(self) -> None:
        unit = KnowledgeUnit()
        self.assertEqual(unit.type, "concept")
        self.assertEqual(unit.importance, "supporting")
        self.assertEqual(unit.rules, [])
        self.assertEqual(unit.procedure, [])

    def test_invalid_type_falls_back_to_concept(self) -> None:
        unit = KnowledgeUnit(type="bad_type")
        self.assertEqual(unit.type, "concept")

    def test_round_trip_with_full_fields(self) -> None:
        unit = KnowledgeUnit(
            unit_id="unit_0001",
            type="rule",
            title="黑K判定规则",
            importance="core",
            definition_or_conclusion="与M5同价的K线不算黑K",
            prerequisites=["已知M5均线方向"],
            branches=[{"direction": "上涨", "rule": "从高点向左找第一根不破M5的K"}],
            procedure=["确定当前线段方向", "向反方向逐K查找", "遇到同价则停止"],
            rules=[{"condition": "K线最低价等于M5", "conclusion": "不成立"}],
            exceptions=["跳空缺口不适用此规则"],
            positive_examples=["第3课12:30的K线"],
            negative_examples=["第4课05:18的K线（同价）"],
            pitfalls=["容易把收盘价等于M5误判为同价"],
            unresolved=["30F级别是否同样适用"],
            evidence_refs=[{"segment_ids": ["seg_0012"], "frame_ids": ["frame_003"]}],
        )
        restored = KnowledgeUnit.from_dict(unit.to_dict())
        self.assertEqual(restored.unit_id, "unit_0001")
        self.assertEqual(restored.type, "rule")
        self.assertEqual(restored.definition_or_conclusion, "与M5同价的K线不算黑K")
        self.assertEqual(len(restored.branches), 1)
        self.assertEqual(restored.branches[0]["direction"], "上涨")
        self.assertEqual(len(restored.rules), 1)
        self.assertEqual(restored.evidence_refs[0]["segment_ids"], ["seg_0012"])


class EvidenceRefTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        ref = EvidenceRef(segment_ids=["seg_0001"], frame_ids=["frame_001"], note="图表证据")
        restored = EvidenceRef.from_dict(ref.to_dict())
        self.assertEqual(restored.segment_ids, ["seg_0001"])
        self.assertEqual(restored.frame_ids, ["frame_001"])
        self.assertEqual(restored.note, "图表证据")


class BatchSerializationTests(unittest.TestCase):
    def test_decisions_batch_round_trip(self) -> None:
        decisions = [
            ContentDecision(decision_id="d1", keep_mode="expand"),
            ContentDecision(decision_id="d2", keep_mode="omit"),
        ]
        restored = decisions_from_list(decisions_to_list(decisions))
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].decision_id, "d1")
        self.assertEqual(restored[1].keep_mode, "omit")

    def test_units_batch_round_trip(self) -> None:
        units = [
            KnowledgeUnit(unit_id="u1", type="rule"),
            KnowledgeUnit(unit_id="u2", type="case"),
        ]
        restored = units_from_list(units_to_list(units))
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].unit_id, "u1")
        self.assertEqual(restored[1].type, "case")


class LessonPlanTests(unittest.TestCase):
    def test_defaults(self) -> None:
        plan = LessonPlan()
        self.assertEqual(plan.schema_version, 1)
        self.assertEqual(plan.course_form, "general")
        self.assertEqual(plan.chapters, [])

    def test_round_trip(self) -> None:
        plan = LessonPlan(
            domain="金融技术分析",
            course_form="rule_teaching",
            core_thread="黑K判定",
            terminology=["M5", "黑K"],
            visual_profile=VisualProfile(
                course_form="chart_analysis",
                visual_dependency="high",
                dominant_visuals=["chart"],
                recommended_level="enhanced",
                signals=["图表术语密集"],
            ),
            chapters=[ChapterPlan(
                chapter_id="ch_001",
                title="第一章",
                source_segment_ids=["seg_001"],
                unit_plans=[UnitPlan(
                    plan_id="plan_001",
                    title="黑K规则",
                    role="core",
                    knowledge_types=["rule"],
                    detail_level="deep",
                    detail_reason="后续反复依赖",
                    required_facets=["prerequisite", "exception"],
                    source_segment_ids=["seg_001"],
                    visual_need=VisualNeed(
                        required=True,
                        question="哪根K",
                        role="locate",
                        target_count=1,
                        max_count=1,
                        explanation_depth="teaching_note",
                        success_criteria=["看清起点K", "定位边界"],
                    ),
                    supplement_policy="derived_and_short_tip",
                )],
            )],
            side_topics=[SideTopic(title="复盘", keep_mode="index_only", source_segment_ids=["seg_099"])],
        )
        restored = LessonPlan.from_dict(plan.to_dict())
        self.assertEqual(restored.domain, "金融技术分析")
        self.assertEqual(restored.course_form, "rule_teaching")
        self.assertEqual(len(restored.chapters), 1)
        self.assertEqual(restored.chapters[0].unit_plans[0].plan_id, "plan_001")
        self.assertEqual(restored.chapters[0].unit_plans[0].detail_level, "deep")
        self.assertTrue(restored.chapters[0].unit_plans[0].visual_need.required)
        self.assertEqual(restored.visual_profile.course_form, "chart_analysis")
        self.assertEqual(restored.chapters[0].unit_plans[0].visual_need.role, "locate")
        self.assertEqual(restored.chapters[0].unit_plans[0].visual_need.success_criteria, ["看清起点K", "定位边界"])
        self.assertEqual(len(restored.side_topics), 1)
        self.assertEqual(restored.side_topics[0].keep_mode, "index_only")

    def test_all_unit_plans(self) -> None:
        plan = LessonPlan(chapters=[
            ChapterPlan(unit_plans=[UnitPlan(plan_id="p1"), UnitPlan(plan_id="p2")]),
            ChapterPlan(unit_plans=[UnitPlan(plan_id="p3")]),
        ])
        self.assertEqual(len(plan.all_unit_plans), 3)

    def test_invalid_detail_level_falls_back(self) -> None:
        up = UnitPlan(detail_level="invalid")
        self.assertEqual(up.detail_level, "standard")


class FrameSemanticTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        s = FrameSemantic(
            frame_id="frame_007",
            timestamp_seconds=1985.66,
            path="/img/f7.jpg",
            ocr_text="M5 中枢线",
            nearby_transcript="老师说明如何比较",
            visual_type="chart",
            semantic_source=["ocr", "nearby_transcript"],
            confidence=0.68,
        )
        restored = FrameSemantic.from_dict(s.to_dict())
        self.assertEqual(restored.frame_id, "frame_007")
        self.assertEqual(restored.ocr_text, "M5 中枢线")
        self.assertEqual(restored.visual_type, "chart")
        self.assertAlmostEqual(restored.confidence, 0.68)

    def test_confidence_clamped(self) -> None:
        self.assertEqual(FrameSemantic(confidence=1.5).confidence, 1.0)
        self.assertEqual(FrameSemantic(confidence=-0.3).confidence, 0.0)

    def test_batch_round_trip(self) -> None:
        semantics = [FrameSemantic(frame_id="f1"), FrameSemantic(frame_id="f2")]
        restored = frame_semantics_from_list(frame_semantics_to_list(semantics))
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].frame_id, "f1")


class VisualBindingTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        b = VisualBinding(
            frame_id="frame_007",
            unit_id="plan_001",
            relation="worked_example",
            target_block_id="block_005",
            reader_focus="观察中间一天M5值",
            confidence=0.84,
            basis=["ocr_overlap", "nearby_explanation"],
            decision="bind",
        )
        restored = VisualBinding.from_dict(b.to_dict())
        self.assertEqual(restored.frame_id, "frame_007")
        self.assertEqual(restored.unit_id, "plan_001")
        self.assertEqual(restored.decision, "bind")
        self.assertAlmostEqual(restored.confidence, 0.84)

    def test_invalid_decision_falls_back(self) -> None:
        b = VisualBinding(decision="invalid")
        self.assertEqual(b.decision, "bind")

    def test_batch_round_trip(self) -> None:
        bindings = [VisualBinding(frame_id="f1"), VisualBinding(frame_id="f2")]
        restored = bindings_from_list(bindings_to_list(bindings))
        self.assertEqual(len(restored), 2)


class VisualEvidenceTests(unittest.TestCase):
    def test_visual_question_round_trip(self) -> None:
        question = VisualQuestion(
            question_id="vq_001_01",
            unit_id="plan_001",
            question="是否有公式图",
            expected_entities=["公式"],
            expected_relation="变量关系",
        )
        restored = VisualQuestion.from_dict(question.to_dict())
        self.assertEqual(restored.question_id, "vq_001_01")
        self.assertEqual(restored.unit_id, "plan_001")
        self.assertEqual(restored.expected_entities, ["公式"])

    def test_visual_evidence_round_trip(self) -> None:
        evidence = VisualEvidence(
            evidence_id="ve_001",
            question_id="vq_001_01",
            image_path="/tmp/frame.jpg",
            timestamp=12.0,
            matched_knowledge_point_id="plan_001",
            matched_knowledge_id="plan_001",
            relevance_score=0.7,
            why_useful="回答视觉问题",
            match_reason="OCR 命中公式",
            suggested_caption="图 1",
            explanation_for_reader="帮助理解图中关系",
            frame_id="candidate_00002",
            source_timestamp=12.0,
            dedup_group_id="scene_00001",
            scene_cluster_id="scene_00001",
            visible_evidence=["OCR 命中：公式"],
            visual_role="explain",
            criteria_met=["看清公式"],
            visual_answer="变量位于等号两侧",
            needs_detail_pass=False,
            sequence_mode="single",
            visual_group_id="vg_plan_001",
            decision="select",
        )
        restored = VisualEvidence.from_dict(evidence.to_dict())
        self.assertEqual(restored.evidence_id, "ve_001")
        self.assertEqual(restored.frame_id, "candidate_00002")
        self.assertAlmostEqual(restored.relevance_score, 0.7)
        self.assertEqual(restored.matched_knowledge_id, "plan_001")
        self.assertEqual(restored.source_timestamp, 12.0)
        self.assertEqual(restored.dedup_group_id, "scene_00001")
        self.assertEqual(restored.visible_evidence, ["OCR 命中：公式"])
        self.assertEqual(restored.criteria_met, ["看清公式"])
        self.assertEqual(restored.visual_answer, "变量位于等号两侧")
        self.assertEqual(restored.visual_group_id, "vg_plan_001")


class ContentBlockTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        b = ContentBlock(
            block_id="block_001",
            type="rule_list",
            origin="source_backed",
            items=["规则1", "规则2"],
            layout="full_width",
        )
        restored = ContentBlock.from_dict(b.to_dict())
        self.assertEqual(restored.block_id, "block_001")
        self.assertEqual(restored.type, "rule_list")
        self.assertEqual(restored.origin, "source_backed")
        self.assertEqual(len(restored.items), 2)

    def test_invalid_type_falls_back(self) -> None:
        b = ContentBlock(type="invalid_type")
        self.assertEqual(b.type, "paragraph")


class KnowledgeUnitV2FieldsTests(unittest.TestCase):
    def test_v2_fields_round_trip(self) -> None:
        unit = KnowledgeUnit(
            unit_id="u1", type="rule", title="黑K", importance="core",
            plan_id="plan_001",
            detail_level="deep",
            facet_status={"prerequisite": "present", "exception": "missing_in_source"},
            content_blocks=[
                {"block_id": "b1", "type": "paragraph", "origin": "source_backed", "text": "正文"},
                {"block_id": "b2", "type": "figure", "binding_id": "binding_001"},
            ],
            visual_bindings=[
                {"frame_id": "f1", "unit_id": "plan_001", "decision": "bind"},
            ],
        )
        restored = KnowledgeUnit.from_dict(unit.to_dict())
        self.assertEqual(restored.plan_id, "plan_001")
        self.assertEqual(restored.detail_level, "deep")
        self.assertEqual(restored.facet_status["prerequisite"], "present")
        self.assertEqual(len(restored.content_blocks), 2)
        self.assertEqual(len(restored.visual_bindings), 1)

    def test_old_data_without_v2_fields(self) -> None:
        """旧数据（无 V2 字段）应正常反序列化。"""
        old_data = {
            "unit_id": "u1", "type": "concept", "title": "旧", "importance": "core",
            "definition_or_conclusion": "定义",
        }
        unit = KnowledgeUnit.from_dict(old_data)
        self.assertEqual(unit.plan_id, "")
        self.assertEqual(unit.detail_level, "")
        self.assertEqual(unit.content_blocks, [])
        self.assertEqual(unit.visual_bindings, [])


if __name__ == "__main__":
    unittest.main()
