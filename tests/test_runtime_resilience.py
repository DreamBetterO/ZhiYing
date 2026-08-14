from __future__ import annotations

import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from video_study.application.requests import AggregateRequest, CloudAuthorization, ProcessingHandle, ProcessingResult
from video_study.asr import asr_runtime_limit_seconds
from video_study.desktop.controller import DesktopController
from video_study.desktop.models import DesktopState, UiEvent
from video_study.desktop.view import PRIMARY_UI_ACTIONS, drain_ui_events
from video_study.execution.artifacts import WorkspaceLayout
from video_study.execution.events import RunEventJournal
from video_study.providers import FallbackChatClient


def _result(root: Path) -> ProcessingResult:
    return ProcessingResult("id", root / "manifest.json", root / "a.md", root / "a.docx", root / "a.pdf")


class _LocalAggregateService:
    def cached_result(self, _video):
        return None

    def process(self, _request):
        raise AssertionError("本测试不应启动视频处理")

    def aggregate(self, _request):
        raise AssertionError("本测试不应启动云端聚合")

    def local_aggregate(self, _results, *, cancel_check=None):
        return _result(Path("."))

    def delete_video_workspace(self, _video):
        return None

    def clear_workspace(self):
        return 0


class _CancellableAggregateService(_LocalAggregateService):
    def aggregate(self, request):
        deadline = time.monotonic() + 1.0
        while not request.cancel_check() and time.monotonic() < deadline:
            time.sleep(0.005)
        raise RuntimeError("cancelled")


class RuntimeResilienceTests(unittest.TestCase):
    def test_ui_event_drain_is_bounded_per_tick(self) -> None:
        events: queue.Queue[UiEvent] = queue.Queue()
        for index in range(205):
            events.put(UiEvent("progress", DesktopState.RUNNING, message=str(index)))

        drained = drain_ui_events(events, limit=200)

        self.assertEqual(len(drained), 200)
        self.assertEqual(events.qsize(), 5)

    def test_processing_handle_isolates_faulty_ui_subscriber(self) -> None:
        handle = ProcessingHandle()
        received: list[dict] = []
        handle.subscribe(lambda _event: (_ for _ in ()).throw(RuntimeError("ui failed")))
        handle.subscribe(received.append)

        handle.publish({"type": "progress", "progress": 10})
        handle.finish(_result(Path(".")))

        self.assertEqual(received, [{"type": "progress", "progress": 10}])
        self.assertEqual(handle.wait(0.1).video_id, "id")

    def test_local_aggregate_is_mounted_and_runs_asynchronously(self) -> None:
        self.assertIn("local_aggregate", PRIMARY_UI_ACTIONS)
        controller = DesktopController(_LocalAggregateService())
        results = (_result(Path("one")), _result(Path("two")))

        controller.aggregate_local(results)
        deadline = time.monotonic() + 1.0
        while controller.state not in {DesktopState.COMPLETED, DesktopState.FAILED} and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(controller.state, DesktopState.COMPLETED)
        self.assertEqual(controller.aggregate_result["mode"], "")

    def test_aggregate_cancel_reaches_service_and_finishes_cancelled(self) -> None:
        controller = DesktopController(_CancellableAggregateService())
        request = AggregateRequest(
            (_result(Path("one")), _result(Path("two"))),
            CloudAuthorization(True, "secret", "https://example.com/v1", ("model",), max_calls=1),
        )
        controller.aggregate(request)
        deadline = time.monotonic() + 1.0
        while controller.state != DesktopState.RUNNING and time.monotonic() < deadline:
            time.sleep(0.005)
        controller.cancel()
        while controller.state != DesktopState.CANCELLED and time.monotonic() < deadline:
            time.sleep(0.005)

        self.assertEqual(controller.state, DesktopState.CANCELLED)

    def test_run_journal_live_summary_has_sequence_counts_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = RunEventJournal(
                WorkspaceLayout(Path(directory) / "workspace", "video-id"),
                "run-id",
            )
            journal.start({"work_type": "video_processing"})
            journal.publish({
                "type": "step_lifecycle",
                "step_id": "fixture.step",
                "status": "running",
                "code": "step_execution_started",
                "message": "开始执行",
            })
            journal.publish({
                "type": "step_state",
                "step_id": "fixture.step",
                "status": "succeeded",
                "code": "step_succeeded",
                "message": "执行完成",
                "duration_seconds": 1.25,
                "diagnostics": {"cache_reason": "NO_RECORD"},
            })
            live = json.loads(journal.summary_path.read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in journal.jsonl_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([row["sequence"] for row in rows], [1, 2, 3])
        self.assertEqual(live["status"], "running")
        self.assertEqual(live["event_types"]["step_lifecycle"], 1)
        self.assertEqual(live["steps"]["fixture.step"]["duration_seconds"], 1.25)
        self.assertEqual(live["steps"]["fixture.step"]["diagnostics"]["cache_reason"], "NO_RECORD")

    def test_asr_runtime_limit_is_finite_and_scales_with_video(self) -> None:
        self.assertEqual(asr_runtime_limit_seconds({"max_runtime_seconds": 12}, 3600), 12)
        self.assertGreaterEqual(asr_runtime_limit_seconds({}, 3600), 7200)
        self.assertLess(asr_runtime_limit_seconds({}, 3600), float("inf"))

    def test_cloud_wait_has_explicit_outer_deadline(self) -> None:
        blocker = threading.Event()

        class _Completions:
            def create(self, **_kwargs):
                blocker.wait(1.0)
                return object()

        class _Client:
            def __init__(self, **_kwargs):
                self.chat = type("Chat", (), {"completions": _Completions()})()

            def close(self):
                blocker.set()

        with patch("video_study.providers.OpenAI", _Client):
            client = FallbackChatClient(
                api_key="test", base_url="https://example.com/v1", models=["model"], timeout=0.05,
            )
            with self.assertRaises(TimeoutError):
                client._create_response(cancel_check=lambda: False, model="model", messages=[])


if __name__ == "__main__":
    unittest.main()
