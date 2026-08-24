from __future__ import annotations

import unittest
from pathlib import Path
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "zhiying"


class SourceLayoutTests(unittest.TestCase):
    def test_project_uses_zhiying_as_the_canonical_python_identity(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["name"], "zhiying")
        self.assertEqual(project["scripts"]["zhiying"], "zhiying.cli:main")
        self.assertEqual(project["scripts"]["video-study"], "zhiying.cli:main")
        self.assertFalse((ROOT / "src" / "video_study").exists())

    def test_generated_metadata_and_unused_env_template_are_not_source(self) -> None:
        self.assertEqual(list((ROOT / "src").glob("*.egg-info")), [])
        self.assertFalse((ROOT / ".env.example").exists())
        self.assertNotIn("!.env.example", (ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_implementation_is_grouped_by_responsibility(self) -> None:
        contract = yaml.safe_load((ROOT / "docs" / "architecture" / "source-layout.yaml").read_text(encoding="utf-8"))
        expected = {item["package"]: set(item["required_modules"]) for item in contract["packages"]}
        for package, filenames in expected.items():
            package_dir = PACKAGE / package
            self.assertTrue((package_dir / "__init__.py").is_file(), package)
            self.assertTrue(filenames <= {path.name for path in package_dir.iterdir()}, package)

    def test_obsolete_flat_implementation_modules_are_removed(self) -> None:
        obsolete = {
            "aggregate.py",
            "asr.py",
            "document_v3.py",
            "frames.py",
            "localplay.py",
            "media.py",
            "pipeline.py",
            "release_quality.py",
            "render_v31.py",
            "single_instance.py",
            "transcript.py",
        }
        present = {path.name for path in PACKAGE.iterdir() if path.is_file()}
        self.assertFalse(obsolete & present, sorted(obsolete & present))

    def test_operational_scripts_are_grouped_by_execution_role(self) -> None:
        contract = yaml.safe_load((ROOT / "docs" / "architecture" / "source-layout.yaml").read_text(encoding="utf-8"))
        scripts_root = ROOT / "scripts"
        for item in contract["script_groups"]:
            group = scripts_root / item["directory"]
            self.assertTrue(group.is_dir(), item["directory"])
            self.assertTrue(set(item["required_files"]) <= {path.name for path in group.iterdir()})
        root_tools = {path.name for path in scripts_root.iterdir() if path.is_file() and path.name != "__init__.py"}
        self.assertEqual(root_tools, set())

    def test_configured_worker_entry_points_exist(self) -> None:
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        for relative in (config["asr"]["qwen_runner"], config["visual_evidence"]["local_vlm_runner"]):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
