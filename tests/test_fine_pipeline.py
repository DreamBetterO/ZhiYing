from __future__ import annotations

import unittest

from tests import test_coarse_pipeline as coarse
from video_study.pipeline import process_video


class SelectiveInvalidationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = coarse.CoarseProductionPipelineTests(methodName="runTest")
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    @staticmethod
    def _statuses(result: dict) -> dict[str, str]:
        return {
            row["step_id"]: row["status"]
            for row in result["runtime_events"]
            if row.get("type") == "step_state"
        }

    def _baseline(self) -> None:
        with self.fixture.fake_middleware():
            process_video(self.fixture.config, self.fixture.video, cloud_summary=False)

    def test_terminology_change_reruns_normalize_without_decode(self) -> None:
        self._baseline()
        self.fixture.config.raw["asr"]["terminology_replacements"] = {"离线": "本地"}
        with self.fixture.fake_middleware():
            result = process_video(self.fixture.config, self.fixture.video, cloud_summary=False)
        statuses = self._statuses(result)
        self.assertEqual(statuses["audio.extract"], "cached")
        self.assertEqual(statuses["transcript.decode"], "cached")
        self.assertEqual(statuses["transcript.normalize"], "succeeded")
        self.assertEqual(statuses["frames.candidates"], "cached")
        self.assertEqual(statuses["frames.select"], "cached")

    def test_selection_threshold_change_reuses_candidate_sampling(self) -> None:
        self._baseline()
        self.fixture.config.raw["frames"]["scene_change_threshold"] = 0.08
        with self.fixture.fake_middleware():
            result = process_video(self.fixture.config, self.fixture.video, cloud_summary=False)
        statuses = self._statuses(result)
        self.assertEqual(statuses["frames.candidates"], "cached")
        self.assertEqual(statuses["frames.select"], "succeeded")

    def test_engine_change_does_not_reextract_audio(self) -> None:
        self._baseline()
        self.fixture.config.raw["asr"]["engine"] = "fake-v2"
        with self.fixture.fake_middleware():
            result = process_video(self.fixture.config, self.fixture.video, cloud_summary=False)
        statuses = self._statuses(result)
        self.assertEqual(statuses["audio.extract"], "cached")
        self.assertEqual(statuses["transcript.decode"], "succeeded")

    def test_content_level_change_only_invalidates_knowledge_and_render(self) -> None:
        self._baseline()
        self.fixture.config.raw["qwen"]["content_level"] = "丰富"
        with self.fixture.fake_middleware():
            result = process_video(self.fixture.config, self.fixture.video, cloud_summary=False)
        statuses = self._statuses(result)
        for step_id in (
            "audio.extract", "transcript.decode", "transcript.normalize",
            "frames.candidates", "frames.select",
        ):
            self.assertEqual(statuses[step_id], "cached")
        self.assertEqual(statuses["knowledge.plan"], "succeeded")
        self.assertEqual(statuses["knowledge.units"], "succeeded")
        self.assertEqual(statuses["document.assemble"], "succeeded")


if __name__ == "__main__":
    unittest.main()
