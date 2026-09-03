from __future__ import annotations

import tempfile
import unittest
import re
import zipfile
from pathlib import Path

from scripts.release.contract import load_component_catalog, validate_component_catalog
from scripts.release.finalize_release_repo import finalize_release_repository
from scripts.release.verify_core import verify_core_tree


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "release"
RELEASE_SCRIPTS = ROOT / "scripts" / "release"


class ReleaseDistributionTests(unittest.TestCase):
    def test_release_document_links_are_self_contained(self) -> None:
        link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        for document in RELEASE_ROOT.rglob("*.md"):
            for target in link_pattern.findall(document.read_text(encoding="utf-8")):
                if target.startswith(("https://", "http://", "#")):
                    continue
                relative = target.split("#", 1)[0]
                if relative:
                    self.assertTrue((document.parent / relative).resolve().exists(), f"{document}: {target}")

    def test_core_build_embeds_the_complete_user_document_set(self) -> None:
        build_script = (RELEASE_SCRIPTS / "build_core.ps1").read_text(encoding="utf-8-sig")
        for relative in (
            "DOWNLOADS.md",
            "GPU_GUIDE.md",
            "TROUBLESHOOTING.md",
            "PRIVACY.md",
            "manifests\\components.json",
        ):
            self.assertIn(relative, build_script)
        self.assertIn("(Join-Path $repo 'LICENSE')", build_script)
        core_readme = (RELEASE_SCRIPTS / "CORE-README.md").read_text(encoding="utf-8")
        self.assertIn("docs/DOWNLOADS.md", core_readme)
        self.assertNotIn("ZhiYing-Releases", core_readme)

    def test_root_readme_is_a_portable_github_product_page(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for heading in (
            "## 核心能力",
            "## 性能与消耗参考",
            "## 快速开始",
            "## 开发",
            "## 项目结构",
            "## 文档",
            "## 许可与联系",
        ):
            self.assertIn(heading, readme)
        self.assertIn("高质量、可回溯的影像知识点整理平台", readme)
        self.assertIn("17 分钟", readme)
        self.assertIn("13 万", readme)
        self.assertIn("python -m venv .venv", readme)
        self.assertIn("python.exe -m pip install -e .", readme)
        self.assertIn("python.exe -m zhiying desktop --config config.yaml", readme)
        self.assertIn("启动桌面版.cmd", readme)
        self.assertIn("模型或工具不完整也不影响打开界面", readme)
        self.assertIn("release/DOWNLOADS.md", readme)
        self.assertIn("docs/screenshot/首页.png", readme)
        self.assertNotIn("ImageT10", readme)
        self.assertNotIn("requirements.txt", readme)
        self.assertNotIn("Document v3.1", readme)
        self.assertNotIn("D:/Study/", readme)
        local_links = re.findall(r"!?\[[^]]*\]\(([^)]+)\)", readme)
        for target in local_links:
            if target.startswith(("http://", "https://", "#")):
                continue
            relative_target = target.split("#", 1)[0]
            self.assertTrue((ROOT / relative_target).exists(), target)

    def test_publication_policy_uses_release_assets_for_the_core_zip(self) -> None:
        policy = (RELEASE_SCRIPTS / "PUBLISHING.md").read_text(encoding="utf-8")
        self.assertIn("GitHub Release", policy)
        self.assertIn("ZhiYing-Core-", policy)
        self.assertIn("SHA256SUMS.txt", policy)
        self.assertIn("git add release/output/*.zip", policy)
        stable = load_component_catalog(RELEASE_ROOT / "manifests" / "stable.json")
        self.assertEqual(stable["asset_delivery"], "github-release")

    def test_gitignore_has_explicit_repository_root_boundaries(self) -> None:
        lines = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        for entry in ("/Resource/", "/视频/", "/models/", "/workspace/", "/output/", "/packing/", "/tools/"):
            self.assertIn(entry, lines)
        self.assertIn("/release/output/*", lines)
        self.assertIn("!/release/output/.gitkeep", lines)

    def test_release_lives_in_source_root_and_contains_only_lightweight_contracts(self) -> None:
        for relative in (
            "README.md",
            "QUICK_START.md",
            "DOWNLOADS.md",
            "GPU_GUIDE.md",
            "TROUBLESHOOTING.md",
            "PRIVACY.md",
            "manifests/stable.json",
            "manifests/components.json",
            "checksums/README.md",
            "output/.gitkeep",
        ):
            self.assertTrue((RELEASE_ROOT / relative).is_file(), relative)

    def test_release_documents_are_written_for_end_users(self) -> None:
        self.assertFalse((RELEASE_ROOT / "PUBLISHING.md").exists())
        self.assertTrue((RELEASE_SCRIPTS / "PUBLISHING.md").is_file())

        readme = (RELEASE_ROOT / "README.md").read_text(encoding="utf-8")
        for required in ("下载", "解压", "ZhiYing.exe", "模型与工具", "遇到问题"):
            self.assertIn(required, readme)
        self.assertIn("不影响打开软件", readme)
        for internal_term in (
            "ready_for_local_review",
            "published=false",
            "Git 上传边界",
            "Git 提交",
            "维护者",
        ):
            self.assertNotIn(internal_term, readme)

        core_readme = (RELEASE_SCRIPTS / "CORE-README.md").read_text(encoding="utf-8")
        self.assertIn("ZhiYing.exe", core_readme)
        self.assertIn("ZhiYing-Console.exe", core_readme)
        self.assertIn("不影响打开软件", core_readme)
        self.assertNotIn("核对 [`docs/manifests/components.json`]", core_readme)

    def test_public_project_declares_mit_license(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 DreamBetterO", license_text)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license = { file = "LICENSE" }', pyproject)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("MIT License", readme)
        self.assertTrue((ROOT / "SECURITY.md").is_file())

        self.assertFalse((RELEASE_ROOT / "Join-ReleaseParts.ps1").exists())
        self.assertFalse((RELEASE_ROOT / "manifests" / "FULL-OFFLINE-PARTS.json").exists())
        self.assertFalse((RELEASE_ROOT / "manifests" / "RUNTIME-PARTS.json").exists())
        tracked_payloads = [
            path
            for path in RELEASE_ROOT.rglob("*")
            if path.is_file()
            and "output" not in path.relative_to(RELEASE_ROOT).parts
            and path.stat().st_size > 5 * 1024 * 1024
        ]
        self.assertEqual(tracked_payloads, [])

    def test_component_catalog_uses_external_download_sources(self) -> None:
        catalog = load_component_catalog(RELEASE_ROOT / "manifests" / "components.json")
        validate_component_catalog(catalog)

        ids = {item["id"] for item in catalog["components"]}
        self.assertEqual(
            ids,
            {
                "tools-windows-x64",
                "runtime-cuda12-windows-x64",
                "faster-whisper-small",
                "qwen3-asr-0.6b-hf",
                "qwen3-vl-2b-instruct",
            },
        )
        for component in catalog["components"]:
            self.assertNotIn("DreamBetterO/ZhiYing-Releases", component["source_url"])
            self.assertIn(Path(component["install_path"]).parts[0], {"models", "tools"})

    def test_git_ignores_local_release_output_but_keeps_placeholder(self) -> None:
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/release/output/*", ignore_text)
        self.assertIn("!/release/output/.gitkeep", ignore_text)

    def test_only_core_release_builders_remain(self) -> None:
        for relative in (
            "assemble_full_offline.ps1",
            "package_runtime.ps1",
            "package_tools.ps1",
            "verify_full.py",
            "verify_runtime.py",
            "verify_tools.py",
            "split_asset.py",
            "export_release_repo.py",
        ):
            self.assertFalse((RELEASE_SCRIPTS / relative).exists(), relative)
        self.assertTrue((RELEASE_SCRIPTS / "build_core.ps1").is_file())
        self.assertTrue((RELEASE_SCRIPTS / "verify_core.py").is_file())

    def test_core_verifier_requires_program_but_forbids_payloads_and_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ZhiYing-Core"
            for relative in (
                "ZhiYing.exe",
                "ZhiYing-Console.exe",
                "config.yaml",
                "api.yaml",
                "doctor.cmd",
                "README.md",
                "LICENSE",
                "THIRD_PARTY_NOTICES.md",
                "_internal/runtime.dll",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            for relative in ("models", "tools", "Resource", "workspace", "output"):
                (root / relative).mkdir(parents=True, exist_ok=True)

            report = verify_core_tree(root)
            self.assertEqual(report["product"], "ZhiYing")
            self.assertEqual(report["payload_files"], 0)

            (root / "models" / "model.bin").write_bytes(b"forbidden")
            with self.assertRaisesRegex(ValueError, "payload"):
                verify_core_tree(root)
            (root / "models" / "model.bin").unlink()

            (root / ".env").write_text("SECRET=value", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "secret"):
                verify_core_tree(root)

    def test_release_finalizer_records_only_the_core_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "release"
            (root / "manifests").mkdir(parents=True)
            (root / "checksums").mkdir()
            output = root / "output"
            output.mkdir()
            core = output / "ZhiYing-Core-1.0.0-win-x64.zip"
            with zipfile.ZipFile(core, "w") as archive:
                for relative in (
                    "ZhiYing.exe",
                    "README.md",
                    "LICENSE",
                    "doctor.cmd",
                    "docs/QUICK_START.md",
                    "docs/DOWNLOADS.md",
                    "docs/GPU_GUIDE.md",
                    "docs/TROUBLESHOOTING.md",
                    "docs/PRIVACY.md",
                    "docs/manifests/components.json",
                ):
                    archive.writestr(f"ZhiYing-Core-1.0.0-win-x64/{relative}", "test")

            stable = finalize_release_repository(root, version="1.0.0")
            self.assertTrue(stable["ready_for_local_review"])
            self.assertFalse(stable["published"])
            self.assertEqual(set(stable["assets"]), {"core"})
            self.assertEqual(stable["asset_delivery"], "github-release")
            self.assertEqual(stable["external_components"], "manifests/components.json")
            checksum = (root / "checksums" / "SHA256SUMS.txt").read_text(encoding="utf-8")
            self.assertIn(core.name, checksum)

    def test_release_finalizer_rejects_core_without_embedded_guides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "release"
            (root / "output").mkdir(parents=True)
            core = root / "output" / "ZhiYing-Core-1.0.0-win-x64.zip"
            with zipfile.ZipFile(core, "w") as archive:
                archive.writestr("ZhiYing-Core-1.0.0-win-x64/ZhiYing.exe", "test")
            with self.assertRaisesRegex(ValueError, "documentation"):
                finalize_release_repository(root, version="1.0.0")


if __name__ == "__main__":
    unittest.main()
