from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from video_study.execution.artifacts import DOCUMENT_V2, STANDARD_ARTIFACTS
from video_study.execution.steps.coarse import build_coarse_steps


ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def test_desktop_launcher_uses_activated_conda_python_for_v6_runtime(self) -> None:
        launcher_path = ROOT / "启动桌面版.cmd"
        launcher_path.read_bytes().decode("ascii")
        launcher = launcher_path.read_text(encoding="ascii").lower()

        self.assertIn("conda activate imaget10", launcher)
        self.assertIn("python -m video_study desktop", launcher)
        self.assertIn("import langgraph", launcher)
        self.assertNotIn(".venv\\scripts", launcher)

    def test_pipeline_catalog_matches_registered_step_specs(self) -> None:
        value = yaml.safe_load((ROOT / "docs/architecture/pipeline-steps.yaml").read_text(encoding="utf-8"))
        rows = value["steps"]
        specs = [step.spec for step in build_coarse_steps(DOCUMENT_V2)]
        self.assertEqual([row["id"] for row in rows], [spec.step_id for spec in specs])
        for row, spec in zip(rows, specs):
            self.assertEqual(row["owner"], spec.owner)
            self.assertEqual(row["depends_on"], list(spec.dependencies))
            self.assertEqual(row["inputs"], [item.name for item in spec.inputs])
            self.assertEqual(row["outputs"], [item.name for item in spec.outputs])
            self.assertEqual(row["error_prefix"], spec.error_code_prefix)
            self.assertEqual(row["tests"], list(spec.tests))
            for test in row["tests"]:
                self.assertTrue((ROOT / test).is_file(), test)

    def test_module_boundaries_reference_real_interfaces_artifacts_steps_and_tests(self) -> None:
        value = yaml.safe_load((ROOT / "docs/architecture/module-boundaries.yaml").read_text(encoding="utf-8"))
        step_ids = {step.spec.step_id for step in build_coarse_steps(DOCUMENT_V2)}
        for row in value["modules"]:
            importlib.import_module(row["owner"])
            for interface in row["public_interfaces"]:
                module_name, symbol = interface.split(":", 1)
                self.assertTrue(hasattr(importlib.import_module(module_name), symbol), interface)
            for artifact in row["artifacts"]:
                self.assertIn(artifact, STANDARD_ARTIFACTS)
            for step in row["steps"]:
                self.assertIn(step, step_ids)
            for test in row["tests"]:
                self.assertTrue((ROOT / test).is_file(), test)

    def test_problem_index_covers_all_step_error_prefixes(self) -> None:
        value = yaml.safe_load((ROOT / "docs/diagnostics/problem-index.yaml").read_text(encoding="utf-8"))
        rows = value["errors"]
        prefixes = {row["prefix"] for row in rows}
        specs = [step.spec for step in build_coarse_steps(DOCUMENT_V2)]
        self.assertTrue({spec.error_code_prefix for spec in specs} <= prefixes)
        step_ids = {spec.step_id for spec in specs}
        for row in rows:
            importlib.import_module(row["owner"])
            if row["safe_rerun_from"] != "failed_step":
                self.assertIn(row["safe_rerun_from"], step_ids)
            for test in row["tests"]:
                self.assertTrue((ROOT / test).is_file(), test)

    def test_workspace_diagnosis_is_read_only_and_reports_failure(self) -> None:
        from scripts.diagnose_workspace import diagnose

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "video"
            state = root / "state"
            (state / "runs").mkdir(parents=True)
            (state / "cache").mkdir()
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (state / "pipeline-state.json").write_text(json.dumps({
                "run_id": "run-1", "steps": {"frames.select": {
                    "status": "failed", "error_code": "FRAMES_SELECT_UNHANDLED",
                }},
            }), encoding="utf-8")
            (state / "runs/run-1.jsonl").write_text(json.dumps({
                "step_id": "frames.select", "status": "failed",
                "error_code": "FRAMES_SELECT_UNHANDLED",
            }) + "\n", encoding="utf-8")
            before = {str(path.relative_to(root)): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
            report = diagnose(root)
            after = {str(path.relative_to(root)): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}

        self.assertEqual(report["run_id"], "run-1")
        self.assertEqual(report["last_failed_step"], "frames.select")
        self.assertEqual(report["suggested_rerun_step"], "frames.select")
        self.assertEqual(before, after)

    def test_workspace_diagnosis_scans_full_run_for_recent_asr_events(self) -> None:
        from scripts.diagnose_workspace import diagnose

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "video"
            state = root / "state"
            runs = state / "runs"
            runs.mkdir(parents=True)
            (state / "cache").mkdir()
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (state / "pipeline-state.json").write_text(json.dumps({
                "run_id": "run-1", "steps": {},
            }), encoding="utf-8")
            events = [
                {"type": "asr", "sequence": 1, "code": "asr_attempt_failed", "message": "missing nagisa"},
                *(
                    {"type": "runtime", "sequence": index, "code": "noise", "message": "later"}
                    for index in range(2, 45)
                ),
            ]
            (runs / "run-1.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in events),
                encoding="utf-8",
            )
            (runs / "run-1.summary.json").write_text(json.dumps({
                "run_id": "run-1", "status": "succeeded",
            }), encoding="utf-8")

            report = diagnose(root)

        self.assertEqual(report["recent_asr_events"][0]["code"], "asr_attempt_failed")
        self.assertEqual(report["recent_asr_events"][0]["message"], "missing nagisa")

    def test_workspace_diagnosis_uses_transcript_cache_run_for_asr_events(self) -> None:
        from scripts.diagnose_workspace import diagnose

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "video"
            state = root / "state"
            runs = state / "runs"
            cache = state / "cache"
            runs.mkdir(parents=True)
            cache.mkdir()
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (state / "pipeline-state.json").write_text(json.dumps({
                "run_id": "cache-only", "steps": {},
            }), encoding="utf-8")
            (cache / "transcript.decode.json").write_text(json.dumps({
                "run_id": "asr-run",
            }), encoding="utf-8")
            (runs / "cache-only.jsonl").write_text(json.dumps({
                "type": "step_state", "step_id": "transcript.decode", "status": "cached",
            }) + "\n", encoding="utf-8")
            (runs / "cache-only.summary.json").write_text(json.dumps({
                "run_id": "cache-only", "status": "succeeded",
            }), encoding="utf-8")
            (runs / "asr-run.jsonl").write_text(json.dumps({
                "type": "asr", "sequence": 8, "code": "asr_attempt_succeeded",
                "engine": "qwen3-asr-0.6b",
            }) + "\n", encoding="utf-8")

            report = diagnose(root)

        self.assertEqual(report["recent_asr_source_run_id"], "asr-run")
        self.assertEqual(report["recent_asr_events"][0]["engine"], "qwen3-asr-0.6b")

    def test_workspace_diagnosis_marks_historical_v2_as_read_only_not_missing_v3_corruption(self) -> None:
        from scripts.diagnose_workspace import diagnose

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "video"
            (root / "state" / "cache").mkdir(parents=True)
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "knowledge").mkdir()
            (root / "knowledge" / "document.json").write_text('{"schema_version":2}', encoding="utf-8")
            before = (root / "knowledge" / "document.json").read_bytes()
            report = diagnose(root)
            after = (root / "knowledge" / "document.json").read_bytes()

        self.assertEqual(report["migration"]["mode"], "legacy_v2_read_only")
        self.assertEqual(before, after)
        v3 = next(row for row in report["artifacts"] if row["artifact_id"] == "document.v3")
        self.assertEqual(v3["status"], "legacy_not_applicable")

    def test_workspace_diagnosis_marks_pre_v31_document_as_legacy_v3_read_only(self) -> None:
        from scripts.diagnose_workspace import diagnose

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "video"
            (root / "state" / "cache").mkdir(parents=True)
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "knowledge").mkdir()
            document = root / "knowledge" / "document-v3.json"
            document.write_text('{"schema_version":3,"contract_version":"document-v3.0"}', encoding="utf-8")
            before = document.read_bytes()
            report = diagnose(root)
            after = document.read_bytes()

        self.assertEqual(report["migration"]["mode"], "legacy_v3_read_only")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
