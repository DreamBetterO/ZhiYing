"""V5.0 P1 源获取内核 L0 测试（离线，不触网）。

覆盖探索方案 §13.4 的 L0 用例：
TC-001 链接形态识别 / TC-002 预检判定分类 / TC-003 无效提示文案 /
TC-004 URL 清洗与 BV/av 提取 / TC-005 下载进度事件（两段）/
TC-020 抖音链接处理 / TC-021 工具链完整性。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_study.source import (
    DOWNLOAD_FAILED,
    DOWNLOAD_INCOMPLETE,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_TOO_LARGE,
    SOURCE_DRM,
    SOURCE_LIVE_UNSUPPORTED,
    SOURCE_LOGIN_REQUIRED,
    SOURCE_NOT_VIDEO,
    SOURCE_REGION_LOCKED,
    SOURCE_UNSUPPORTED_SITE,
    SOURCE_UNAVAILABLE,
    URL_INVALID,
    SourceError,
    YtDlpSourceAdapter,
    _format_duration_check,
    classify_ytdlp_error,
    extract_url_or_id,
    normalize_url,
    parse_progress_line,
    resolve_downloaded_file,
)


class FakeYtDlpRunner:
    """模拟 yt-dlp exe：预检返回 JSON/错误文本，下载按需喂进度行。"""

    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0,
                 progress_lines: list[str] | None = None) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.progress_lines = progress_lines or []
        self.calls: list[list[str]] = []
        self.download_calls: list[list[str]] = []

    def run(self, command, *, timeout_seconds, cancel_check):
        self.calls.append(list(command))
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)

    def run_download(self, command, *, cancel_check, progress):
        self.download_calls.append(list(command))
        for line in self.progress_lines:
            event = parse_progress_line(line)
            if event and progress:
                progress(event)
        return self.stderr


def _sample_info(**overrides) -> str:
    info = {
        "id": "BV1cmTu6mEL3",
        "title": "测试视频标题",
        "duration": 616.0,
        "extractor_key": "BiliBili",
        "webpage_url": "https://www.bilibili.com/video/BV1cmTu6mEL3",
        "is_live": False,
        "formats": [
            {"format_id": "30280", "height": 720, "fps": 30, "ext": "mp4", "filesize_approx": 20_000_000},
            {"format_id": "30216", "height": 480, "fps": 30, "ext": "mp4", "filesize_approx": 10_000_000},
        ],
        "filesize_approx": 20_000_000,
    }
    info.update(overrides)
    return json.dumps(info, ensure_ascii=False)


class UrlCleaningTests(unittest.TestCase):
    """TC-001 链接形态识别 / TC-004 URL 清洗与 BV/av 提取。"""

    def test_extract_direct_mp4_link(self) -> None:
        self.assertEqual(
            extract_url_or_id("看这个 https://media.example.com/video.mp4 不错"),
            "https://media.example.com/video.mp4",
        )

    def test_extract_hls_playlist(self) -> None:
        self.assertEqual(
            extract_url_or_id("https://test-streams.example/x36xhzz/x36xhzz.m3u8"),
            "https://test-streams.example/x36xhzz/x36xhzz.m3u8",
        )

    def test_extract_bare_bv_from_text(self) -> None:
        self.assertEqual(
            extract_url_or_id("分享视频 BV1cmTu6mEL3 给大家"),
            "https://www.bilibili.com/video/BV1cmTu6mEL3",
        )

    def test_extract_bare_av_from_text(self) -> None:
        self.assertEqual(
            extract_url_or_id("av170001 这个视频"),
            "https://www.bilibili.com/video/av170001",
        )

    def test_extract_bilibili_page_url(self) -> None:
        self.assertEqual(
            extract_url_or_id("https://www.bilibili.com/video/BV1cmTu6mEL3/?p=1"),
            "https://www.bilibili.com/video/BV1cmTu6mEL3/?p=1",
        )

    def test_extract_short_link(self) -> None:
        """TC-001 短链：保留原 URL 交给 yt-dlp 跟随还原。"""
        self.assertEqual(
            extract_url_or_id("https://b23.tv/AbCdEf"),
            "https://b23.tv/AbCdEf",
        )
        self.assertEqual(normalize_url("b23.tv/AbCdEf"), "https://b23.tv/AbCdEf")

    def test_extract_plain_webpage(self) -> None:
        """TC-001 普通网页：能提取 URL，但预检阶段应判为非视频页。"""
        self.assertEqual(
            extract_url_or_id("https://example.com/article/123"),
            "https://example.com/article/123",
        )

    def test_empty_and_garbage_input(self) -> None:
        self.assertEqual(extract_url_or_id(""), "")
        self.assertEqual(extract_url_or_id("随便一段没有链接的文字"), "")

    def test_normalize_adds_scheme_and_drops_trailing_punctuation(self) -> None:
        self.assertEqual(normalize_url("example.com/video.mp4"), "https://example.com/video.mp4")
        self.assertEqual(normalize_url("https://a.b/c.mp4。"), "https://a.b/c.mp4")

    def test_normalize_rejects_invalid_host(self) -> None:
        self.assertEqual(normalize_url("not a url"), "")


class ClassificationTests(unittest.TestCase):
    """TC-002 预检判定分类 / TC-003 无效提示文案。"""

    def test_ytdlp_error_signal_mapping(self) -> None:
        cases = {
            "ERROR: Unsupported URL: https://example.com/page": (SOURCE_UNSUPPORTED_SITE, "未识别到视频内容"),
            "ERROR: HTTP Error 404: Not Found": (SOURCE_UNAVAILABLE, "链接失效或视频已下架"),
            "ERROR: This video is only available to premium members": (SOURCE_LOGIN_REQUIRED, "需要登录或会员"),
            "ERROR: Video unavailable. This video is not available in your country": (SOURCE_REGION_LOCKED, "地区/版权限制"),
            "ERROR: This video is DRM protected": (SOURCE_DRM, "DRM/付费保护"),
            "ERROR: [generic] Timed out": (DOWNLOAD_TIMEOUT, "无法连接视频站点"),
        }
        for text, (expected_code, expected_hint) in cases.items():
            with self.subTest(text=text[:40]):
                code, message = classify_ytdlp_error(text)
                self.assertEqual(code, expected_code)
                self.assertIn(expected_hint, message)

    def test_ytdlp_412_ratelimit_maps_to_timeout_not_unavailable(self) -> None:
        """P5 实测：B 站阶段性 412/风控应提示稍后重试，而非误报链接失效。"""
        code, message = classify_ytdlp_error("ERROR: HTTP Error 412: Precondition Failed (caused by <HTTPError 412>)")
        self.assertEqual(code, DOWNLOAD_TIMEOUT)
        self.assertIn("稍后重试", message)

    def test_unknown_error_falls_back_to_unavailable(self) -> None:
        code, _message = classify_ytdlp_error("ERROR: some weird failure")
        self.assertEqual(code, SOURCE_UNAVAILABLE)


class PreflightTests(unittest.TestCase):
    """TC-002/TC-020/TC-021：预检成功分类、抖音拦截、体积上限、工具链。"""

    def _adapter(self, runner: FakeYtDlpRunner, *, probe=None) -> YtDlpSourceAdapter:
        return YtDlpSourceAdapter(tool="yt-dlp", runner=runner, probe=probe)

    def test_preflight_ok_extracts_candidate(self) -> None:
        runner = FakeYtDlpRunner(stdout=_sample_info())
        adapter = self._adapter(runner)
        candidate = adapter.preflight("https://www.bilibili.com/video/BV1cmTu6mEL3")
        self.assertEqual(candidate["video_id"], "BV1cmTu6mEL3")
        self.assertEqual(candidate["title"], "测试视频标题")
        self.assertEqual(candidate["duration_seconds"], 616.0)
        self.assertEqual(candidate["size_bytes"], 20_000_000)
        self.assertEqual(candidate["best_format"]["height"], 720)
        self.assertFalse(candidate["is_live"])
        self.assertFalse(candidate["drm"])

    def test_preflight_invalid_input(self) -> None:
        adapter = self._adapter(FakeYtDlpRunner())
        with self.assertRaises(SourceError) as ctx:
            adapter.preflight("")
        self.assertEqual(ctx.exception.code, URL_INVALID)

    def test_preflight_douyin_rejected_without_download(self) -> None:
        runner = FakeYtDlpRunner()
        adapter = self._adapter(runner)
        with self.assertRaises(SourceError) as ctx:
            adapter.preflight("https://www.douyin.com/video/7674856617862597934")
        self.assertEqual(ctx.exception.code, SOURCE_UNSUPPORTED_SITE)
        self.assertIn("暂不支持抖音", ctx.exception.message)
        self.assertEqual(runner.calls, [])  # 不发起真实预检

    def test_preflight_deny_site(self) -> None:
        runner = FakeYtDlpRunner()
        adapter = self._adapter(runner)
        with self.assertRaises(SourceError) as ctx:
            adapter.preflight("https://denied.example.com/video", options={"deny_sites": ["denied.example.com"]})
        self.assertEqual(ctx.exception.code, SOURCE_UNSUPPORTED_SITE)
        self.assertEqual(runner.calls, [])

    def test_preflight_404_maps_to_unavailable(self) -> None:
        runner = FakeYtDlpRunner(stderr="ERROR: HTTP Error 404: Not Found", returncode=1)
        adapter = self._adapter(runner)
        with self.assertRaises(SourceError) as ctx:
            adapter.preflight("https://www.bilibili.com/video/BV1xx4040000")
        self.assertEqual(ctx.exception.code, SOURCE_UNAVAILABLE)

    def test_preflight_live_rejected(self) -> None:
        runner = FakeYtDlpRunner(stdout=_sample_info(is_live=True, live_status="is_live"))
        adapter = self._adapter(runner)
        with self.assertRaises(SourceError) as ctx:
            adapter.preflight("https://live.example.com/room/1")
        self.assertEqual(ctx.exception.code, SOURCE_LIVE_UNSUPPORTED)

    def test_preflight_drm_rejected(self) -> None:
        runner = FakeYtDlpRunner(stdout=_sample_info(formats=[]))
        adapter = self._adapter(runner)
        with self.assertRaises(SourceError) as ctx:
            adapter.preflight("https://drm.example.com/video")
        self.assertEqual(ctx.exception.code, SOURCE_DRM)

    def test_preflight_too_large_rejected(self) -> None:
        runner = FakeYtDlpRunner(stdout=_sample_info(filesize_approx=3 * 1024 ** 3))
        adapter = self._adapter(runner)
        with self.assertRaises(SourceError) as ctx:
            adapter.preflight("https://big.example.com/video", options={"max_bytes": 2 * 1024 ** 3})
        self.assertEqual(ctx.exception.code, DOWNLOAD_TOO_LARGE)

    def test_preflight_uses_cookies_and_user_agent_options(self) -> None:
        runner = FakeYtDlpRunner(stdout=_sample_info())
        adapter = self._adapter(runner)
        adapter.preflight(
            "https://www.bilibili.com/video/BV1cmTu6mEL3",
            options={"cookies_file": "C:/tmp/cookies.txt", "user_agent": "custom-agent"},
        )
        command = runner.calls[0]
        self.assertIn("--cookies", command)
        self.assertIn("C:/tmp/cookies.txt", command)
        self.assertIn("--user-agent", command)
        self.assertIn("custom-agent", command)
        self.assertIn("--dump-json", command)

    def test_check_tools_reports_presence(self) -> None:
        runner = FakeYtDlpRunner()
        adapter = self._adapter(runner)
        with patch("video_study.source.find_tool", return_value="yt-dlp"), \
                patch("subprocess.run") as sub_run:
            sub_run.return_value = SimpleNamespace(stdout="2026.07.04\n", returncode=0)
            result = adapter.check_tools()
        self.assertTrue(result["yt_dlp"])
        self.assertEqual(result["yt_dlp_version"], "2026.07.04")


class DownloadTests(unittest.TestCase):
    """TC-005 两段进度 / TC-010 完整性校验（下载部分）。"""

    def test_parse_progress_line_download_and_merge_phases(self) -> None:
        events = [event for line in (
            "[download]  12.3% of 4.17MiB at 1.23MiB/s ETA 00:03",
            "[download] 100% of 4.17MiB in 00:00",
            "[Merger] Merging formats into \"video.mp4\"",
        ) if (event := parse_progress_line(line))]
        self.assertEqual([event["phase"] for event in events], ["download", "download", "merge"])
        self.assertAlmostEqual(events[0]["percent"], 12.3)
        self.assertEqual(events[0]["total_bytes"], int(4.17 * 1024 ** 2))
        self.assertEqual(events[0]["speed_bytes"], int(1.23 * 1024 ** 2))
        self.assertEqual(events[2]["phase"], "merge")

    def test_parse_progress_line_ignores_noise(self) -> None:
        self.assertIsNone(parse_progress_line(""))
        self.assertIsNone(parse_progress_line("[info] some log"))

    def test_download_ok_with_progress_and_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "source" / "测试视频"
            target.parent.mkdir(parents=True)
            runner = FakeYtDlpRunner(progress_lines=[
                "[download]  50.0% of 2.00MiB at 1.00MiB/s ETA 00:01",
                "[download] 100% of 2.00MiB in 00:01",
                "[Merger] Merging formats into \"video.mp4\"",
            ])
            probe = lambda _path: {"format": {"duration": "616.0"}}
            adapter = YtDlpSourceAdapter(tool="yt-dlp", runner=runner, probe=probe)
            events: list[dict] = []
            # acquire 完成后由 resolve_downloaded_file 定位实际文件
            (target.parent / "测试视频.mp4").write_bytes(b"video-bytes")
            result = adapter.acquire(
                {"url": "https://www.bilibili.com/video/BV1cmTu6mEL3",
                 "video_id": "BV1cmTu6mEL3", "title": "测试视频",
                 "duration_seconds": 616.0, "extractor": "BiliBili"},
                target,
                progress=lambda event: events.append(dict(event)),
            )
            self.assertEqual([event["phase"] for event in events], ["download", "download", "merge"])
            self.assertEqual(Path(result["path"]).name, "测试视频.mp4")
            self.assertEqual(result["duration_seconds"], 616.0)
            self.assertGreater(result["size_bytes"], 0)
            self.assertIn("--continue", runner.download_calls[0])
            self.assertIn("--merge-output-format", runner.download_calls[0])
            self.assertIn("-f", runner.download_calls[0])

    def test_download_incomplete_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "source" / "视频"
            target.parent.mkdir(parents=True)
            runner = FakeYtDlpRunner()
            probe = lambda _path: {"format": {"duration": "300.0"}}  # 预检 616s，实际 300s
            adapter = YtDlpSourceAdapter(tool="yt-dlp", runner=runner, probe=probe)
            (target.parent / "视频.mp4").write_bytes(b"video-bytes")
            with self.assertRaises(SourceError) as ctx:
                adapter.acquire(
                    {"url": "https://example.com/video", "video_id": "v1", "title": "视频",
                     "duration_seconds": 616.0, "extractor": ""},
                    target,
                )
            self.assertEqual(ctx.exception.code, DOWNLOAD_INCOMPLETE)

    def test_download_no_file_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "source" / "视频"
            runner = FakeYtDlpRunner()
            adapter = YtDlpSourceAdapter(tool="yt-dlp", runner=runner, probe=lambda _p: {"format": {}})
            with self.assertRaises(SourceError) as ctx:
                adapter.acquire(
                    {"url": "https://example.com/video", "video_id": "v1", "title": "视频",
                     "duration_seconds": 1.0, "extractor": ""},
                    target,
                )
            self.assertEqual(ctx.exception.code, DOWNLOAD_FAILED)

    def test_download_failure_signal_maps_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "source" / "视频"
            runner = FakeYtDlpRunner(stderr="ERROR: HTTP Error 404: Not Found")
            adapter = YtDlpSourceAdapter(tool="yt-dlp", runner=runner, probe=lambda _p: {"format": {}})
            with self.assertRaises(SourceError) as ctx:
                adapter.acquire(
                    {"url": "https://example.com/video", "video_id": "v1", "title": "视频",
                     "duration_seconds": 1.0, "extractor": ""},
                    target,
                )
            self.assertEqual(ctx.exception.code, SOURCE_UNAVAILABLE)

    def test_duration_check_threshold(self) -> None:
        self.assertTrue(_format_duration_check(616.0, 616.0))
        self.assertTrue(_format_duration_check(616.0, 619.0))   # 3s 容差
        self.assertTrue(_format_duration_check(100.0, 101.5))    # 2% 容差
        self.assertFalse(_format_duration_check(616.0, 300.0))
        self.assertTrue(_format_duration_check(0.0, 120.0))      # 无预期时长不判失败

    def test_resolve_downloaded_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "视频"
            self.assertIsNone(resolve_downloaded_file(target))
            target.with_suffix(".mp4").write_bytes(b"data")
            self.assertEqual(resolve_downloaded_file(target).name, "视频.mp4")
            target.write_bytes(b"direct")
            self.assertEqual(resolve_downloaded_file(target).name, "视频")


if __name__ == "__main__":
    unittest.main()
