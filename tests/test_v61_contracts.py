"""CP61-0 V6.1 机器合同测试与测试追溯矩阵。

这些测试只读校验已实施并验收的 V6.1 机器合同 YAML，
确保完成状态、版本和稳定边界不会退回实施前基线。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "迭代升级" / "V6.1 Function Calling编辑Harness目标合同.yaml"
STATE = ROOT / "docs" / "迭代升级" / "当前架构升级状态.yaml"
CHARACTERIZATION = ROOT / "tests" / "test_v61_characterization.py"
FIXTURES = ROOT / "tests" / "fixtures" / "v61"


def _test_methods(path: Path) -> set[str]:
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


class V61ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
        cls.state = yaml.safe_load(STATE.read_text(encoding="utf-8"))
        cls.methods = _test_methods(CHARACTERIZATION)

    def test_contract_and_active_state_are_consistent(self) -> None:
        self.assertEqual(self.contract["contract_id"], "v6-1-function-calling-editorial-harness")
        self.assertEqual(self.contract["status"], "implemented_and_validated")
        self.assertEqual(self.state["change_id"], "v6-1-function-calling-editorial-harness")
        self.assertEqual(self.state["phase"], "completed")
        self.assertEqual(self.state["status"], "implemented_and_validated")
        self.assertEqual(self.state["target"]["implementation_version"], "1.0.0")

    def test_versioning_fields_match_approved_contract(self) -> None:
        versioning = self.contract["versioning"]
        self.assertEqual(versioning["architecture_baseline"], "V6.0")
        self.assertEqual(versioning["product_baseline"], "1.0.0rc1")
        self.assertEqual(versioning["architecture_target"], "V6.1")
        self.assertEqual(versioning["implementation_target"], "1.0.0")
        self.assertEqual(versioning["graph_version"], "v6.1-editorial-tools-1")
        self.assertEqual(versioning["document_schema_version"], 3)
        self.assertEqual(versioning["document_contract_version"], "document-v3.1")
        self.assertEqual(versioning["blueprint_schema_version"], 2)
        self.assertEqual(versioning["editorial_policy_version"], 1)
        self.assertEqual(versioning["evidence_overlay_version"], 1)
        self.assertEqual(versioning["quality_report_version"], 2)
        self.assertEqual(versioning["page_audit_version"], 1)

    def test_tool_error_codes_are_stable_and_bounded(self) -> None:
        errors = self.contract["tool_errors"]
        self.assertEqual(len(errors), len(set(errors)))
        self.assertIn("TOOL_UNKNOWN", errors)
        self.assertIn("TOOL_ARGS_INVALID", errors)
        self.assertIn("TOOL_SCOPE_VIOLATION", errors)
        self.assertIn("TOOL_RESULT_TOO_LARGE", errors)
        self.assertIn("TOOL_MULTIPLE_MUTATIONS", errors)
        self.assertIn("TOOL_REVISION_CONFLICT", errors)
        self.assertIn("TOOL_BUDGET_EXCEEDED", errors)
        self.assertIn("TOOL_PROVIDER_UNSUPPORTED", errors)
        self.assertIn("TOOL_NO_PROGRESS", errors)
        self.assertIn("TOOL_CHECKPOINT_SERIALIZATION_FAILED", errors)

    def test_tool_sets_match_approved_scopes(self) -> None:
        tools = self.contract["tools"]
        self.assertEqual(
            {name for name in tools["planning"]},
            {"lookup_evidence", "lookup_visual_facts", "get_renderer_capabilities", "submit_blueprint"},
        )
        self.assertEqual(set(tools["writing"]), {"submit_chapter"})
        self.assertEqual(
            {name for name in tools["revision"]},
            {"lookup_evidence", "get_page_detail", "submit_patch", "finish_editing"},
        )
        patch = tools["revision"]["submit_patch"]
        self.assertEqual(patch["mode"], "candidate_state_only")
        self.assertIn("add_component", patch["allowed_operations"])
        self.assertNotIn("replace_entire_document", patch["allowed_operations"])
        self.assertIn("replace_entire_document", patch["forbidden_operations"])
        self.assertTrue(tools["revision"]["finish_editing"]["bypass_audit_forbidden"])
        for forbidden in ("write_file", "run_shell", "call_external_network", "upload_image"):
            self.assertIn(forbidden, tools["global_forbidden"])

    def test_model_capability_gate_matches_contract(self) -> None:
        levels = self.contract["model_capabilities"]
        self.assertEqual(set(levels["levels"]), {"tool_native", "structured_only", "local_deterministic"})
        self.assertTrue(levels["levels"]["tool_native"]["editorial_agent_enabled"])
        self.assertFalse(levels["levels"]["structured_only"]["editorial_agent_observation_loop_enabled"])
        self.assertFalse(levels["levels"]["local_deterministic"]["cloud_port_constructed"])
        self.assertTrue(levels["levels"]["local_deterministic"]["local_pipeline_required"])
        self.assertEqual(levels["default_for_unknown_model"], "structured_only")
        self.assertFalse(levels["openai_compatible_implies_tool_native"])
        self.assertTrue(levels["live_capability_probe_requires_cloud_authorization"])

    def test_stage_budget_contract_is_machine_checkable(self) -> None:
        budget = self.contract["budget"]
        self.assertEqual(budget["type"], "StageBudget")
        for field in ("max_total_calls", "max_total_input_tokens", "max_total_output_tokens",
                      "max_stage_calls", "max_stage_tokens", "max_tool_turns",
                      "max_tool_result_chars", "max_batch_units", "repair_reserve"):
            self.assertIn(field, budget["limits_required"])
        self.assertEqual(budget["default_ratio"]["writing"], 0.65)
        self.assertFalse(budget["writing_may_consume_revision_reserve"])
        self.assertTrue(budget["same_tool_same_args_same_revision_cacheable"])
        self.assertTrue(budget["checkpoint_replay_must_check_cache_first"])

    def test_terminal_states_and_provenance_are_truthful(self) -> None:
        states = self.contract["terminal_states"]
        self.assertEqual(set(states), {"succeeded", "degraded", "failed", "cancelled"})
        self.assertTrue(states["degraded"]["auto_publish_artifacts"])
        self.assertTrue(states["degraded"]["explicit_ui_reason_required"])
        self.assertTrue(states["degraded"]["qwen_smart_summary_label_forbidden"])
        provenance = self.contract["provenance"]
        self.assertEqual(
            set(provenance["required_stages"]),
            {"intent", "evidence_reconcile", "blueprint", "chapter_writing", "page_audit", "final_repair"},
        )
        for value in ("cloud_tool", "cloud_structured", "local_vlm", "local_rules", "local_deterministic", "skipped", "none"):
            self.assertIn(value, provenance["allowed_values"])

    def test_four_v61_fixtures_exist_with_golden_expectations(self) -> None:
        for name, fixture in (
            ("math_concept.json", "math_concept"),
            ("math_example.json", "math_example"),
            ("strong_visual.json", "strong_visual"),
            ("weak_visual.json", "weak_visual"),
        ):
            path = FIXTURES / name
            self.assertTrue(path.is_file(), name)
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["kind"], fixture)
            self.assertIsInstance(value.get("golden"), dict)
            self.assertIsInstance(value.get("policy", {}).get("forbidden"), list)
            self.assertGreaterEqual(len(value["transcript"]["segments"]), 1)
            self.assertGreaterEqual(len(value["plan"]["units"]), 1)

    def test_characterization_matrix_items_map_to_existing_tests(self) -> None:
        mapping = {
            "current_plan_is_legacy_wrapper": ("test_v610_plan_contains_legacy_source_section", "test_v61_plan_is_not_legacy_wrapper"),
            "current_renderer_downgrades_v3": ("test_v610_renderer_downgrades_v3_to_v2", "test_v61_render_does_not_downgrade_v3_to_v2"),
            "current_word_has_no_omml": ("test_v610_frozen_docx_omml_is_zero", "test_v61_word_omml_matches_confirmed_equations"),
            "current_degraded_writer_can_look_cloud": (
                "test_v610_frozen_degraded_sample_document_mode_is_cloud_summary",
                "test_v61_degraded_units_not_labeled_cloud_summary",
            ),
        }
        matrix = self.contract["test_matrix"]["characterization"]
        self.assertEqual(set(matrix), set(mapping))
        for names in mapping.values():
            for name in names:
                self.assertIn(name, self.methods, f"缺失测试方法：{name}")

    def test_golden_matrix_items_map_to_existing_fixtures(self) -> None:
        mapping = {
            "high_math_example": "math_example.json",
            "high_math_definition": "math_concept.json",
            "strong_visual": "strong_visual.json",
            "weak_visual": "weak_visual.json",
        }
        matrix = self.contract["test_matrix"]["golden"]
        self.assertEqual(set(matrix), set(mapping))
        for fixture in mapping.values():
            self.assertTrue((FIXTURES / fixture).is_file(), fixture)

    def test_future_matrix_items_have_owner_cp_and_planned_test_file(self) -> None:
        planned = {
            "tool_protocol": ("CP61-3", "tests/test_v61_tool_protocol.py"),
            "graph": ("CP61-4", "tests/test_v61_editorial_graph.py"),
            "degradation": ("CP61-4", "tests/test_v61_degradation.py"),
            "document_renderer": ("CP61-2", "tests/test_v61_native_renderers.py"),
            "ui": ("CP61-5", "tests/test_v61_ui_equivalent.py"),
            "acceptance": ("CP61-7", "unittest/compileall/pip check/git diff --check/yaml/链接/架构守卫"),
        }
        matrix = self.contract["test_matrix"]
        for key in planned:
            self.assertIn(key, matrix)
            owner_cp, test_file = planned[key]
            self.assertIn(owner_cp, {phase["id"] for phase in self.contract["phases"]})
            if test_file.startswith("tests/"):
                # 计划文件暂不要求存在（后续 CP 创建），但路径必须位于 tests/ 下
                self.assertTrue(test_file.startswith("tests/test_v61_"))
            self.assertGreaterEqual(len(matrix[key]), 1)


if __name__ == "__main__":
    unittest.main()
