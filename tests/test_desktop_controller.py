from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from video_study.application.requests import ProcessingHandle, ProcessingRequest, ProcessingResult
from video_study.desktop.controller import DesktopController
from video_study.desktop.models import DesktopState


def result(path: Path) -> ProcessingResult:
    return ProcessingResult("id", path / "m.json", path / "a.md", path / "a.docx", path / "a.pdf")


class FakeService:
    def __init__(self, cached=False, fail=False, hold=False) -> None:
        self.cached = cached; self.fail = fail; self.hold = hold; self.deleted = []; self.cleared = 0

    def cached_result(self, video):
        return result(video.parent) if self.cached else None

    def process(self, request):
        handle = ProcessingHandle()
        if self.hold:
            return handle
        if self.fail: handle.fail(RuntimeError("boom"))
        else: handle.finish(result(request.video.parent))
        return handle

    def aggregate(self, request): return result(Path("."))
    def delete_video_workspace(self, video): self.deleted.append(video)
    def clear_workspace(self): self.cleared += 1; return 2


class DesktopControllerTests(unittest.TestCase):
    def wait_state(self, controller, expected):
        deadline = time.time() + 1
        while time.time() < deadline and controller.state != expected: time.sleep(.01)
        self.assertEqual(controller.state, expected)

    def test_normal_and_cached_completion_without_tk(self) -> None:
        for cached in (False, True):
            controller = DesktopController(FakeService(cached=cached))
            controller.add([Path("lesson.mp4")])
            controller.start(lambda path: ProcessingRequest(path))
            self.wait_state(controller, DesktopState.COMPLETED)
            self.assertEqual(controller.items[0].stage, "completed")

    def test_failure_cancel_and_repeated_cancel(self) -> None:
        failed = DesktopController(FakeService(fail=True)); failed.add([Path("bad.mp4")])
        failed.start(lambda path: ProcessingRequest(path)); self.wait_state(failed, DesktopState.FAILED)
        held = DesktopController(FakeService(hold=True)); held.add([Path("hold.mp4")])
        held.start(lambda path: ProcessingRequest(path))
        deadline = time.time() + 1
        while time.time() < deadline and held.state != DesktopState.RUNNING: time.sleep(.01)
        held.cancel(); held.cancel()
        self.assertEqual(held.state, DesktopState.CANCELLING)

    def test_cleanup_commands(self) -> None:
        service = FakeService(); controller = DesktopController(service); controller.add([Path("a.mp4")])
        controller.delete_selected(); self.assertEqual(len(service.deleted), 1)
        self.assertEqual(controller.clear_workspace(), 2)

    def test_toggle_all_is_controller_command(self) -> None:
        controller = DesktopController(FakeService()); controller.add([Path("a.mp4"), Path("b.mp4")])
        controller.toggle_all(); self.assertFalse(any(item.checked for item in controller.items))
        controller.toggle_all(); self.assertTrue(all(item.checked for item in controller.items))

    def test_selected_videos_move_as_stable_block_and_define_processing_order(self) -> None:
        controller = DesktopController(FakeService())
        paths = [Path(name) for name in ("a.mp4", "b.mp4", "c.mp4", "d.mp4")]
        controller.add(paths)
        for path in paths:
            controller.select(path, False)
        controller.select(paths[1], True)
        controller.select(paths[2], True)

        self.assertTrue(controller.move_selected_up())
        self.assertEqual([item.path.name for item in controller.items], ["b.mp4", "c.mp4", "a.mp4", "d.mp4"])
        self.assertFalse(controller.move_selected_up())
        self.assertTrue(controller.move_selected_down())
        self.assertEqual([item.path.name for item in controller.items], ["a.mp4", "b.mp4", "c.mp4", "d.mp4"])

    def test_reordering_is_rejected_while_running(self) -> None:
        controller = DesktopController(FakeService(hold=True))
        controller.add([Path("a.mp4"), Path("b.mp4")])
        controller.start(lambda path: ProcessingRequest(path))
        deadline = time.time() + 1
        while time.time() < deadline and controller.state != DesktopState.RUNNING: time.sleep(.01)
        with self.assertRaisesRegex(RuntimeError, "处理中不能调整视频顺序"):
            controller.move_selected_up()

    def test_aggregate_is_async_command(self) -> None:
        from video_study.application.requests import AggregateRequest, CloudAuthorization
        controller = DesktopController(FakeService())
        request = AggregateRequest((result(Path(".")), result(Path("."))), CloudAuthorization(True, "x", "https://example.com", ("m",), max_calls=1))
        controller.aggregate(request)
        self.wait_state(controller, DesktopState.COMPLETED)
        self.assertEqual(controller.aggregate_result["video_id"], "id")

    def test_v4_task_progress_updates_eta_and_elapsed_fields(self) -> None:
        controller = DesktopController(FakeService())
        controller.add([Path("lesson.mp4")])
        item = controller.items[0]

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
        self.assertEqual(item.eta, 12.5)
        self.assertGreaterEqual(item.elapsed, 0)


if __name__ == "__main__":
    unittest.main()
