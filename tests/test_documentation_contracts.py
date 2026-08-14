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


if __name__ == "__main__":
    unittest.main()
