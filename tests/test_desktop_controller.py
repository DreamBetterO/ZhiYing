from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from zhiying.application.requests import JobHandle, JobResult, ProcessingHandle, ProcessingRequest, ProcessingResult
from zhiying.desktop.controller import DesktopController
from zhiying.desktop.models import DesktopState


def result(path: Path) -> ProcessingResult:
    return ProcessingResult("id", path / "m.json", path / "a.md", path / "a.docx", path / "a.pdf")


class FakeService:
    def __init__(self, cached=False, fail=False, hold=False, download=None, hold_on_call=None, history=None) -> None:
        self.cached = cached; self.fail = fail; self.hold = hold; self.deleted = []; self.cleared = 0
        self.download = download
        self.hold_on_call = hold_on_call  # 第 N 次 process 调用时挂起（用于观察 RUNNING 状态）
        self.process_calls = 0
        self.history = history

    def history_snapshot(self, video):
        return self.history

    def cached_result(self, video):
        return result(video.parent) if self.cached else None

    def process(self, request):
        handle = ProcessingHandle()
        if self.hold_on_call is not None and self.process_calls == self.hold_on_call:
            self.process_calls += 1
            return handle
        self.process_calls += 1
        if self.hold:
            return handle
        if self.fail: handle.fail(RuntimeError("boom"))
        else:
            base = request.video.parent if request.video is not None else Path(".")
            handle.finish(result(base))
        return handle

    def process_job(self, request):
        handle = JobHandle()
        values = []
        try:
            for index, source in enumerate(request.sources):
                handle.publish({"type": "job_video_started", "source_index": index})
                child = self.process(source)
                if not child.done:
                    return handle
                values.append(child.wait())
            handle.finish(JobResult(tuple(values)))
        except BaseException as exc:
            handle.fail(exc)
        return handle

    def download_url(self, url, *, progress=None, cancel_check=None):
        if self.download is not None:
            return self.download(url, progress=progress, cancel_check=cancel_check)
        raise NotImplementedError("测试未配置 download_url")

    def aggregate(self, request): return result(Path("."))
    def delete_video_workspace(self, video): self.deleted.append(video)
    def clear_workspace(self): self.cleared += 1; return 2


def make_request(item):
    return ProcessingRequest(item.path)


class DesktopControllerTests(unittest.TestCase):
    def wait_state(self, controller, expected):
        deadline = time.time() + 1
        while time.time() < deadline and controller.state != expected: time.sleep(.01)
        self.assertEqual(controller.state, expected)

    def test_normal_and_cached_completion_without_tk(self) -> None:
        for cached in (False, True):
            controller = DesktopController(FakeService(cached=cached))
            controller.add([Path("lesson.mp4")])
            controller.start(make_request)
            self.wait_state(controller, DesktopState.COMPLETED)
            self.assertEqual(controller.items[0].stage, "completed")

    def test_readding_removed_video_restores_and_announces_last_run(self) -> None:
        prior = result(Path("."))
        snapshot = {
            "run_id": "run-old",
            "status": "degraded",
            "started_at": "2026-08-25T10:00:00+08:00",
            "finished_at": "2026-08-25T10:02:30+08:00",
            "elapsed_seconds": 150.0,
            "cloud_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "result": prior.to_legacy(),
        }
        controller = DesktopController(FakeService(history=snapshot))
        video = Path("lesson.mp4")
        controller.add([video])
        controller.remove_selected()
        controller.add([video])

        item = controller.items[0]
        self.assertEqual(item.stage, "history")
        self.assertEqual(item.status, "历史：已完成（降级）")
        self.assertEqual(item.elapsed, 150.0)
        self.assertEqual(item.result["cloud_usage"]["total_tokens"], 150)
        self.assertEqual(item.history["run_id"], "run-old")
        events = list(controller.events.queue)
        self.assertGreaterEqual(sum(event.kind == "history_restored" for event in events), 2)

    def test_degraded_completion_keeps_outputs_and_exposes_reason(self) -> None:
        degraded = ProcessingResult(
            "id", Path("m.json"), Path("a.md"), Path("a.docx"), Path("a.pdf"),
            mode="cloud_structured", diagnostics={
                "status": "degraded", "editorial_mode": "cloud_structured",
                "degradation_summary": ["Writer 超时，章节使用本地确定性结果"],
            },
        )
        controller = DesktopController(FakeService())
        controller.add([Path("lesson.mp4")])
        item = controller.items[0]
        controller._complete_item(item, degraded)
        self.assertEqual(item.status, "已完成（降级）")
        self.assertIn("Writer 超时", item.detail)
        self.assertEqual(item.result["markdown"], Path("a.md"))
        self.assertEqual(item.result["docx"], Path("a.docx"))
        self.assertEqual(item.result["pdf"], Path("a.pdf"))

    def test_completion_refreshes_last_run_metadata(self) -> None:
        completed = ProcessingResult(
            "id", Path("m.json"), Path("a.md"), Path("a.docx"), Path("a.pdf"),
            cloud_usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            diagnostics={
                "status": "succeeded",
                "runtime_events": [
                    {"type": "run_started", "run_id": "run-new", "timestamp": "2026-08-25T11:00:00+08:00"},
                    {"type": "run_finished", "run_id": "run-new", "timestamp": "2026-08-25T11:03:00+08:00"},
                ],
            },
        )
        controller = DesktopController(FakeService())
        controller.add([Path("lesson.mp4")])
        item = controller.items[0]
        controller._complete_item(item, completed)

        self.assertEqual(item.history["run_id"], "run-new")
        self.assertEqual(item.history["finished_at"], "2026-08-25T11:03:00+08:00")
        self.assertEqual(item.history["cloud_usage"]["total_tokens"], 150)


    def test_failure_cancel_and_repeated_cancel(self) -> None:
        failed = DesktopController(FakeService(fail=True)); failed.add([Path("bad.mp4")])
        failed.start(make_request); self.wait_state(failed, DesktopState.FAILED)
        held = DesktopController(FakeService(hold=True)); held.add([Path("hold.mp4")])
        held.start(make_request)
        deadline = time.time() + 1
        while time.time() < deadline and held.state != DesktopState.RUNNING: time.sleep(.01)
        held.cancel(); held.cancel()
        self.assertEqual(held.state, DesktopState.CANCELLING)

    def test_cleanup_commands(self) -> None:
        service = FakeService(history={"run_id": "old", "result": {}})
        controller = DesktopController(service); controller.add([Path("a.mp4")])
        controller.clear_selected_cache(); self.assertEqual(len(service.deleted), 1)
        self.assertEqual(controller.items[0].history, {})
        controller.items[0].history = {"run_id": "another"}
        self.assertEqual(controller.clear_workspace(), 2)
        self.assertEqual(controller.items[0].history, {})

    def test_toggle_all_is_controller_command(self) -> None:
        controller = DesktopController(FakeService()); controller.add([Path("a.mp4"), Path("b.mp4")])
        controller.toggle_all(); self.assertFalse(any(item.checked for item in controller.items))
        controller.toggle_all(); self.assertTrue(all(item.checked for item in controller.items))

    def test_reorder_moves_single_item_deterministically(self) -> None:
        controller = DesktopController(FakeService())
        paths = [Path(name) for name in ("a.mp4", "b.mp4", "c.mp4", "d.mp4")]
        controller.add(paths)
        # Move item at index 1 to index 3
        self.assertTrue(controller.reorder(1, 3))
        self.assertEqual([item.path.name for item in controller.items], ["a.mp4", "c.mp4", "d.mp4", "b.mp4"])
        # Move item at index 3 back to index 0
        self.assertTrue(controller.reorder(3, 0))
        self.assertEqual([item.path.name for item in controller.items], ["b.mp4", "a.mp4", "c.mp4", "d.mp4"])
        # Same index is a no-op
        self.assertFalse(controller.reorder(0, 0))
        self.assertTrue(controller.aggregate_result == {})

    def test_reorder_clears_aggregate_result(self) -> None:
        controller = DesktopController(FakeService())
        controller.add([Path("a.mp4"), Path("b.mp4")])
        controller.aggregate_result = {"some": "result"}
        controller.reorder(0, 1)
        self.assertEqual(controller.aggregate_result, {})

    def test_reorder_is_rejected_while_running(self) -> None:
        controller = DesktopController(FakeService(hold=True))
        controller.add([Path("a.mp4"), Path("b.mp4")])
        controller.start(make_request)
        deadline = time.time() + 1
        while time.time() < deadline and controller.state != DesktopState.RUNNING: time.sleep(.01)
        with self.assertRaisesRegex(RuntimeError, "处理中不能调整视频顺序"):
            controller.reorder(0, 1)

    def test_aggregate_is_async_command(self) -> None:
        from zhiying.application.requests import AggregateRequest, CloudAuthorization
        controller = DesktopController(FakeService())
        request = AggregateRequest((result(Path(".")), result(Path("."))), CloudAuthorization(True, "x", "https://example.com", ("m",), max_calls=1))
        controller.aggregate(request)
        self.wait_state(controller, DesktopState.COMPLETED)
        self.assertEqual(controller.aggregate_result["video_id"], "id")

    def test_v4_task_progress_tracks_eta_internally_without_ui_text(self) -> None:
        controller = DesktopController(FakeService())
        controller.add([Path("lesson.mp4")])
        item = controller.items[0]
        item.progress = 30

        controller._on_runtime(item, {
            "type": "task_progress",
            "event": {
                "stage": "visual",
                "completed": 1,
                "total": 4,
                "eta_seconds": 12.5,
            },
        })

        self.assertEqual(item.stage, "visual")
        self.assertEqual(item.progress, 30)
        self.assertEqual(item.eta, 12.5)
        self.assertNotIn("剩余", item.detail)
        self.assertNotIn("估算中", item.detail)
        self.assertGreaterEqual(item.elapsed, 0)

    def test_stage_progress_is_smoothed_for_large_early_jump(self) -> None:
        controller = DesktopController(FakeService())
        controller.add([Path("lesson.mp4")])
        item = controller.items[0]

        controller._on_runtime(item, {
            "type": "progress",
            "stage": "frames",
            "message": "正在提取候选画面",
            "progress": 55,
        })

        self.assertEqual(item.progress, 3)

    def test_stage_progress_is_monotonic(self) -> None:
        controller = DesktopController(FakeService())
        controller.add([Path("lesson.mp4")])
        item = controller.items[0]
        item.progress = 50

        controller._on_runtime(item, {
            "type": "progress",
            "stage": "asr",
            "message": "正在执行本地语音识别",
            "progress": 40,
        })

        self.assertEqual(item.progress, 50)

    def test_add_url_downloads_then_ready_and_emits_source_ready(self) -> None:
        from zhiying.desktop.models import UiEvent

        def download(url, *, progress=None, cancel_check=None):
            progress({"phase": "download", "percent": 100, "total_bytes": 10, "speed_bytes": None})
            return {"path": "C:/downloaded/测试视频.mp4", "title": "测试视频", "url": url, "video_id": "BV1cmTu6mEL3", "cached": False}

        controller = DesktopController(FakeService(download=download))
        controller.add_url("https://www.bilibili.com/video/BV1cmTu6mEL3")
        deadline = time.time() + 1
        while time.time() < deadline and controller.items[0].status != "已就绪": time.sleep(.01)

        item = controller.items[0]
        self.assertEqual(item.stage, "ready")
        self.assertEqual(item.progress, 100)
        self.assertEqual(item.detail_title, "测试视频")
        kinds = [event.kind for event in list(controller.events.queue)]
        self.assertIn("source_ready", kinds)

    def test_add_url_cached_reuses_without_download_message(self) -> None:
        def download(url, *, progress=None, cancel_check=None):
            return {"path": "C:/cached/测试视频.mp4", "title": "测试视频", "url": url, "video_id": "BV1cmTu6mEL3", "cached": True}

        controller = DesktopController(FakeService(download=download))
        controller.add_url("https://www.bilibili.com/video/BV1cmTu6mEL3")
        deadline = time.time() + 1
        while time.time() < deadline and controller.items[0].status != "已就绪": time.sleep(.01)
        events = list(controller.events.queue)
        ready = next(event for event in events if event.kind == "source_ready")
        self.assertIn("已复用本地缓存", ready.message)

    def test_add_url_failure_marks_failed(self) -> None:
        def download(url, *, progress=None, cancel_check=None):
            raise RuntimeError("[DOWNLOAD_INCOMPLETE] 下载文件不完整")

        controller = DesktopController(FakeService(download=download))
        controller.add_url("https://www.bilibili.com/video/BV1cmTu6mEL3")
        deadline = time.time() + 1
        while time.time() < deadline and controller.items[0].status != "失败": time.sleep(.01)

        item = controller.items[0]
        self.assertEqual(item.stage, "failed")
        self.assertIn("下载文件不完整", item.message)

    def test_add_url_cache_is_resolved_by_graph_not_controller(self) -> None:
        """链接源下载缓存只标记来源就绪，处理缓存由后续 Graph 节点判断。"""
        def download(url, *, progress=None, cancel_check=None):
            return {"path": "C:/cached/测试视频.mp4", "title": "测试视频", "url": url, "video_id": "BV1cmTu6mEL3", "cached": True}

        controller = DesktopController(FakeService(cached=True, download=download))
        controller.add_url("https://www.bilibili.com/video/BV1cmTu6mEL3")
        deadline = time.time() + 1
        while time.time() < deadline and controller.items[0].status != "已就绪": time.sleep(.01)

        item = controller.items[0]
        self.assertEqual(item.stage, "ready")
        self.assertEqual(item.progress, 100)
        self.assertFalse(item.result)
        events = list(controller.events.queue)
        ready = next(event for event in events if event.kind == "source_ready")
        self.assertIn("已复用本地缓存", ready.message)

    def test_add_url_duplicate_rejected(self) -> None:
        from zhiying.desktop.models import QueueItem
        controller = DesktopController(FakeService())
        controller.items.append(QueueItem(source_kind="url", source_url="https://x.example/v"))
        with self.assertRaises(ValueError):
            controller.add_url("https://x.example/v")

    def test_url_item_ready_then_processing_uses_url_request(self) -> None:
        from zhiying.desktop.models import QueueItem

        captured = {}

        def download(url, *, progress=None, cancel_check=None):
            return {"path": "C:/downloaded/测试视频.mp4", "title": "测试视频", "url": url, "video_id": "BV1cmTu6mEL3", "cached": False}

        service = FakeService(download=download)
        controller = DesktopController(service)
        controller.add_url("https://www.bilibili.com/video/BV1cmTu6mEL3")
        deadline = time.time() + 1
        while time.time() < deadline and controller.items[0].status != "已就绪": time.sleep(.01)
        item = controller.items[0]
        item.checked = True

        def make_url_request(item):
            captured["request"] = ProcessingRequest(
                url=item.source_url, content_level="推荐",
            ) if item.source_kind == "url" else ProcessingRequest(item.path)
            return captured["request"]

        controller.start(make_url_request)
        self.wait_state(controller, DesktopState.COMPLETED)
        self.assertEqual(captured["request"].url, "https://www.bilibili.com/video/BV1cmTu6mEL3")
        self.assertIsNone(captured["request"].video)

    def test_ready_url_item_restarts_progress_when_processing_begins(self) -> None:
        """下载完成（已就绪/100%）后开始整理，进度应重新从 0 开始。"""
        def download(url, *, progress=None, cancel_check=None):
            return {"path": "C:/downloaded/测试视频.mp4", "title": "测试视频", "url": url, "video_id": "BV1cmTu6mEL3", "cached": False}

        controller = DesktopController(FakeService(download=download, hold=True))
        controller.add_url("https://www.bilibili.com/video/BV1cmTu6mEL3")
        deadline = time.time() + 1
        while time.time() < deadline and controller.items[0].status != "已就绪": time.sleep(.01)
        item = controller.items[0]
        self.assertEqual(item.progress, 100)          # 下载阶段结束
        item.checked = True

        controller.start(make_request)
        deadline = time.time() + 1
        while time.time() < deadline and controller.state != DesktopState.RUNNING: time.sleep(.01)

        self.assertEqual(item.progress, 0)            # 新阶段开始，进度重新开始
        self.assertEqual(item.stage, "queued")
        controller.cancel()

    def test_completed_item_restarts_progress_on_cloud_refine(self) -> None:
        """本地整理完成（100%）后再点云端优化（同一 controller），进度应重新从 0 开始。"""
        service = FakeService(hold_on_call=1)  # 第 2 次 start 挂起，便于观察 RUNNING
        controller = DesktopController(service)
        controller.add([Path("lesson.mp4")])
        controller.start(make_request)
        self.wait_state(controller, DesktopState.COMPLETED)
        item = controller.items[0]
        self.assertEqual(item.progress, 100)          # 本地整理完成
        item.checked = True

        controller.start(make_request)                # 云端优化：同一 controller 再次 start
        deadline = time.time() + 1
        while time.time() < deadline and controller.state != DesktopState.RUNNING: time.sleep(.01)

        self.assertEqual(item.progress, 0)            # 云端精炼新阶段，进度重新开始
        controller.cancel()


if __name__ == "__main__":
    unittest.main()
