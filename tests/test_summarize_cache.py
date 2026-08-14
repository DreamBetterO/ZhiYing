from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_study.summarize import build_document


class KnowledgeCacheTests(unittest.TestCase):
    def test_changed_source_protocol_invalidates_cached_document(self) -> None:
        manifest = {"video_id": "demo", "title": "Demo", "source_path": "demo.mp4", "duration_seconds": 5}
        transcript = {"segments": [{"segment_id": "s1", "start_seconds": 1, "end_seconds": 2, "text": "内容"}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "document.json"
            first = build_document(manifest, transcript, {"frames": []}, output, {"enabled": False}, {"source_link_base": "old://link"})
            second = build_document(manifest, transcript, {"frames": []}, output, {"enabled": False}, {"source_link_base": "video-study://play"})
        self.assertTrue(first["sections"][0]["knowledge_points"][0]["source_url"].startswith("old://link/"))
        self.assertTrue(second["sections"][0]["knowledge_points"][0]["source_url"].startswith("video-study://play/"))

    def test_changed_transcript_rebuilds_cached_document(self) -> None:
        manifest = {
            "video_id": "demo-123",
            "title": "Demo",
            "source_path": "Resource/demo.mp4",
            "duration_seconds": 10.0,
        }
        transcript = {
            "segments": [{
                "segment_id": "seg_00001",
                "start_seconds": 1.0,
                "end_seconds": 2.0,
                "text": "标准医学术语",
                "raw_text": "误识别词",
            }]
        }
        cached = {"mode": "transcript_preview", "transcript": [{"text": "误识别词"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "document.json"
            output.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            document = build_document(
                manifest,
                transcript,
                {"frames": []},
                output,
                {"enabled": False},
                {"source_link_base": "video-study://play"},
            )

        self.assertEqual(document["transcript"], transcript["segments"])
        self.assertEqual(document["sections"][0]["knowledge_points"][0]["statement"], "标准医学术语")


if __name__ == "__main__":
    unittest.main()
