from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class V61DiagnosticsTests(unittest.TestCase):
    @staticmethod
    def _workspace(directory: str) -> Path:
        root = Path(directory) / "video"
        (root / "state" / "cache").mkdir(parents=True)
        (root / "knowledge").mkdir()
        (root / "manifest.json").write_text("{}", encoding="utf-8")
        return root

    def test_missing_registered_artifact_is_not_reported_as_workspace_root(self) -> None:
        from scripts.diagnose_workspace import diagnose

        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            report = diagnose(root)

        policy = next(row for row in report["artifacts"] if row["artifact_id"] == "editorial.policy")
        self.assertEqual(policy["status"], "missing")
        self.assertEqual(Path(policy["path"]).name, "editorial-policy.json")
        self.assertNotEqual(Path(policy["path"]), root)

    def test_v61_summary_reports_versions_capability_usage_quality_and_revision(self) -> None:
        from scripts.diagnose_workspace import diagnose

        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            (root / "knowledge" / "editorial-policy.json").write_text(json.dumps({
                "schema_version": 1,
            }), encoding="utf-8")
            (root / "knowledge" / "document-blueprint-v2.json").write_text(json.dumps({
                "schema_version": 2,
            }), encoding="utf-8")
            (root / "knowledge" / "editorial-session.json").write_text(json.dumps({
                "requested_capability": "tool_native",
                "capability": "structured_only",
                "terminal_status": "degraded",
                "tool_turns": 2,
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
                "model_chain": ["model-a", "model-b"],
                "degradation_reasons": ["tool provider unsupported"],
                "provenance": {"blueprint": "structured_only"},
                "document_revision": 1,
                "revision_cycles_used": 1,
                "quality_report": {
                    "schema_version": 2, "status": "invalid",
                    "issues": [{"code": "MATH_LATEX_MISSING", "owner_component": "eq-1"}],
                    "math": {"equation_components": 2, "word_omml": 1},
                    "visual": {"image_components": 3},
                    "evidence": {"source_reference_components": 4},
                },
                "page_report": {"schema_version": 1, "status": "valid", "issues": []},
            }), encoding="utf-8")
            (root / "knowledge" / "document-v3.json").write_text(json.dumps({
                "schema_version": 3, "contract_version": "document-v3.1",
            }), encoding="utf-8")

            report = diagnose(root)

        summary = report["v61"]
        self.assertEqual(summary["versions"]["editorial_policy_version"], 1)
        self.assertEqual(summary["versions"]["blueprint_version"], 2)
        self.assertEqual(summary["versions"]["document_contract_version"], "document-v3.1")
        self.assertEqual(summary["provider"]["requested_capability"], "tool_native")
        self.assertEqual(summary["provider"]["effective_capability"], "structured_only")
        self.assertEqual(summary["provider"]["tool_turns"], 2)
        self.assertEqual(summary["provider"]["token_usage"]["total_tokens"], 150)
        self.assertEqual(summary["quality"]["issue_ids"], ["MATH_LATEX_MISSING"])
        self.assertEqual(summary["quality"]["owner_components"], ["eq-1"])
        self.assertEqual(summary["quality"]["component_revision"], 1)
        self.assertEqual(summary["statistics"]["equation_count"], 2)
        self.assertEqual(summary["statistics"]["image_count"], 3)
        self.assertEqual(summary["suggested_rerun_node"], "document.write")

    def test_v61_fault_matrix_has_stable_owner_error_and_rerun(self) -> None:
        matrix = yaml.safe_load(
            (ROOT / "docs" / "diagnostics" / "v61-fault-matrix.yaml").read_text(encoding="utf-8")
        )
        problem_index = yaml.safe_load(
            (ROOT / "docs" / "diagnostics" / "problem-index.yaml").read_text(encoding="utf-8")
        )
        errors = {row["prefix"]: row for row in problem_index["errors"]}
        self.assertTrue(matrix["faults"])
        for fault in matrix["faults"]:
            self.assertIn(fault["error_code"], errors)
            self.assertEqual(fault["owner"], errors[fault["error_code"]]["owner"])
            self.assertEqual(fault["suggested_rerun"], errors[fault["error_code"]]["safe_rerun_from"])
            for test in fault["tests"]:
                self.assertTrue((ROOT / test).is_file(), test)


if __name__ == "__main__":
    unittest.main()
