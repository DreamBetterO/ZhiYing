from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_runner_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "workers" / "qwen_vl_runner.py"
    spec = importlib.util.spec_from_file_location("qwen_vl_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load qwen_vl_runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QwenVLRunnerPromptTests(unittest.TestCase):
    def test_compare_prompt_does_not_use_placeholder_criteria_text(self) -> None:
        runner = _load_runner_module()
        prompt, _, _ = runner._prompt({
            "action": "compare",
            "question": {"question": "哪张图能解释K线重叠"},
            "candidates": [{"candidate_id": "candidate_00001"}],
            "contract": {"success_criteria": ["画面中可辨认K线"]},
        })
        self.assertNotIn("原样复制已满足条件", prompt)
        self.assertNotIn("原样复制未满足条件", prompt)
        self.assertIn('"criteria_met":[]', prompt)
        self.assertIn("只能从成功条件数组中原样复制", prompt)
        self.assertIn("visible_evidence 最多 2 条", prompt)
        self.assertIn("220 个汉字以内", prompt)

    def test_json_parser_repairs_trailing_commas(self) -> None:
        runner = _load_runner_module()
        parsed = runner._json_object('```json\n{"decision":"no_match","visible_evidence":[],}\n```')
        self.assertEqual(parsed["decision"], "no_match")

    def test_json_parser_accepts_safe_python_literal_style(self) -> None:
        runner = _load_runner_module()
        parsed = runner._json_object("{'decision': 'select', 'visible_evidence': ['K线']}" )
        self.assertEqual(parsed["visible_evidence"], ["K线"])

    def test_candidate_alias_maps_only_to_current_image_set(self) -> None:
        runner = _load_runner_module()
        candidates = [
            {"candidate_id": "candidate_00011"},
            {"candidate_id": "candidate_00017"},
        ]
        self.assertEqual(
            runner._map_candidate_alias({"selected_candidate_id": "图片 B"}, candidates)["selected_candidate_id"],
            "candidate_00017",
        )
        self.assertEqual(
            runner._map_candidate_alias({"selected_candidate_id": "D"}, candidates)["selected_candidate_id"],
            "D",
        )

    def test_truncated_output_repair_never_invents_visual_facts(self) -> None:
        runner = _load_runner_module()
        raw = (
            '{"decision":"select","selected_candidate_id":"B",'
            '"visible_evidence":["图中可见K线与反穿标注"],'
            '"criteria_met":["画面中可辨认K线"],"criteria_missing":['
        )
        result = runner._repair_structured_result(
            raw,
            "compare",
            [{"candidate_id": "candidate_00011"}, {"candidate_id": "candidate_00017"}],
        )
        self.assertEqual(result["selected_candidate_id"], "candidate_00017")
        self.assertEqual(result["visible_evidence"], ["图中可见K线与反穿标注"])
        self.assertEqual(result["criteria_met"], ["画面中可辨认K线"])
        self.assertEqual(result["criteria_missing"], [])

    def test_truncated_output_without_explicit_select_is_rejected(self) -> None:
        runner = _load_runner_module()
        result = runner._repair_structured_result(
            '{"selected_candidate_id":"A","visible_evidence":["K线"]',
            "compare",
            [{"candidate_id": "candidate_00011"}],
        )
        self.assertEqual(result["decision"], "no_match")

    def test_session_loads_model_once_for_multiple_jobs(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in (1, 2):
                (root / f"request-job{index}.json").write_text(json.dumps({
                    "job_id": f"job{index}",
                    "payload": {"action": "compare", "index": index},
                }), encoding="utf-8")
            (root / "done.json").write_text('{"done":true}', encoding="utf-8")
            loaded = (object(), object(), object())
            with patch.object(runner, "_load_model", return_value=loaded) as load_model, patch.object(
                runner, "_generate", side_effect=lambda runtime, model, payload, loaded=None: {"index": payload["index"]},
            ) as generate:
                runner._run_session(Path("runtime"), Path("model"), root)
            self.assertEqual(load_model.call_count, 1)
            self.assertEqual(generate.call_count, 2)
            self.assertEqual(json.loads((root / "ready.json").read_text(encoding="utf-8"))["model_load_count"], 1)
            self.assertEqual(json.loads((root / "stopped.json").read_text(encoding="utf-8"))["processed_jobs"], 2)


if __name__ == "__main__":
    unittest.main()
