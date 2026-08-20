"""V5.0 P2 来源身份扩展 L1 集成测试（离线，不触网）。

覆盖探索方案 §13.4 的 L1 用例：
TC-006 下载完成→已就绪（状态机由 P3 承接，此处验证 from_url 下载产物可用）/
TC-008 下载失败分支（DOWNLOAD_INCOMPLETE 阻止下游）/
TC-009 重复链接缓存复用（find_by_url）/
TC-010 下载完整性校验 / TC-011 find_by_url 缓存命中。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_study.application.requests import ProcessingRequest
from video_study.config import AppConfig
from video_study.execution.artifacts import WorkspaceCatalog, WorkspaceLayout
from video_study.execution.bootstrap import (
    _source_download_dir,
    _stable_source_video_id,
    acquire_source_from_url,
    run_compatible_pipeline_from_url,
)
from video_study.source import DOWNLOAD_INCOMPLETE, SourceError


class FakeSourcePort:
    """L1 fake SourcePort：preflight 返回固定候选；acquire 写出 fixture 文件。"""

    def __init__(self, *, video_id="BV1cmTu6mEL3", title="测试视频", duration=616.0,
                 fail_code=None, bytes_content=b"video-fixture-bytes") -> None:
        self.video_id = video_id
        self.title = title
        self.duration = duration
        self.fail_code = fail_code
        self.bytes_content = bytes_content
        self.preflight_calls = 0
        self.acquire_calls = 0

    def preflight(self, url, *, options=None, cancel_check=None):
        self.preflight_calls += 1
        return {
            "url": "https://www.bilibili.com/video/BV1cmTu6mEL3",
            "video_id": self.video_id,
            "title": self.title,
            "duration_seconds": self.duration,
            "size_bytes": len(self.bytes_content),
            "extractor": "BiliBili",
            "is_live": False,
            "drm": False,
            "best_format": {"height": 480},
            "webpage_url": "https://www.bilibili.com/video/BV1cmTu6mEL3",
        }

    def acquire(self, candidate, target, *, options=None, cancel_check=None, progress=None):
        self.acquire_calls += 1
        if self.fail_code:
            raise SourceError(self.fail_code, "模拟下载失败")
        if progress:
            progress({"phase": "download", "percent": 100, "total_bytes": len(self.bytes_content), "speed_bytes": None})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.bytes_content)
        return {
            "path": str(target), "url": candidate["url"], "video_id": self.video_id,
            "title": self.title, "duration_seconds": self.duration,
            "size_bytes": len(self.bytes_content), "extractor": "BiliBili", "format": "fake",
        }


class ProcessingRequestMutualExclusionTests(unittest.TestCase):
    def test_video_and_url_are_mutually_exclusive(self) -> None:
        ProcessingRequest(video=Path("a.mp4"))
        ProcessingRequest(url="https://example.com/video")
        with self.assertRaisesRegex(ValueError, "video 或 url 之一"):
            ProcessingRequest()
        with self.assertRaisesRegex(ValueError, "video 或 url 之一"):
            ProcessingRequest(video=Path("a.mp4"), url="https://example.com/video")


class StableSourceVideoIdTests(unittest.TestCase):
    def test_prefers_site_id(self) -> None:
        self.assertEqual(
            _stable_source_video_id({"video_id": "BV1cmTu6mEL3", "url": "https://www.bilibili.com/video/BV1cmTu6mEL3"}),
            "BV1cmTu6mEL3",
        )

    def test_falls_back_to_url_hash(self) -> None:
        value = _stable_source_video_id({"video_id": "", "url": "https://media.example.com/trailer.mp4"})
        self.assertTrue(value.startswith("url-"))
        self.assertEqual(value, _stable_source_video_id({"video_id": "", "url": "https://media.example.com/trailer.mp4"}))

    def test_sanitizes_unusual_id(self) -> None:
        value = _stable_source_video_id({"video_id": "a/b\\c:d", "url": "https://x.example"})
        self.assertNotIn("/", value)
        self.assertNotIn("\\", value)
        self.assertNotIn(":", value)


class SourceDownloadDirTests(unittest.TestCase):
    def test_defaults_to_project_video_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(root, {"source": {"enabled": True}})
            self.assertEqual(_source_download_dir(config, config.raw["source"]), (root / "视频").resolve())

    def test_uses_configured_relative_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(root, {"source": {"enabled": True, "download_dir": "我的视频"}})
            self.assertEqual(_source_download_dir(config, config.raw["source"]), (root / "我的视频").resolve())

    def test_uses_configured_absolute_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            absolute = root / "outside"
            config = AppConfig(root, {"source": {"enabled": True, "download_dir": str(absolute)}})
            self.assertEqual(_source_download_dir(config, config.raw["source"]), absolute.resolve())

    def test_acquire_source_writes_to_configured_download_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            download_root = root / "下载视频"
            config = AppConfig(root, {
                "paths": {"workspace_dir": "workspace", "output_dir": "output", "model_dir": "models"},
                "source": {"enabled": True, "download_dir": str(download_root)},
                "asr": {}, "frames": {}, "qwen": {}, "render": {}, "visual_teaching": {"level": "auto"},
            })
            port = FakeSourcePort()
            acquired = acquire_source_from_url(
                config, "https://www.bilibili.com/video/BV1cmTu6mEL3", source_port=port,
            )
            downloaded = Path(acquired["path"])
            self.assertFalse(acquired["cached"])
            self.assertTrue(downloaded.is_file())
            self.assertTrue(download_root.resolve() in downloaded.resolve().parents)


class RunFromUrlTests(unittest.TestCase):
    def _config(self, root: Path) -> AppConfig:
        return AppConfig(root, {
            "paths": {"workspace_dir": "workspace", "output_dir": "output", "model_dir": "models"},
            "source": {"enabled": True, "max_bytes": 2 * 1024 ** 3},
            "asr": {}, "frames": {}, "qwen": {}, "render": {}, "visual_teaching": {"level": "auto"},
        })

    def test_from_url_downloads_then_runs_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            config = self._config(root)
            port = FakeSourcePort()
            captured = {}

            def fake_pipeline(cfg, video, **kwargs):
                captured["video"] = Path(video)
                captured["source_url"] = kwargs.get("source_url")
                captured["display_title"] = kwargs.get("display_title")
                captured["video_id"] = kwargs.get("video_id")
                return {"video_id": "BV1cmTu6mEL3", "manifest": "m.json", "markdown": "a.md", "docx": "a.docx", "pdf": "a.pdf"}

            with patch("video_study.execution.bootstrap.run_compatible_pipeline", side_effect=fake_pipeline):
                result = run_compatible_pipeline_from_url(
                    config, "https://www.bilibili.com/video/BV1cmTu6mEL3", source_port=port,
                )

            self.assertEqual(port.acquire_calls, 1)
            self.assertEqual(captured["source_url"], "https://www.bilibili.com/video/BV1cmTu6mEL3")
            self.assertEqual(captured["display_title"], "测试视频")
            self.assertEqual(captured["video_id"], "BV1cmTu6mEL3")
            self.assertTrue(captured["video"].is_file())
            self.assertIn("BV1cmTu6mEL3", str(captured["video"]))
            self.assertEqual(result["video_id"], "BV1cmTu6mEL3")

    def test_from_url_cached_link_skips_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            config = self._config(root)
            workspace = root / "workspace"
            video_id = "BV1cmTu6mEL3"
            layout = WorkspaceLayout(workspace, video_id)
            source_dir = layout.video_root / "source"
            source_dir.mkdir(parents=True)
            downloaded = source_dir / "测试视频.mp4"
            downloaded.write_bytes(b"cached-bytes")
            manifest = layout.artifact_paths(__import__("video_study.execution.artifacts", fromlist=["SOURCE_MANIFEST"]).SOURCE_MANIFEST)[0]
            manifest.write_text(json.dumps({
                "schema_version": 1, "video_id": video_id, "title": "测试视频",
                "source_path": str(downloaded), "fingerprint": "abc",
                "duration_seconds": 616.0, "size_bytes": 12,
                "source_url": "https://www.bilibili.com/video/BV1cmTu6mEL3",
                "display_title": "测试视频", "stages": {},
            }, ensure_ascii=False), encoding="utf-8")

            port = FakeSourcePort()
            captured = {}

            def fake_pipeline(cfg, video, **kwargs):
                captured["video"] = Path(video)
                return {"video_id": video_id, "manifest": "m.json", "markdown": "a.md", "docx": "a.docx", "pdf": "a.pdf"}

            with patch("video_study.execution.bootstrap.run_compatible_pipeline", side_effect=fake_pipeline):
                run_compatible_pipeline_from_url(
                    config, "https://www.bilibili.com/video/BV1cmTu6mEL3", source_port=port,
                )

            self.assertEqual(port.acquire_calls, 0)  # 缓存命中，不重复下载
            self.assertEqual(captured["video"], downloaded)

    def test_from_url_incomplete_download_blocks_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            config = self._config(root)
            port = FakeSourcePort(fail_code=DOWNLOAD_INCOMPLETE)

            with patch("video_study.execution.bootstrap.run_compatible_pipeline") as fake_pipeline:
                with self.assertRaises(SourceError) as ctx:
                    run_compatible_pipeline_from_url(
                        config, "https://www.bilibili.com/video/BV1cmTu6mEL3", source_port=port,
                    )
                fake_pipeline.assert_not_called()
            self.assertEqual(ctx.exception.code, DOWNLOAD_INCOMPLETE)

    def test_from_url_disabled_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            config = AppConfig(root, {
                "paths": {"workspace_dir": "workspace", "output_dir": "output", "model_dir": "models"},
                "source": {"enabled": False},
            })
            with self.assertRaisesRegex(RuntimeError, "链接源获取已禁用"):
                run_compatible_pipeline_from_url(
                    config, "https://www.bilibili.com/video/BV1cmTu6mEL3", source_port=FakeSourcePort(),
                )


class FindByUrlTests(unittest.TestCase):
    def _entry(self, workspace_root: Path, video_id: str, source_url: str, source_path: Path) -> None:
        layout = WorkspaceLayout(workspace_root, video_id)
        manifest = layout.artifact_paths(__import__("video_study.execution.artifacts", fromlist=["SOURCE_MANIFEST"]).SOURCE_MANIFEST)[0]
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "schema_version": 1, "video_id": video_id, "title": video_id,
            "source_path": str(source_path), "fingerprint": "abc",
            "duration_seconds": 1.0, "size_bytes": 1, "source_url": source_url,
            "stages": {},
        }, ensure_ascii=False), encoding="utf-8")

    def test_find_by_url_hits_normalized_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_id = "BV1cmTu6mEL3"
            source = root / "video.mp4"
            source.write_bytes(b"x")
            self._entry(root, video_id, "https://www.bilibili.com/video/BV1cmTu6mEL3", source)
            catalog = WorkspaceCatalog(root)
            entry = catalog.find_by_url("www.bilibili.com/video/BV1cmTu6mEL3")
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry.manifest["video_id"], video_id)
            self.assertIsNone(catalog.find_by_url("https://other.example/video"))

    def test_find_by_url_ignores_entries_without_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"x")
            self._entry(root, "local-id", "", source)
            catalog = WorkspaceCatalog(root)
            self.assertIsNone(catalog.find_by_url("https://example.com/video"))


if __name__ == "__main__":
    unittest.main()
