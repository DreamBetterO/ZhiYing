from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zhiying.media.speech import _initial_prompt, apply_terminology_corrections
from zhiying.media.transcript import normalize_transcript, write_srt


def sample_transcript(text: str = "心房才动伴随房才") -> dict:
    return {
        "schema_version": 1,
        "segments": [{
            "segment_id": "seg_00001",
            "start_seconds": 1.25,
            "end_seconds": 3.5,
            "text": text,
            "avg_logprob": -0.1,
            "no_speech_prob": 0.0,
        }],
    }


class TerminologyCorrectionTests(unittest.TestCase):
    def test_initial_prompt_combines_hotwords_and_video_title_without_duplicates(self) -> None:
        prompt = _initial_prompt({"hotwords": ["医学", "心房颤动", "医学"]}, "房颤教学")

        self.assertEqual(prompt, "医学，心房颤动，房颤教学")

    def test_correction_is_non_cascading_and_preserves_traceability(self) -> None:
        settings = {"terminology_replacements": {"心房才动": "心房颤动", "房才": "房颤", "房颤": "其他"}}
        corrected, changed = apply_terminology_corrections(sample_transcript(), settings)

        self.assertTrue(changed)
        segment = corrected["segments"][0]
        self.assertEqual(segment["text"], "心房颤动伴随房颤")
        self.assertEqual(segment["raw_text"], "心房才动伴随房才")
        self.assertEqual(segment["start_seconds"], 1.25)
        self.assertEqual(corrected["terminology_correction"]["replacement_count"], 2)

    def test_removing_rules_restores_raw_asr_text(self) -> None:
        corrected, _ = apply_terminology_corrections(
            sample_transcript(), {"terminology_replacements": {"房才": "房颤"}}
        )
        restored, changed = apply_terminology_corrections(corrected, {"terminology_replacements": {}})

        self.assertTrue(changed)
        self.assertEqual(restored["segments"][0]["text"], "心房才动伴随房才")
        self.assertNotIn("raw_text", restored["segments"][0])
        self.assertNotIn("terminology_correction", restored)

    def test_raw_transcript_is_corrected_and_srt_is_built_by_normalize_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_srt = root / "transcript.srt"
            result = normalize_transcript(
                sample_transcript(), {"terminology_replacements": {"房才": "房颤"}}, 10.0,
            )
            write_srt(output_srt, result["segments"])

            self.assertEqual(result["segments"][0]["text"], "心房颤动伴随房颤")
            self.assertIn("心房颤动伴随房颤", output_srt.read_text(encoding="utf-8-sig"))

    def test_tail_timestamp_is_clamped_to_video_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_srt = root / "transcript.srt"
            raw = sample_transcript("视频末尾")
            raw["segments"][0]["start_seconds"] = 10.2
            raw["segments"][0]["end_seconds"] = 11.3
            result = normalize_transcript(raw, {}, 10.0)
            write_srt(output_srt, result["segments"])

            self.assertEqual(result["segments"][0]["start_seconds"], 10.0)
            self.assertEqual(result["segments"][0]["end_seconds"], 10.0)
            self.assertIn("00:00:10,000 --> 00:00:10,000", output_srt.read_text(encoding="utf-8-sig"))

    def test_normalization_preserves_provider_engine_identity(self) -> None:
        raw = sample_transcript("已有转写")
        raw["engine"] = "faster-whisper"
        result = normalize_transcript(raw, {}, 10.0)
        self.assertEqual(result["engine"], "faster-whisper")
        self.assertEqual(result["segments"][0]["text"], "已有转写")


if __name__ == "__main__":
    unittest.main()
