"""CP61-1 Artifact/cache/migration 合同测试（V6.1 编辑子图 Artifact 注册与序列化）。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from video_study.editorial.blueprint import DocumentBlueprint
from video_study.editorial.document import build_v31_document, make_component
from video_study.editorial.evidence import EvidenceCorrectionOverlay, detect_local_corrections, transcript_digest
from video_study.editorial.intent import compile_editorial_policy
from video_study.execution.artifacts import (
    CHAPTER_V31,
    DOCUMENT_BLUEPRINT,
    EDITORIAL_SESSION,
    EDITORIAL_POLICY,
    EVIDENCE_CORRECTIONS,
    V61_ARTIFACTS,
    canonical_json_hash,
)
from video_study.knowledge.editorial import brief_from_text


class V61ArtifactContractTests(unittest.TestCase):
    def test_v61_artifacts_are_registered_with_contract_paths(self) -> None:
        expected = {
            "editorial.policy": ("knowledge/editorial-policy.json",),
            "evidence.corrections": ("knowledge/evidence-corrections.json",),
            "document.blueprint": ("knowledge/document-blueprint-v2.json",),
            "editorial.session": ("knowledge/editorial-session.json",),
            "document.chapter_v31": ("knowledge/chapters/v3.1/chapters.json",),
        }
        self.assertEqual({name: artifact.relative_paths for name, artifact in V61_ARTIFACTS.items()}, expected)

    def test_standard_artifacts_include_v61_editorial_artifacts_after_cutover(self) -> None:
        # CP61-5 后 v61 编辑 Artifact 已并入生产 STANDARD_ARTIFACTS
        from video_study.execution.artifacts import STANDARD_ARTIFACTS
        self.assertIn("editorial.policy", STANDARD_ARTIFACTS)
        self.assertIn("evidence.corrections", STANDARD_ARTIFACTS)
        self.assertIn("document.blueprint", STANDARD_ARTIFACTS)
        self.assertIn("editorial.session", STANDARD_ARTIFACTS)
        self.assertIn("document.chapter_v31", STANDARD_ARTIFACTS)
        self.assertNotIn("document.plan", STANDARD_ARTIFACTS)

    def test_canonical_fingerprint_is_stable(self) -> None:
        policy = compile_editorial_policy(brief_from_text("不要内容导览，公式优先"))
        first = canonical_json_hash(policy.to_dict())
        second = canonical_json_hash(policy.to_dict())
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))

    def test_editorial_policy_artifact_roundtrip(self) -> None:
        policy = compile_editorial_policy(brief_from_text("不要课程复习，例题加思路"))
        payload = json.loads(json.dumps(policy.to_dict(), ensure_ascii=False))
        restored = type(policy).from_dict(payload)
        self.assertEqual(restored.to_dict(), policy.to_dict())

    def test_evidence_corrections_artifact_roundtrip_and_digest(self) -> None:
        transcript = {"schema_version": 1, "segments": [
            {"segment_id": "seg_00001", "start_seconds": 0.0, "end_seconds": 3.0,
             "text": "圆寒数 长数 不定级分"},
        ]}
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(transcript),
            corrections=detect_local_corrections(transcript),
        )
        restored = EvidenceCorrectionOverlay.from_dict(overlay.to_dict())
        self.assertEqual(restored.to_dict(), overlay.to_dict())
        self.assertEqual(restored.transcript_digest, overlay.transcript_digest)

    def test_blueprint_artifact_roundtrip_and_capability_version(self) -> None:
        blueprint = DocumentBlueprint(
            blueprint_id="bp_1", capability_version="renderer-capability-v1",
            chapters=[],
        )
        restored = DocumentBlueprint.from_dict(json.loads(json.dumps(blueprint.to_dict(), ensure_ascii=False)))
        self.assertEqual(restored.to_dict(), blueprint.to_dict())

    def test_v31_document_artifact_is_stable_and_valid(self) -> None:
        from video_study.editorial.document import validate_document_v31
        document = build_v31_document(
            metadata={"video_id": "x"},
            components=[
                make_component(
                    "container", component_id="chapter_001", semantic_role="chapter",
                    title="第一章", children=[
                        make_component(
                            "paragraph", component_id="chapter_001.p1", semantic_role="paragraph",
                            text="内容", source_refs={"segment_ids": ["seg_00001"]},
                        ),
                    ],
                ),
            ],
            provenance={"blueprint": "local_deterministic"},
        )
        validate_document_v31(document)
        self.assertEqual(document["contract_version"], "document-v3.1")
        self.assertEqual(len(canonical_json_hash(document)), 64 + len("sha256:"))


if __name__ == "__main__":
    unittest.main()
