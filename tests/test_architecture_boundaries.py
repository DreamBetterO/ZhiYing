from __future__ import annotations

import ast
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "video_study"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_product_has_no_web_server_or_serve_command(self) -> None:
        forbidden_imports = {"http.server", "socketserver", "flask", "fastapi", "uvicorn"}
        violations: list[str] = []
        for path in SOURCE_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {node.module or ""}
                else:
                    continue
                matched = names & forbidden_imports
                if matched:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: {sorted(matched)}")
        cli_source = (SOURCE_ROOT / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn('add_parser("serve"', cli_source)
        self.assertEqual(violations, [])

    def test_cloud_is_opt_in_and_document_contract_is_v2(self) -> None:
        api = yaml.safe_load((PROJECT_ROOT / "api.yaml").read_text(encoding="utf-8"))
        config = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
        self.assertIs(api["qwen"]["enabled"], False)
        self.assertNotIn("document", config)
        self.assertNotIn("cloud_payload", config)
        self.assertNotIn("progress", config)
        self.assertNotIn("execution_mode", config["visual_evidence"])
        self.assertEqual(config["render"]["source_link_base"], "video-study://play")

    def test_secret_file_is_ignored(self) -> None:
        patterns = {
            line.strip()
            for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn(".env", patterns)

    def test_execution_contract_has_no_concrete_middleware_dependency(self) -> None:
        forbidden_roots = {"tkinter", "openai", "subprocess"}
        forbidden_video_study = {
            "video_study.asr", "video_study.frames", "video_study.media",
            "video_study.providers", "video_study.render", "video_study.summarize",
            "video_study.execution.adapters.vision",
        }
        violations: list[str] = []
        for path in (SOURCE_ROOT / "execution").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name.split(".", 1)[0] in forbidden_roots or name in forbidden_video_study:
                        violations.append(f"{path.name}: {name}")
        self.assertEqual(violations, [])

    def test_knowledge_and_desktop_do_not_cross_forbidden_boundaries(self) -> None:
        rules = {
            "knowledge": {"video_study.summarize", "video_study.pipeline", "video_study.desktop", "tkinter", "openai", "subprocess"},
            "desktop": {"video_study.knowledge", "video_study.providers", "video_study.execution.runner", "openai", "subprocess"},
        }
        violations: list[str] = []
        for package, forbidden in rules.items():
            for path in (SOURCE_ROOT / package).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        if node.level:
                            prefix = "video_study." + (package + "." if node.level == 1 else "")
                            names = [prefix + module]
                        else:
                            names = [module]
                    else:
                        continue
                    for name in names:
                        if any(name == item or name.startswith(item + ".") for item in forbidden):
                            violations.append(f"{path.relative_to(PROJECT_ROOT)}: {name}")
        self.assertEqual(violations, [])

    def test_pipeline_is_a_thin_compatibility_facade(self) -> None:
        path = SOURCE_ROOT / "pipeline.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_calls = {
            "extract_audio", "transcribe", "extract_keyframes", "build_document",
            "render_markdown", "render_docx", "render_pdf", "json.load", "json.loads",
        }
        called: set[str] = set()
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
                elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    called.add(f"{node.func.value.id}.{node.func.attr}")
            elif isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        self.assertEqual(called & forbidden_calls, set())
        self.assertFalse(any(name.startswith("video_study.execution.steps") for name in imports))
        self.assertNotIn("use_new_runner", source)

    def test_workspace_consumers_do_not_hardcode_manifest_or_document_paths(self) -> None:
        for relative in ("desktop/view.py", "desktop/controller.py", "application/processing.py", "localplay.py", "aggregate.py"):
            source = (SOURCE_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(module=relative):
                self.assertNotIn("*/manifest.json", source)
                self.assertNotIn('"knowledge" / "document.json"', source)

    def test_desktop_view_and_controller_boundaries(self) -> None:
        view = (SOURCE_ROOT / "desktop" / "view.py").read_text(encoding="utf-8")
        controller = (SOURCE_ROOT / "desktop" / "controller.py").read_text(encoding="utf-8")
        for forbidden in (
            "DefaultProcessingService", "WorkspaceCatalog", "aggregate_documents", "run_all",
            "video_study.knowledge", "video_study.providers", "subprocess",
        ):
            self.assertNotIn(forbidden, view)
        for forbidden in ("tkinter", "messagebox", "filedialog", "startfile", "openai"):
            self.assertNotIn(forbidden, controller)

    def test_third_party_and_process_imports_stay_in_infrastructure_adapters(self) -> None:
        subprocess_allowed = {
            "asr.py", "localplay.py", "media.py", "render.py", "utils.py",
            "execution/adapters/vision.py",
        }
        openai_allowed = {"providers.py"}
        subprocess_users: set[str] = set()
        openai_users: set[str] = set()
        for path in SOURCE_ROOT.rglob("*.py"):
            relative = path.relative_to(SOURCE_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".", 1)[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".", 1)[0]}
                else:
                    continue
                if "subprocess" in names:
                    subprocess_users.add(relative)
                if "openai" in names:
                    openai_users.add(relative)
        self.assertEqual(subprocess_users, subprocess_allowed)
        self.assertEqual(openai_users, openai_allowed)


if __name__ == "__main__":
    unittest.main()
