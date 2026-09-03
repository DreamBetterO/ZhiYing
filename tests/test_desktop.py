import unittest
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import yaml

from zhiying import __version__
from zhiying.config import AppConfig
from zhiying.desktop import (
    QueueItem,
    STAGE_LABELS,
    cloud_authorization_message,
    config_with_content_level,
    config_with_visual_teaching_level,
    format_duration,
    format_eta,
    qwen_asr_ready,
    save_desktop_settings,
    save_api_credentials,
    validate_desktop_settings,
    validate_speech_models,
    blended_hex,
    watermark_options,
)
from zhiying.desktop.view import PRIMARY_UI_ACTIONS, PRODUCT_DISPLAY_NAME, QUEUE_COLUMNS, UI_COPY


class DesktopLogicTests(unittest.TestCase):
    def test_protocol_registration_failure_does_not_block_desktop_startup(self) -> None:
        from pathlib import Path
        from zhiying.desktop import launch_desktop

        with TemporaryDirectory() as directory:
            config = AppConfig(Path(directory), {"paths": {"workspace_dir": "workspace"}})
            root = MagicMock()
            with (
                patch.dict("sys.modules", {"torch": None}),
                patch("tkinter.Tk", return_value=root),
                patch("zhiying.infrastructure.playback.register_protocol", side_effect=PermissionError("denied")),
                patch("zhiying.desktop.controller.DesktopController", return_value=MagicMock()),
                patch("zhiying.desktop.DefaultProcessingService", return_value=MagicMock()),
                patch("zhiying.desktop.view.DesktopView"),
            ):
                launch_desktop(config)

        root.mainloop.assert_called_once_with()

    def test_primary_ui_keeps_all_product_actions_mounted(self) -> None:
        self.assertEqual(set(PRIMARY_UI_ACTIONS), {
            "add", "add_link", "toggle_all", "remove", "clear_selected_cache", "clear_cache",
            "local", "cloud", "cancel", "aggregate", "local_aggregate", "open_output", "open_video",
            "open_markdown", "open_docx", "open_pdf", "open_aggregate", "settings",
        })

    def test_ui_version_matches_package_version(self) -> None:
        self.assertEqual(__version__, "1.0.0")

    def test_primary_ui_uses_consistent_product_language(self) -> None:
        self.assertEqual(PRODUCT_DISPLAY_NAME, "知影")
        self.assertEqual(UI_COPY["tagline"], "将教学视频整理为可溯源的学习文档")
        self.assertEqual(UI_COPY["add_local"], "＋ 添加本地视频")
        self.assertEqual(UI_COPY["add_link"], "＋ 添加视频链接")
        self.assertEqual(UI_COPY["local_process"], "生成本地文档")
        self.assertEqual(UI_COPY["cloud_process"], "使用云端优化")
        self.assertEqual(UI_COPY["local_merge"], "合并本地文档")
        self.assertEqual(UI_COPY["cloud_merge"], "云端优化合并")
        self.assertEqual(UI_COPY["settings"], "模型与服务设置")
        self.assertNotIn("聚合", "".join(UI_COPY.values()))

    def test_qwen_model_is_only_offered_when_runtime_and_weights_are_complete(self) -> None:
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / "models" / "qwen-runtime" / "Scripts" / "python.exe"
            runtime = root / "models" / "qwen-runtime" / "Lib" / "site-packages"
            model = root / "models" / "qwen-model"
            python.parent.mkdir(parents=True)
            python.touch()
            runtime.mkdir(parents=True)
            model.mkdir(parents=True)
            (model / "config.json").write_text("{}", encoding="utf-8")
            weights = model / "model.safetensors"
            weights.touch()
            config = AppConfig(root, {"asr": {
                "qwen_runtime_python": "models/qwen-runtime/Scripts/python.exe",
                "qwen_runtime_dir": "models/qwen-runtime/Lib/site-packages",
                "qwen_model_dir": "models/qwen-model",
            }})

            self.assertTrue(qwen_asr_ready(config))
            weights.unlink()
            self.assertFalse(qwen_asr_ready(config))

    def test_clear_workspace_cache_keeps_output_and_workspace_directory(self) -> None:
        from zhiying.application.processing import DefaultProcessingService
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            output = root / "output"
            (workspace / "video-a" / "audio").mkdir(parents=True)
            (workspace / "video-a" / "audio" / "audio.flac").write_bytes(b"cache")
            (workspace / "loose.tmp").write_bytes(b"cache")
            output.mkdir()
            (output / "lesson.pdf").write_bytes(b"document")
            config = AppConfig(root, {"paths": {"workspace_dir": "workspace"}})

            self.assertEqual(DefaultProcessingService(config).clear_workspace(), 2)
            self.assertTrue(workspace.is_dir())
            self.assertEqual(list(workspace.iterdir()), [])
            self.assertTrue((output / "lesson.pdf").is_file())

    def test_clear_workspace_cache_rejects_project_root(self) -> None:
        from zhiying.application.processing import DefaultProcessingService
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(root, {"paths": {"workspace_dir": "."}})
            with self.assertRaises(ValueError):
                DefaultProcessingService(config).clear_workspace()

    def test_watermark_is_configurable_and_opacity_is_bounded(self) -> None:
        from pathlib import Path
        config = AppConfig(Path("."), {"desktop": {"watermark": {"text": "custom", "opacity": 0.01}}})
        self.assertEqual(watermark_options(config), ("custom", 0.05))
        self.assertEqual(blended_hex("#000000", "#ffffff", 0.1), "#e6e6e6")

    def test_duration_format_is_compact_and_stable(self) -> None:
        self.assertEqual(format_duration(None), "—")
        self.assertEqual(format_duration(65.9), "01:05")
        self.assertEqual(format_duration(3661), "1:01:01")
        self.assertEqual(format_eta(None, True), "估算中")
        self.assertEqual(format_eta(None, False), "—")

    def test_cloud_authorization_describes_shared_pipeline_budget(self) -> None:
        message = cloud_authorization_message({
            "_runtime_base_url": "https://example.com/v1",
            "_runtime_models": ["model-a", "model-b", "model-c"],
            "budget": {
                "max_calls_per_video": 5,
                "max_input_chars": 60000,
                "planning_max_output_tokens": 3200,
                "max_output_tokens": 6000,
            },
        }, aggregate=False)
        self.assertIn("全流程共享最多 5 次请求", message)
        self.assertIn("规划 1 次 + 整理 1 次", message)
        self.assertIn("3,200 Tokens", message)
        self.assertIn("6,000 Tokens", message)
        self.assertIn("https://example.com/v1", message)

    def test_local_speech_validation_does_not_require_cloud_settings(self) -> None:
        self.assertEqual(validate_speech_models("faster-whisper"), ["faster-whisper", "qwen3-asr-0.6b"])

    def test_new_queue_item_starts_queued_without_artifacts(self) -> None:
        from pathlib import Path
        item = QueueItem(Path("lecture.mp4"))
        self.assertEqual(item.stage, "queued")
        self.assertEqual(STAGE_LABELS[item.stage], "等待中")
        self.assertEqual(item.result, {})

    def test_history_queue_display_is_chinese_without_last_run_column(self) -> None:
        self.assertEqual(STAGE_LABELS["history"], "历史记录")
        self.assertNotIn("last_run", QUEUE_COLUMNS)

    def test_setting_validation_rejects_bad_url_and_unknown_speech_model(self) -> None:
        with self.assertRaises(ValueError):
            validate_desktop_settings("not-a-url", "qwen", "faster-whisper")
        result = validate_desktop_settings("https://example.com/v1", "qwen", "unknown-asr")
        self.assertEqual(result[2], ["faster-whisper", "qwen3-asr-0.6b"])

    def test_setting_save_updates_metadata_without_api_key(self) -> None:
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.yaml").write_text("qwen:\n  default_base_url: https://old.example/v1\n  default_models: [old]\n", encoding="utf-8")
            (root / "config.yaml").write_text("api_config: api.yaml\nasr:\n  engine: faster-whisper\n", encoding="utf-8")
            config = AppConfig(root, {"api_config": "api.yaml", "qwen": {}, "asr": {}})
            save_desktop_settings(config, "https://new.example/v1", ["model-a", "model-b"], ["qwen3-asr-0.6b"])
            api = yaml.safe_load((root / "api.yaml").read_text(encoding="utf-8"))
            main = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(api["qwen"]["default_models"], ["model-a", "model-b"])
            self.assertEqual(main["desktop"]["speech_models"], ["qwen3-asr-0.6b", "faster-whisper"])
            self.assertNotIn("key", (root / "api.yaml").read_text(encoding="utf-8").lower())

    def test_content_level_does_not_change_visual_budget(self) -> None:
        from pathlib import Path
        raw = {
            "asr": {"engine": "faster-whisper"},
            "frames": {"max_keyframes": 8},
            "render": {"offline_section_seconds": 300, "offline_points_per_section": 2},
            "qwen": {"budget": {
                "compact_max_output_tokens": 8000,
                "max_output_tokens": 12000,
                "rich_max_output_tokens": 16000,
            }, "timeout_seconds": 240, "compact_timeout_seconds": 180,
                "rich_timeout_seconds": 360},
        }
        config = AppConfig(Path("."), raw)
        compact = config_with_content_level(config, "精简")
        recommended = config_with_content_level(config, "推荐")
        rich = config_with_content_level(config, "丰富")
        self.assertEqual(compact.raw["asr"], rich.raw["asr"])
        self.assertEqual(compact.raw["frames"]["max_keyframes"], 8)
        self.assertEqual(rich.raw["frames"]["max_keyframes"], 8)
        self.assertEqual(compact.raw["qwen"]["content_level"], "精简")
        self.assertEqual(rich.raw["qwen"]["content_level"], "丰富")
        self.assertEqual(compact.raw["qwen"]["budget"]["max_output_tokens"], 8000)
        self.assertEqual(rich.raw["qwen"]["budget"]["max_output_tokens"], 16000)
        self.assertEqual(recommended.raw["qwen"]["budget"]["max_output_tokens"], 12000)
        self.assertEqual(compact.raw["qwen"]["timeout_seconds"], 180)
        self.assertEqual(recommended.raw["qwen"]["timeout_seconds"], 240)
        self.assertEqual(rich.raw["qwen"]["timeout_seconds"], 360)
        self.assertEqual(raw["frames"]["max_keyframes"], 8)

    def test_visual_teaching_level_is_independent(self) -> None:
        from pathlib import Path
        config = AppConfig(Path("."), {
            "frames": {"max_keyframes": 8},
            "visual_teaching": {"level": "auto"},
            "desktop": {},
        })
        enhanced = config_with_visual_teaching_level(config, "增强")
        self.assertEqual(enhanced.raw["visual_teaching"]["level"], "enhanced")
        self.assertEqual(enhanced.raw["frames"]["max_keyframes"], 8)
        self.assertEqual(config.raw["visual_teaching"]["level"], "auto")

    def test_api_key_is_persisted_only_to_local_env_file(self) -> None:
        from pathlib import Path
        from unittest.mock import patch
        with TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=False):
            root = Path(directory)
            config = AppConfig(root, {"qwen": {"api_key_env": "TEST_API_KEY", "base_url_env": "TEST_BASE_URL"}})
            save_api_credentials(config, "temporary-secret", "https://example.com/v1")
            saved = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("TEST_API_KEY", saved)
            self.assertIn("temporary-secret", saved)
            self.assertNotIn("temporary-secret", str(config.raw))

    def test_selected_video_can_recover_cached_render_result(self) -> None:
        from zhiying.application.processing import DefaultProcessingService
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory); video = root / "lesson.mp4"; video.write_bytes(b"video")
            work = root / "workspace" / "lesson-id"; output = root / "output" / "lesson-id"
            (work / "knowledge").mkdir(parents=True); output.mkdir(parents=True)
            paths = {}
            for kind in ("markdown", "docx", "pdf"):
                path = output / f"lesson.{kind if kind != 'markdown' else 'md'}"; path.write_bytes(b"x"); paths[kind] = str(path)
            manifest = {"video_id": "lesson-id", "source_path": str(video), "stages": {"render": paths}}
            (work / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
            (work / "knowledge" / "document.json").write_text('{"mode":"cloud_summary"}', encoding="utf-8")
            config = AppConfig(root, {"paths": {"workspace_dir": "workspace"}})
            result = DefaultProcessingService(config).history_result(video)
            self.assertEqual(result.video_id, "lesson-id")
            self.assertEqual(result.mode, "cloud_summary")

    def test_cached_result_scans_output_dir_when_manifest_render_incomplete(self) -> None:
        """manifest.stages.render 可能只有 markdown，docx/pdf 应从 output 目录扫描补全。"""
        from zhiying.application.processing import DefaultProcessingService
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory); video = root / "lesson.mp4"; video.write_bytes(b"video")
            work = root / "workspace" / "lesson-id"; output = root / "output" / "lesson-id"
            (work / "knowledge").mkdir(parents=True); output.mkdir(parents=True)
            (output / "lesson.md").write_bytes(b"x")
            (output / "lesson.docx").write_bytes(b"x")
            (output / "lesson.pdf").write_bytes(b"x")
            manifest = {"video_id": "lesson-id", "source_path": str(video), "stages": {"render": {"markdown": str(output / "lesson.md")}}}
            (work / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
            (work / "knowledge" / "document.json").write_text('{"mode":"cloud_summary","cloud_usage":{"total_tokens":100}}', encoding="utf-8")
            config = AppConfig(root, {"paths": {"workspace_dir": "workspace", "output_dir": "output"}})
            result = DefaultProcessingService(config).history_result(video)
            self.assertIsNotNone(result)
            self.assertEqual(result.video_id, "lesson-id")
            self.assertTrue(Path(result.docx).is_file())
            self.assertTrue(Path(result.pdf).is_file())
            self.assertEqual(result.cloud_usage.get("total_tokens"), 100)

    def test_history_snapshot_restores_latest_run_status_time_and_all_stage_usage(self) -> None:
        from zhiying.application.processing import DefaultProcessingService
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory); video = root / "lesson.mp4"; video.write_bytes(b"video")
            work = root / "workspace" / "lesson-id"; output = root / "output" / "lesson-id"
            (work / "knowledge").mkdir(parents=True); (work / "state" / "runs").mkdir(parents=True)
            output.mkdir(parents=True)
            for suffix in ("md", "docx", "pdf"):
                (output / f"lesson.{suffix}").write_bytes(b"x")
            manifest = {"video_id": "lesson-id", "source_path": str(video), "stages": {"render": {"markdown": str(output / "lesson.md")}}}
            (work / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
            (work / "knowledge" / "document.json").write_text('{"mode":"cloud_summary"}', encoding="utf-8")
            (work / "knowledge" / "lesson-plan.json").write_text('{"cloud_info":{"usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}}}', encoding="utf-8")
            (work / "knowledge" / "knowledge-units.json").write_text('{"cloud_info":{"usage":{"prompt_tokens":200,"completion_tokens":30,"total_tokens":230}}}', encoding="utf-8")
            (work / "knowledge" / "editorial-session.json").write_text('{"usage":{"prompt_tokens":300,"completion_tokens":40,"total_tokens":340}}', encoding="utf-8")
            summary = {
                "run_id": "run-latest", "status": "degraded",
                "started_at": "2026-08-25T10:00:00+08:00",
                "finished_at": "2026-08-25T10:02:30+08:00",
            }
            (work / "state" / "runs" / "run-latest.summary.json").write_text(__import__("json").dumps(summary), encoding="utf-8")
            config = AppConfig(root, {"paths": {"workspace_dir": "workspace", "output_dir": "output"}})

            snapshot = DefaultProcessingService(config).history_snapshot(video)

            self.assertEqual(snapshot["run_id"], "run-latest")
            self.assertEqual(snapshot["status"], "degraded")
            self.assertEqual(snapshot["elapsed_seconds"], 150.0)
            self.assertEqual(snapshot["cloud_usage"], {
                "prompt_tokens": 600, "completion_tokens": 90, "total_tokens": 690,
            })

            summary["outputs"] = {
                "cloud_usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }
            (work / "state" / "runs" / "run-latest.summary.json").write_text(
                __import__("json").dumps(summary), encoding="utf-8",
            )
            exact_snapshot = DefaultProcessingService(config).history_snapshot(video)
            self.assertEqual(exact_snapshot["cloud_usage"], {
                "prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
            })
